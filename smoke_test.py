#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""安卓版(Kivy) 冒烟测试：在 Windows 上提前验证 UI 构建与交互逻辑。

不弹窗口也能跑（Kivy 用 'mock' 隐藏窗口 + 空输入提供者）。
覆盖: 字体注册 / 控件齐全 / 未连接禁用 / 间隔钳位 / 状态解析刷新 UI / AGV。
"""
import os, sys, time

# Kivy 2.3 已移除 mock 窗口提供者, 这里用真实 SDL2 窗口但不跑主循环,
# 窗口会一闪而过; 只验证"能不能构建出来"与交互逻辑, 不做视觉校验。
os.environ.setdefault("KIVY_LOG_LEVEL", "error")

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import main as M

# 拦截弹窗: 冒烟测试里不希望真的弹出 Popup, 改为记录
_alerts = []
M.MCTCApp.alert = lambda self, msg, title="提示": _alerts.append(msg)

npass = nfail = 0


def chk(name, cond, detail=""):
    global npass, nfail
    if cond:
        npass += 1
        print("  [OK]   %-30s %s" % (name, detail))
    else:
        nfail += 1
        print("  [FAIL] %-30s %s" % (name, detail))


def sec(t):
    print("\n" + "=" * 68 + "\n" + t + "\n" + "=" * 68)


sec("1) 中文字体注册")
print("  font_name=%s  path=%s" % (M.FONT, M.FONT_PATH))
chk("命中中文字体(CJK)", M.FONT == "CJK", M.FONT_PATH)

sec("2) 构建界面")
app = M.MCTCApp()
root = app.build()
chk("build() 返回根节点", root is not None)
chk("标题含 MCTC", "MCTC" in app.title, app.title)

need = ["sp_type", "ent_ip", "ent_port", "ent_addr", "sp_proto", "btn_conn",
        "lbl_conn", "card_sys", "card_run", "card_door", "card_carin",
        "lbl_floor", "btn_read", "sw_autoref", "ent_interval",
        "ent_timeout", "ent_retries",
        "sw_driver", "sw_cont", "sw_hb", "ent_hb", "lbl_agv",
        "log_label", "statusbar"]
miss = [n for n in need if not hasattr(app, n)]
chk("关键控件齐全 (%d 项)" % len(need), not miss, "缺失: %s" % miss if miss else "全部存在")

sec("3) 服务器预设与默认值")
chk("默认服务器类型", app.sp_type.text in M.SERVER_PRESETS or app.sp_type.text == "自定义",
    app.sp_type.text)
print("  IP=%s 端口=%s 站址=%s 协议=%s" % (
    app.ent_ip.text, app.ent_port.text, app.ent_addr.text, app.sp_proto.text))
chk("预设含 NA11x", any("NA11x" in k for k in M.SERVER_PRESETS))
chk("预设含 C2000", any("C2000" in k for k in M.SERVER_PRESETS))
app.sp_type.text = "NA11x (亿佰特)"
chk("选 NA11x 自动填 IP/端口",
    app.ent_ip.text == "192.168.3.7" and app.ent_port.text == "8887",
    "%s:%s" % (app.ent_ip.text, app.ent_port.text))
app.sp_type.text = "C2000 (康耐德)"
chk("选 C2000 自动填 IP/端口",
    app.ent_ip.text == "192.168.4.1" and app.ent_port.text == "8000",
    "%s:%s" % (app.ent_ip.text, app.ent_port.text))

sec("4) 未连接时动作控件禁用 / 连接后启用")
app._set_actions_enabled(False)
chk("未连接: 全部动作禁用",
    all(not w.disabled for w in app.action_widgets) is False and
    all(w.disabled for w in app.action_widgets),
    "共 %d 个控件" % len(app.action_widgets))
app._set_actions_enabled(True)
chk("已连接: 非控制动作启用",
    all(not w.disabled for w in app.action_widgets
        if w not in app.ctrl_widgets))
chk("已连接但未开使能: 控制动作仍禁用",
    all(w.disabled for w in app.ctrl_widgets))

sec("5) 间隔输入钳位（防手抖填崩）")
chk("刷新默认 3 秒", app._interval_of("3", 3, 0.5, 60) == 3)
chk("刷新下限 0.5s", app._interval_of("0", 3, 0.5, 60) == 0.5)
chk("刷新上限 60s", app._interval_of("9999", 3, 0.5, 60) == 60)
chk("非法字符回落默认", app._interval_of("abc", 3, 0.5, 60) == 3)
chk("心跳默认 30s", app._interval_of("30", 30, 1, 120) == 30)
chk("心跳上限 120s", app._interval_of("600", 30, 1, 120) == 120)
chk("心跳下限 1s", app._interval_of("0.1", 30, 1, 120) == 1)

sec("6) 状态解析结果写回界面")
import protocol_core as P
req = P.build_read(1, P.REG_SYS, 5)
body = bytes([0x03, 0x0A, 0x00, 0x03, 0x00, 0x02, 0x00, 0x04,
              0x00, 0x00, 0x00, 0x05])
resp = bytes([0x01]) + body + P.crc_bytes(bytes([0x01]) + body)
res = P.parse_status(req, resp)
app._update_status(res)
chk("系统卡片", "正常" in app.card_sys.val_lbl.text, app.card_sys.val_lbl.text)
chk("运行卡片", "下运行" in app.card_run.val_lbl.text, app.card_run.val_lbl.text)
chk("门卡片", "关门到位" in app.card_door.val_lbl.text, app.card_door.val_lbl.text)
chk("楼层大字(含方向箭头)", "5" in app.lbl_floor.text and "楼" in app.lbl_floor.text,
    app.lbl_floor.text)
chk("轿内开关卡片", "0x0000" in app.card_carin.val_lbl.text,
    app.card_carin.val_lbl.text)
chk("状态栏存在", isinstance(app.statusbar.text, str) and app.statusbar.text != "",
    app.statusbar.text)

sec("7) 协议模式切换")
app.sp_proto.text = "Modbus TCP 网关"
chk("识别为 MBAP 模式", app.use_tcp_mbap() is True)
app.sp_proto.text = "Modbus RTU 透传"
chk("识别为透传模式", app.use_tcp_mbap() is False)

sec("8) 参数校验")
def try_params(ip, port, addr="1"):
    _alerts.clear()
    app.ent_ip.text, app.ent_port.text, app.ent_addr.text = ip, port, addr
    ok = app.ensure_params()
    return ok, (_alerts[-1] if _alerts else "")

ok, msg = try_params("999.1.1.1", "8887")
chk("非法 IP 被拦下", ok is False, msg)
ok, msg = try_params("192.168.3.7", "abc")
chk("非法端口被拦下", ok is False, msg)
ok, msg = try_params("192.168.3.7", "70000")
chk("端口越界被拦下", ok is False, msg)
ok, msg = try_params("192.168.3.7", "8887", "0")
chk("站址 0 被拦下", ok is False, msg)
ok, msg = try_params("192.168.3.7", "8887", "1")
chk("合法参数通过", ok is True, msg)
ok, msg = try_params("192.168.4.1", "8000", "01")
chk("站址支持 01 写法", ok is True, msg)

sec("9) USB-OTG 直连模式")
import usb_conn as UC
chk("USB 模块可导入", UC is not None)
chk("Windows 下降级为不可用", UC.USB_OK is False, UC.USB_ERR)
# 切换到 USB 模式
app.sp_link.text = "USB OTG 直连"
app.on_link_change(app.sp_link, "USB OTG 直连")
chk("连接模式=usb", app.link == "usb", app.link)
chk("参数区出现 USB 扫描控件",
    hasattr(app, "sp_usb") and hasattr(app, "btn_scan"))
# USB 模式即使协议选 MBAP 也不封装 MBAP（设备侧无网关）
app.sp_proto.text = "Modbus TCP 网关"
chk("USB 模式强制 RTU(不封装 MBAP)", app.use_tcp_mbap() is False)
app.sp_proto.text = "Modbus RTU 透传"
# 无设备时 ensure_params 应被拦下
app.usb_devices = []
app.sp_usb.text = "（点扫描检测）"
_alerts.clear()
ok = app.ensure_params()
chk("USB 无设备被拦下", ok is False, _alerts[-1] if _alerts else "")
# 注入一个假设备后应通过
fake = {"name": "/dev/bus/usb/001/002", "vid": 0x1A86, "pid": 0x7523,
        "vendor": "QinHeng", "product": "CH340",
        "driver": "QinHeng (CH340/CH341)",
        "label": "1A86:7523 CH340 串口 [QinHeng (CH340/CH341)]",
        "is_485": True}
app.usb_devices = [fake]
app.sp_usb.text = fake["label"]
_alerts.clear()
ok = app.ensure_params()
chk("USB 选到设备通过", ok is True, _alerts[-1] if _alerts else "ok")
chk("已记录 usb_device", getattr(app, "usb_device", None) is fake)
# 切回网络模式不应残留 usb 链接
app.sp_link.text = "网络 TCP（串口服务器）"
app.on_link_change(app.sp_link, "网络 TCP（串口服务器）")
chk("切回网络模式", app.link == "net", app.link)

sec("10) MQTT 云模式")
import mqtt_conn as MC
chk("MQTT 模块可导入", MC is not None)
chk("paho-mqtt 可用", MC.MQTT_OK is True, MC.MQTT_ERR)
app.sp_link.text = "MQTT 云（TAS-KS-301）"
app.on_link_change(app.sp_link, "MQTT 云（TAS-KS-301）")
chk("连接模式=mqtt", app.link == "mqtt", app.link)
chk("参数区出现 MQTT 控件",
    all(hasattr(app, n) for n in ("ent_mqtt_host", "ent_mqtt_port",
                                  "ent_mqtt_user", "ent_mqtt_pwd",
                                  "ent_mqtt_down", "ent_mqtt_up",
                                  "ent_addr_m")))


def try_mqtt(host, port, down="mctc/down", up="mctc/up", addr="1"):
    _alerts.clear()
    app.ent_mqtt_host.text = host
    app.ent_mqtt_port.text = port
    app.ent_mqtt_down.text = down
    app.ent_mqtt_up.text = up
    app.ent_addr_m.text = addr
    ok = app.ensure_params()
    return ok, (_alerts[-1] if _alerts else "")


ok, msg = try_mqtt("", "1883")
chk("空服务器地址被拦下", ok is False, msg)
ok, msg = try_mqtt("broker.example.com", "abc")
chk("非法端口被拦下", ok is False, msg)
ok, msg = try_mqtt("broker.example.com", "70000")
chk("端口越界被拦下", ok is False, msg)
ok, msg = try_mqtt("broker.example.com", "1883", down="", up="mctc/up")
chk("空主题被拦下", ok is False, msg)
ok, msg = try_mqtt("broker.example.com", "1883", down="same", up="same")
chk("上下行主题相同被拦下", ok is False, msg)
ok, msg = try_mqtt("broker.example.com", "1883", addr="0")
chk("MQTT 站址 0 被拦下", ok is False, msg)
ok, msg = try_mqtt("broker.example.com", "1883")
chk("合法 MQTT 参数通过", ok is True, msg)
chk("站址取自 MQTT 区输入框", app.addr == 1, str(app.addr))
# DTU 透传的是原始 RTU 帧，MQTT 模式必须禁用 MBAP 封装
app.sp_proto.text = "Modbus TCP 网关"
chk("MQTT 模式强制 RTU(不封装 MBAP)", app.use_tcp_mbap() is False)
app.sp_proto.text = "Modbus RTU 透传"

sec("11) 远程控制使能门控")
chk("默认关闭(只读监控)", app.ctrl_enabled is False, str(app.ctrl_enabled))
chk("受门控控件已收集", len(app.ctrl_widgets) >= 9,
    "%d 个" % len(app.ctrl_widgets))
app._set_actions_enabled(True)          # 模拟已连接
chk("已连接但未开使能: 写指令仍禁用",
    all(w.disabled for w in app.ctrl_widgets))
chk("只读指令不受门控(一键读取)",
    app.btn_read not in app.ctrl_widgets)

_sent = []
app.send_async = lambda *a, **k: _sent.append(a)
app.front_call(3)
app.open_door()
app.close_door()
app.enter_agv()
app.exit_agv()
chk("未开使能: 写指令全部被拦截", len(_sent) == 0,
    "实际下发 %d 条" % len(_sent))
app.sw_cont.active = True
chk("未开使能: 持续开门开关自动回弹", app.sw_cont.active is False)

app.sw_ctrl.active = True               # 经 Kivy 绑定触发 on_ctrl_enable
chk("开启使能", app.ctrl_enabled is True)
chk("开启后写指令控件启用", all(not w.disabled for w in app.ctrl_widgets))
app.front_call(3)
app.open_door()
app.close_door()
chk("开启后写指令可下发", len(_sent) == 3, "实际 %d 条" % len(_sent))
app.sw_cont.active = True
chk("开启使能后可开持续开门", app.sw_cont.active is True)
app.sw_ctrl.active = False
chk("关闭使能: 持续开门被自动停掉", app.sw_cont.active is False)
chk("关闭使能: 写指令控件重新禁用",
    all(w.disabled for w in app.ctrl_widgets))

sec("12) 电梯面板（方向箭头 / 门动画）")
chk("方向箭头控件存在", hasattr(app, "view_dir"))
chk("门动画控件存在", hasattr(app, "view_door"))
app._update_status(res)                 # 复用第 6 节的解析结果
chk("方向箭头跟随运行状态", app.view_dir._dir == res["run"],
    "dir=%s run=%s" % (app.view_dir._dir, res["run"]))
chk("门动画开门度已设置",
    abs(app.view_door._target - M.DOOR_OPENNESS[res["door"]]) < 1e-6,
    "target=%s door=%s" % (app.view_door._target, res["door"]))
chk("门状态文字已更新", app.lbl_door_anim.text != "--", app.lbl_door_anim.text)
chk("方向文字已更新", app.lbl_dir.text != "--", app.lbl_dir.text)

sec("13) 收发线程模型（单线程串行 / 去重 / 重试 / 长度定界）")
import io
import queue as _Q
import tempfile
import threading

# 11) 里把 send_async 换成了桩函数，这里恢复成真实实现
try:
    del app.send_async
except AttributeError:
    pass
app.link, app.proto = "net", "rtu"


def wait_for(cond, timeout=2.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if cond():
            return True
        time.sleep(0.01)
    return False


class FakeConn(object):
    """假连接：按队列返回响应，并记录每次 read_frame 收到的参数。"""

    def __init__(self, replies=None):
        self.replies = list(replies or [])
        self.sent = []
        self.calls = []            # [(expected_len, timeout)]
        self.resets = 0
        self._open = True
        self._lk = threading.Lock()

    def open(self):
        self._open = True

    def close(self):
        self._open = False

    def is_open(self):
        return self._open

    def write(self, data):
        with self._lk:
            self.sent.append(bytes(data))

    def read_frame(self, timeout, inter=0.05, expected_len=None):
        with self._lk:
            self.calls.append((expected_len, timeout))
            return self.replies.pop(0) if self.replies else b""

    def reset_buffers(self):
        with self._lk:
            self.resets += 1


req5 = P.build_read(1, P.REG_SYS, 5)          # 期望响应 = 5 + 2*5 = 15 字节
body5 = bytes([0x03, 0x0A, 0x00, 0x03, 0x00, 0x01, 0x00, 0x02,
               0x00, 0x00, 0x00, 0x03])
resp5 = bytes([0x01]) + body5 + P.crc_bytes(bytes([0x01]) + body5)

# --- 13.1 长度定界：expected_len 必须传到底层 ---
fc = FakeConn(replies=[resp5])
app.conn = fc
app._start_tx_worker()
app.send_async(req5, "读状态", P.parse_status)
chk("收发线程已启动", wait_for(lambda: len(fc.calls) >= 1),
    "线程=%s" % (app._tx_thread is not None))
chk("RTU 模式 expected_len=15 传到底层", fc.calls[0][0] == 15,
    "收到 %s" % (fc.calls[0][0],))
chk("超时按界面毫秒值换算", abs(fc.calls[0][1] - 0.8) < 1e-6,
    "timeout=%s" % (fc.calls[0][1],))

# --- 13.2 Modbus TCP 模式：expected_len 要加 MBAP 头 ---
app.proto = "tcp"
fc2 = FakeConn(replies=[b"\x00\x01\x00\x00\x00\x07\x01\x03\x02\x00\x03\xaa"])
app.conn = fc2
app.send_async(req5, "读状态(TCP)", P.parse_status)
chk("TCP 模式 expected_len=19(=15+4)", wait_for(lambda: len(fc2.calls) >= 1)
    and fc2.calls[0][0] == 19, "收到 %s" % (fc2.calls[0][0] if fc2.calls else None))
chk("TCP 帧线上带 MBAP(7B 头)", fc2.sent and fc2.sent[0][:2] == b"\x00\x01",
    fc2.sent[0][:7].hex(" ") if fc2.sent else "")
app.proto = "rtu"

# --- 13.3 无响应自动重试 ---
app.ent_timeout.text, app.ent_retries.text = "120", "2"
fc3 = FakeConn(replies=[b"", b"", resp5])     # 前两次超时，第三次成功
app.conn = fc3
app.send_async(req5, "读状态(重试)", P.parse_status)
chk("无响应按配置重试至成功", wait_for(lambda: len(fc3.calls) >= 3),
    "实际尝试 %d 次" % len(fc3.calls))
chk("重试前清理残留缓冲", fc3.resets >= 2, "reset=%d" % fc3.resets)
chk("两次超时后第三次拿到响应", fc3.replies == [])

# --- 13.4 重试次数为 0 时只发一次 ---
fc4 = FakeConn(replies=[b""])
app.conn = fc4
app.ent_retries.text = "0"
app.send_async(req5, "读状态(不重试)", P.parse_status)
chk("重试 0 次: 只发一次", wait_for(lambda: len(fc4.calls) >= 1)
    and not wait_for(lambda: len(fc4.calls) >= 2, 0.4),
    "尝试 %d 次" % len(fc4.calls))

# --- 13.5 周期性任务去重（队列不积压）---
app.conn = None
app._stop_tx_worker()
app._tx_queue = _Q.Queue()
f_cont = P.build_write(1, P.REG_DOOR_CTRL, P.OPEN_VAL)
for _ in range(20):
    app.send_async(f_cont, "持续开门", quiet=True, coalesce="cont_open")
chk("同键周期任务只留最新 1 条", app._tx_queue.qsize() == 1,
    "队列 %d" % app._tx_queue.qsize())
for _ in range(20):
    app.send_async(f_cont, "AGV心跳", quiet=True, coalesce="agv_hb")
chk("不同键互不干扰", app._tx_queue.qsize() == 2,
    "队列 %d" % app._tx_queue.qsize())
app.send_async(req5, "手动开门", quiet=True)   # 无 coalesce 键 -> 不去重
chk("无键任务不参与去重", app._tx_queue.qsize() == 3,
    "队列 %d" % app._tx_queue.qsize())

# --- 13.6 队列积压上限 ---
app._tx_queue = _Q.Queue()
for i in range(100):
    app.send_async(req5, "填充%d" % i, quiet=True)
app.send_async(req5, "溢出那条", quiet=True)
chk("积压超上限时丢弃新任务", app._tx_queue.qsize() == 100,
    "队列 %d" % app._tx_queue.qsize())

# --- 13.7 超时/重试参数钳位与 MQTT 放宽 ---
app.ent_timeout.text, app.link = "800", "net"
chk("超时 800ms -> 0.8s", abs(app._io_timeout() - 0.8) < 1e-6,
    str(app._io_timeout()))
app.link = "mqtt"
chk("MQTT 自动放宽到 ≥3s", app._io_timeout() >= 3.0, str(app._io_timeout()))
app.ent_timeout.text = "5000"
chk("MQTT 下用户设更大时尊重用户值", abs(app._io_timeout() - 5.0) < 1e-6,
    str(app._io_timeout()))
app.link, app.ent_timeout.text = "net", "99999"
chk("超时上限 20000ms", abs(app._io_timeout() - 20.0) < 1e-6,
    str(app._io_timeout()))
app.ent_timeout.text, app.ent_retries.text = "800", "abc"
chk("重试非法输入回落默认 2", app._retries_now() == 2, str(app._retries_now()))
app.ent_retries.text = "99"
chk("重试上限 9", app._retries_now() == 9, str(app._retries_now()))
app.ent_retries.text = "0"
chk("重试下限 0", app._retries_now() == 0, str(app._retries_now()))
app.ent_retries.text = "2"

# --- 13.8 断开清理 ---
app.conn = FakeConn()
app._start_tx_worker()
_th = app._tx_thread
chk("连接后收发线程存活", _th is not None and _th.is_alive())
app._teardown_conn()
chk("断开后队列引用已清空", app._tx_queue is None)
chk("断开后收发线程已退出", wait_for(lambda: not _th.is_alive(), 2.0))

# --- 13.9 日志导出 ---
_td = tempfile.mkdtemp(prefix="mctc_log_")
_orig_dir = M.MCTCApp._export_dir
M.MCTCApp._export_dir = staticmethod(lambda: _td)
app._log_plain = ["10:00:00  [TX] 01 03 9C 41 00 05 3F",
                  "10:00:00  [RX] 01 03 0A 00 03 00 01"]
app.addr = 1
_alerts.clear()
app.export_log()
_files = [f for f in os.listdir(_td) if f.startswith("mctc_log_")]
chk("导出生成 txt", len(_files) == 1, str(_files))
if _files:
    _fp = os.path.join(_td, _files[0])
    _raw = io.open(_fp, "rb").read()
    _txt = io.open(_fp, encoding="utf-8-sig").read()
    chk("导出带 UTF-8 BOM", _raw[:3] == b"\xef\xbb\xbf", str(_raw[:3]))
    chk("抬头含协议与站址", "MCTC-KZ-B0S" in _txt and "0x01" in _txt)
    chk("正文含收发原始帧", "01 03 9C 41 00 05" in _txt)
chk("导出后提示路径", bool(_alerts) and "mctc_log_" in _alerts[-1])
app._log_plain = []
_alerts.clear()
app.export_log()
chk("空日志导出给出提示", bool(_alerts) and "没有日志" in _alerts[-1],
    _alerts[-1] if _alerts else "无提示")
M.MCTCApp._export_dir = _orig_dir

# --- 13.10 前后台切换 ---
chk("on_pause 返回 True(不销毁)", app.on_pause() is True)
app.conn = None
app.on_resume()                      # 未连接时不应崩
chk("on_resume 未连接不崩", True)

sec("14) 协议层版本（防与桌面版走偏）")
chk("含 CRC16 查表法(256 项)", hasattr(P, "_CRC_TABLE")
    and len(P._CRC_TABLE) == 256, str(len(getattr(P, "_CRC_TABLE", []))))
chk("查表法与逐位法结果一致",
    all(P.crc16_modbus(bytes([i])) == P.crc16_modbus_bitwise(bytes([i]))
        for i in range(256)))
chk("含响应长度推算 expected_resp_len",
    P.expected_resp_len(P.build_read(1, P.REG_SYS, 5)) == 15
    and P.expected_resp_len(P.build_write(1, P.REG_FRONT, 3)) == 8,
    "读5=%s 写=%s" % (P.expected_resp_len(P.build_read(1, P.REG_SYS, 5)),
                      P.expected_resp_len(P.build_write(1, P.REG_FRONT, 3))))
_b = bytes([0x02, 0x03, 0x02, 0x00, 0x03])
chk("含从机地址校验",
    "从机地址" in (P.validate_basic(_b + P.crc_bytes(_b),
                                    P.build_read(1, P.REG_SYS, 1)) or ""),
    str(P.validate_basic(_b + P.crc_bytes(_b), P.build_read(1, P.REG_SYS, 1))))
chk("异常码有中文说明", P.MODBUS_EXC.get(2) == "非法数据地址",
    str(P.MODBUS_EXC.get(2)))

# 强校验：protocol_core.py 必须等于桌面版协议层的抽取结果（跑 sync_protocol.py）
_src = os.path.join(os.path.dirname(HERE), "protocol_converter_test.py")
if os.path.exists(_src):
    _txt = io.open(_src, encoding="utf-8").read()
    _want = _txt[:_txt.find("\nclass App")]
    _core = io.open(os.path.join(HERE, "protocol_core.py"), encoding="utf-8").read()
    _same = _core.endswith(_want)
    chk("protocol_core 与桌面版协议层完全一致", _same,
        "一致" if _same else "已漂移！请运行: python sync_protocol.py")
else:
    print("  [SKIP] 桌面版 protocol_converter_test.py 不在上级目录，跳过一致性强校验")

sec("结果: %d 通过 / %d 失败" % (npass, nfail))
sys.exit(1 if nfail else 0)
