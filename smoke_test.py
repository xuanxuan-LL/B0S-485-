#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""安卓版(Kivy) 冒烟测试：在 Windows 上提前验证 UI 构建与交互逻辑。

不弹窗口也能跑（Kivy 用 'mock' 隐藏窗口 + 空输入提供者）。
覆盖: 字体注册 / 控件齐全 / 未连接禁用 / 间隔钳位 / 状态解析刷新 UI / AGV。
"""
import os, sys

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
chk("已连接: 全部动作启用", all(not w.disabled for w in app.action_widgets))

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

sec("结果: %d 通过 / %d 失败" % (npass, nfail))
sys.exit(1 if nfail else 0)
