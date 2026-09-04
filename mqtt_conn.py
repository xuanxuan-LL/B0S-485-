#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mqtt_conn.py —— MQTT 云通道（手机 ⇄ 云端 Broker ⇄ TAS-KS-301 ⇄ RS485 ⇄ 协议转换板）
================================================================================
接口与 protocol_core 的 TcpConn / usb_conn.UsbConn 完全一致：
    open / close / is_open / write / read_frame
所以上层 UI(main.py) 不需要区分走的是网口、USB 还是 MQTT 云。

链路：
    手机 App --publish--> [下行主题] --订阅--> TAS-KS-301 --> RS485 --> 协议板
    手机 App <--订阅-- [上行主题] <--发布-- TAS-KS-301 <-- RS485 -- 协议板

为什么 read_frame 要"按长度定界"而不是"按时间定界"：
    TAS-KS-301 把串口数据转 MQTT 时，是按 §6.1.2 的「分包时间 + 打包长度」组包的，
    它并不认识 Modbus 帧边界 —— 一帧可能被拆成多条 MQTT 消息(拆包)，
    也可能两帧被合并进一条(粘包)。
    好在 MCTC-KZ-B0S 是 Modbus-RTU，对我们发的固定请求，响应长度是确定的：
        0x03 读 N 个寄存器 -> 3 + 2N + 2 = 5 + 2N 字节
        0x06 写单寄存器    -> 回显 8 字节
        异常响应           -> 5 字节(功能码最高位置 1)
    所以这里直接"攒够预期长度 + CRC 校验"来定界，拆包粘包一并解决；
    CRC 不通过就丢弃首字节重新同步，顺带把 DTU 的业务心跳包/注册包滤掉。

TAS-KS-301 侧推荐配置（AT 指令，详见说明书 §7.5）：
    AT+IPPORT="<broker地址>",1883,1      服务器地址与端口
    AT+CLIENTID="<唯一ID>",1             客户端 ID（多设备不可重复）
    AT+USERPWD="<账号>","<密码>",1        Broker 认证
    AT+MQTTSUB=1,"<下行主题>",0,1,1      订阅下行指令主题(qos1)
    AT+MQTTPUB=1,"<上行主题>",0,0,1,1    发布上行状态主题
    AT+MQTTKEEP=60,1                     MQTT 协议心跳(秒)
    另：建议关闭业务心跳包/注册包(AT+KEEPALIVE / AT+DTUID)，
        否则它们也会混进上行主题，虽能被本模块 CRC 过滤，但徒增流量。

