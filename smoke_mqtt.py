#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
smoke_mqtt.py —— mqtt_conn 传输层冒烟测试（无需真实 MQTT 服务器）
=================================================================
用一个假的 paho Client 模拟「TAS-KS-301 + 协议板」从机，重点验证
MqttConn.read_frame 的帧定界能否扛住 DTU 的拆包 / 粘包 / 心跳包污染。

运行：
    python smoke_mqtt.py
"""
import os
import sys
import threading
import time
import types

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

from protocol_core import crc16_modbus, crc_bytes, build_read, build_write, \
    REG_SYS, REG_DOOR_CTRL, OPEN_VAL

import mqtt_conn
from mqtt_conn import MqttConn, expected_resp_len

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("OK  " if cond else "FAIL", name,
                           ("  <- " + detail) if detail and not cond else ""))


# ---------------------------------------------------------------------------
# 假 paho：模拟 MQTT 服务器 + Modbus 从机
# ---------------------------------------------------------------------------
class FakeInfo(object):
    def wait_for_publish(self, timeout=None):
        return True


class FakeMsg(object):
    def __init__(self, topic, payload):
        self.topic = topic
        self.payload = payload


class FakeClient(object):
    """模拟 paho Client。mode 决定响应怎么被切分投递。"""

    def __init__(self, *a, **kw):
        self.mode = "single"
        self.junk = b""
        self.exc = None
        self.silent = False
        self.delay = 0.02
        self.on_connect = None
        self.on_message = None
        self.on_disconnect = None
        self.subscriptions = []

    # --- paho 接口 ---
    def username_pw_set(self, u, p=None):
        pass

    def will_set(self, topic, payload=None, qos=0, retain=False):
        pass

    def connect(self, host, port, keepalive):
        if self.on_connect:
            self.on_connect(self, None, None, 0)      # rc=0 连接成功

    def loop_start(self):
        pass

    def loop_stop(self):
        pass

    def disconnect(self):
        if self.on_disconnect:
            self.on_disconnect(self, None, 0)

    def subscribe(self, topic, qos=0):
        self.subscriptions.append(topic)

    def publish(self, topic, payload, qos=0):
        if not self.silent:
            resp = self._make_response(bytes(payload))
            t = threading.Thread(target=self._deliver, args=(resp,),
                                 daemon=True)
            t.start()
        return FakeInfo()

    # --- 从机模拟 ---
    def _make_response(self, req):
        if self.exc is not None:
            return bytes([req[0], req[1] | 0x80, self.exc]) + \
                crc_bytes(bytes([req[0], req[1] | 0x80, self.exc]))
        if len(req) >= 2 and req[1] == 0x06:
            return req                                 # 写指令回显
        if len(req) >= 6 and req[1] == 0x03:
            cnt = (req[4] << 8) | req[5]
            body = bytes([req[0], 0x03, cnt * 2])
            for i in range(cnt):
                body += bytes([0x00, 0x03 + i])        # 假数据
            return body + crc_bytes(body)
        return b""

    def _deliver(self, resp):
        time.sleep(self.delay)
        stream = self.junk + resp
        if self.mode == "single":
            chunks = [stream]
        elif self.mode == "split":                     # 拆包：逐字节/小片投递
            chunks = [stream[i:i + 3] for i in range(0, len(stream), 3)]
        elif self.mode == "byte":                      # 极限拆包：一字节一包
            chunks = [stream[i:i + 1] for i in range(len(stream))]
        elif self.mode == "merged":                    # 粘包：整条一次 + 尾部多余
            chunks = [stream]
        else:
            chunks = [stream]
        for c in chunks:
            if c and self.on_message:
                self.on_message(self, None, FakeMsg("up", c))
                time.sleep(0.005)


def make_conn(mode="single", junk=b"", exc=None, silent=False, io_timeout=2.0):
    """构造一个走假 paho 的 MqttConn。"""
    fake_mod = types.SimpleNamespace(
        Client=FakeClient,
        CallbackAPIVersion=types.SimpleNamespace(VERSION1=1))
    mqtt_conn.mqtt = fake_mod

    conn = MqttConn(host="broker.test", port=1883,
                    username="u", password="p",
                    topic_down="cmd", topic_up="stat",
                    conn_timeout=2.0, io_timeout=io_timeout)
    conn.open()
    conn._cli.mode = mode
    conn._cli.junk = junk
    conn._cli.exc = exc
    conn._cli.silent = silent
    return conn


def valid(frame, req):
    return (len(frame) >= 5
            and crc16_modbus(frame[:-2]) == (frame[-2] | (frame[-1] << 8))
            and frame[0] == req[0])


# ---------------------------------------------------------------------------
# 用例
# ---------------------------------------------------------------------------
def main():
    print("=== mqtt_conn 冒烟测试 ===\n")
    print("paho-mqtt 可用性: %s %s" % (mqtt_conn.MQTT_OK, mqtt_conn.MQTT_ERR))

    print("\n[1] 响应长度推算")
    req5 = build_read(1, REG_SYS, 5)
    check("读5寄存器 -> 15 字节", expected_resp_len(req5) == 15,
          "实际 %d" % expected_resp_len(req5))
    wr = build_write(1, REG_DOOR_CTRL, OPEN_VAL)
    check("写单寄存器 -> 8 字节", expected_resp_len(wr) == 8,
          "实际 %d" % expected_resp_len(wr))

    print("\n[2] 正常单包响应")
    c = make_conn("single")
    c.write(req5)
    r = c.read_frame(2.0)
    check("收到完整帧", valid(r, req5) and len(r) == 15, "len=%d" % len(r))
    c.close()

    print("\n[3] 拆包（每 3 字节一条 MQTT 消息）")
    c = make_conn("split")
    c.write(req5)
    r = c.read_frame(2.0)
    check("拆包能重组", valid(r, req5) and len(r) == 15, "len=%d" % len(r))
    c.close()

    print("\n[4] 极限拆包（每 1 字节一条消息）")
    c = make_conn("byte")
    c.write(req5)
    r = c.read_frame(2.0)
    check("逐字节也能重组", valid(r, req5) and len(r) == 15,
          "len=%d" % len(r))
    c.close()

    print("\n[5] 心跳包/注册包污染（帧前混入 6 字节垃圾）")
    c = make_conn("single", junk=b"HB\x01\x02\x03\x04")
    c.write(req5)
    r = c.read_frame(2.0)
    check("垃圾前缀被 CRC 重同步滤掉", valid(r, req5) and len(r) == 15,
          "len=%d" % len(r))
    c.close()

    print("\n[6] 异常响应（功能码 |0x80，5 字节）")
    c = make_conn("single", exc=0x02)
    c.write(req5)
    r = c.read_frame(2.0)
    check("异常帧正确定界", len(r) == 5 and r[1] == 0x83 and valid(r, req5),
          "len=%d fc=0x%02X" % (len(r), r[1] if len(r) > 1 else -1))
    c.close()

    print("\n[7] 写指令回显（8 字节）")
    c = make_conn("single")
    c.write(wr)
    r = c.read_frame(2.0)
    check("写回显 8 字节", valid(r, wr) and len(r) == 8, "len=%d" % len(r))
    c.close()

    print("\n[8] 无响应超时")
    c = make_conn("single", silent=True, io_timeout=0.5)
    c.write(req5)
    t0 = time.time()
    r = c.read_frame(0.5)
    dt = time.time() - t0
    check("超时返回空且不卡死", r == b"" and 0.4 <= dt <= 1.2,
          "len=%d dt=%.2fs" % (len(r), dt))
    c.close()

    print("\n[9] 连续多轮收发（缓冲区不串帧）")
    c = make_conn("split")
    ok = True
    for i in range(5):
        c.write(req5)
        r = c.read_frame(2.0)
        if not (valid(r, req5) and len(r) == 15):
            ok = False
            break
    check("连续 5 轮均正确", ok)
    c.close()

    print("\n=== 结果: %d 通过 / %d 失败 ===" % (len(PASS), len(FAIL)))
    if FAIL:
        for f in FAIL:
            print("  失败: %s" % f)
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