依赖: pip install paho-mqtt
"""
import threading
import time

try:
    import paho.mqtt.client as mqtt
    MQTT_OK = True
    MQTT_ERR = ""
except Exception as _e:          # 未安装 / 未打包进 APK 时降级
    mqtt = None
    MQTT_OK = False
    MQTT_ERR = "%s: %s" % (type(_e).__name__, _e)

from protocol_core import BaseConn, crc16_modbus

__all__ = ["MqttConn", "MQTT_OK", "MQTT_ERR", "expected_resp_len"]

# CONNACK 返回码说明（兼容 paho 1.x / 2.x）
_RC_TEXT = {
    0: "连接成功",
    1: "协议版本不支持",
    2: "客户端 ID 被拒绝",
    3: "服务器不可用",
    4: "账号或密码错误",
    5: "未授权",
}


def _new_client(client_id):
    """兼容 paho-mqtt 1.x / 2.x：统一采用 VERSION1 的回调签名。"""
    if not MQTT_OK:
        raise RuntimeError("未安装 paho-mqtt（%s）" % MQTT_ERR)
    try:                                    # paho 2.x
        return mqtt.Client(mqtt.CallbackAPIVersion.VERSION1,
                           client_id=client_id)
    except AttributeError:                  # paho 1.x
        return mqtt.Client(client_id=client_id)


def expected_resp_len(req: bytes) -> int:
    """按 Modbus-RTU 请求推算响应长度；推算不出来时返回 0。"""
    if not req or len(req) < 2:
        return 0
    fc = req[1]
    if fc == 0x03 and len(req) >= 6:        # 读保持寄存器
        cnt = (req[4] << 8) | req[5]
        return 5 + 2 * cnt
    if fc in (0x06, 0x10):                  # 写单/写多寄存器 -> 8 字节回显
        return 8
    return 0


class MqttConn(BaseConn):
    """MQTT 云通道。

    topic_down: App -> DTU 的下行主题（DTU 侧用 AT+MQTTSUB 订阅）
    topic_up:   DTU -> App 的上行主题（DTU 侧用 AT+MQTTPUB 发布）
    """

    def __init__(self, host, port=1883, username="", password="",
                 client_id="", topic_down="", topic_up="",
                 keepalive=60, qos=1, conn_timeout=8.0, io_timeout=3.0):
        self.host = (host or "").strip()
        self.port = int(port)
        self.username = (username or "").strip()
        self.password = password or ""
        self.client_id = (client_id or "").strip() or self._default_cid()
        self.topic_down = (topic_down or "").strip()
        self.topic_up = (topic_up or "").strip()
        self.keepalive = int(keepalive)
        self.qos = int(qos)
        self.conn_timeout = float(conn_timeout)
        self.io_timeout = float(io_timeout)

        self._cli = None
        self._buf = bytearray()
        self._rx = threading.Event()
        self._lock = threading.Lock()
        self._last_req = b""
        self._connected = False
        self._rc = None
        self._last_rx = 0.0
        self.last_error = ""

    @staticmethod
    def _default_cid():
        try:
            import uuid
            return "mctc-app-%s" % uuid.uuid4().hex[:12]
        except Exception:
            return "mctc-app-%d" % int(time.time())

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------
    def open(self):
        if not MQTT_OK:
            raise RuntimeError("未安装 paho-mqtt（%s）" % MQTT_ERR)
        if not self.host:
            raise RuntimeError("请填写 MQTT 服务器地址")
        if not (1 <= self.port <= 65535):
            raise RuntimeError("MQTT 端口范围 1-65535")
        if not self.topic_down or not self.topic_up:
            raise RuntimeError("请填写下行主题（App→DTU）与上行主题（DTU→App）")

        self._connected = False
        self._rc = None
        self._rx.clear()
        with self._lock:
            self._buf = bytearray()

        cli = _new_client(self.client_id)
        if self.username:
            cli.username_pw_set(self.username, self.password or None)
        cli.on_connect = self._on_connect
        cli.on_message = self._on_message
        cli.on_disconnect = self._on_disconnect
        try:        # 遗嘱：App 异常掉线时通知对端
            cli.will_set(self.topic_up, b"OFFLINE", qos=1, retain=False)
        except Exception:
            pass

        cli.connect(self.host, self.port, self.keepalive)
        cli.loop_start()

        deadline = time.time() + self.conn_timeout
        while time.time() < deadline and self._rc is None:
            time.sleep(0.05)

        if self._rc is None:
            cli.loop_stop()
            raise RuntimeError(
                "MQTT 连接超时（%.0f 秒）\n请检查：服务器地址/端口、手机能否上网、"
                "Broker 是否放行 1883 端口" % self.conn_timeout)
        if self._rc != 0:
            cli.loop_stop()
            raise RuntimeError("MQTT 连接被拒绝：%s（rc=%s）"
                               % (_RC_TEXT.get(self._rc, "未知"), self._rc))
        self._cli = cli

    def close(self):
        cli, self._cli = self._cli, None
        self._connected = False
        self._rx.set()          # 唤醒可能阻塞在 read_frame 的线程
        if cli is not None:
            try:
                cli.loop_stop()
            except Exception:
                pass
            try:
                cli.disconnect()
            except Exception:
                pass

    def is_open(self):
        return self._cli is not None and self._connected

    # ------------------------------------------------------------------
    # paho 回调（运行在 paho 网络线程）
    # ------------------------------------------------------------------
    def _on_connect(self, client, userdata, flags, rc):
        self._rc = rc
        if rc == 0:
            try:
                client.subscribe(self.topic_up, qos=self.qos)
            except Exception:
                pass
            self._connected = True
        else:
            self._connected = False
            self.last_error = _RC_TEXT.get(rc, "rc=%s" % rc)

    def _on_disconnect(self, client, userdata, rc):
        self._connected = False
        self._rx.set()

    def _on_message(self, client, userdata, msg):
        try:
            payload = msg.payload
            if isinstance(payload, str):
                payload = payload.encode("utf-8", "ignore")
            elif isinstance(payload, (bytearray, memoryview)):
                payload = bytes(payload)
        except Exception:
            return
        if not payload:
            return
        with self._lock:
            self._buf += payload
        self._last_rx = time.time()
        self._rx.set()

    # ------------------------------------------------------------------
    # 收发
    # ------------------------------------------------------------------
    @staticmethod
    def _crc_ok(frame: bytes) -> bool:
        if len(frame) < 5:
            return False
        return crc16_modbus(frame[:-2]) == (frame[-2] | (frame[-1] << 8))

    def write(self, data):
        if not self.is_open():
            raise RuntimeError("MQTT 未连接")
        self._last_req = bytes(data)
        with self._lock:
            self._buf = bytearray()      # 丢弃上一轮残留，避免错帧
        self._rx.clear()
        try:
            info = self._cli.publish(self.topic_down, bytes(data), qos=self.qos)
            info.wait_for_publish(timeout=2.0)
        except Exception as e:
            raise RuntimeError("MQTT 发布失败: %s" % e)

    def read_frame(self, timeout=None, inter=0.05):
        """阻塞读取一帧响应。

        优先按 Modbus-RTU 的确定性长度定界；CRC 不通过则丢弃首字节重新同步
        （DTU 的业务心跳包/注册包会在这步被滤掉）。
        """
        if timeout is None:
            timeout = self.io_timeout
        req = self._last_req
        exp = expected_resp_len(req) if req else 0
        deadline = time.time() + timeout

        while True:
            frame = None
            with self._lock:
                buf = self._buf
                # 1) 异常响应：功能码最高位置 1，固定 5 字节
                if (req and len(buf) >= 5 and buf[0] == req[0]
                        and buf[1] == (req[1] | 0x80)):
                    cand = bytes(buf[:5])
                    if self._crc_ok(cand):
                        frame, drop = cand, 5
                    else:
                        frame, drop = None, 1
                # 2) 已知长度：攒够即收
                elif exp and len(buf) >= exp:
                    cand = bytes(buf[:exp])
                    if self._crc_ok(cand):
                        frame, drop = cand, exp
                    else:
                        frame, drop = None, 1
                # 3) 长度未知：退化为字节间隔静默判定
                elif (not exp) and len(buf) >= 5 and \
                        (time.time() - self._last_rx) >= inter:
                    frame, drop = bytes(buf), len(buf)
                else:
                    drop = 0
                if drop:
                    del buf[:drop]
            if frame is not None:
                return frame

            remain = deadline - time.time()
            if remain <= 0:
                break
            self._rx.wait(min(0.03, remain))
            self._rx.clear()

        with self._lock:                 # 超时：把收到的残帧交上层判定
            out = bytes(self._buf)
            self._buf = bytearray()
        return out

    def reset_buffers(self):
        with self._lock:
            self._buf = bytearray()
        self._rx.clear()
