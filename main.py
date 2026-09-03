#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MCTC-KZ-B0S 电梯测试（安卓版 / Kivy）
====================================
与 Windows 桌面版 protocol_converter_test.py 共用同一份协议实现(protocol_core.py),
仅 UI 层用 Kivy 重写, 以便在安卓手机上运行。

与桌面版的差异（安卓限制导致）:
  1) 连接方式只有「网络 TCP」: 手机没有 RS485 串口, 也不建议走 USB-OTG,
     统一通过 WiFi 连接串口服务器(NA11x / C2000)的 TCP 端口。
  2) 网络操作(连接/收发)全部放后台线程: 安卓主线程做网络会直接崩。
  3) 中文字体需显式注册: Kivy 默认 Roboto 不含中文字形, 不处理会显示方框。

功能:
  * 串口服务器预设 NA11x(192.168.3.7:8887) / C2000(192.168.4.1:8000) + IP/端口自定义
  * 传输协议切换: Modbus RTU 透传 / Modbus TCP 网关(MBAP)
  * 一键读取电梯状态(系统/运行/门/轿内开关/楼层) + 自动刷新开关 + 间隔(秒)
  * 1-5 层前门登记、开门/关门、司机功能开关、持续开门信号(200ms)
  * AGV 进入/退出/读状态、心跳自动发送开关 + 间隔(秒)
  * 通信日志(TX/RX 原始帧 + 解析说明)

协议层来源: protocol_core.py (由 sync_protocol.py 从桌面版自动抽取, 勿手改)
"""
import os
import threading
import time

from kivy.app import App
from kivy.clock import Clock
from kivy.core.text import LabelBase
from kivy.metrics import dp, sp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.gridlayout import GridLayout
from kivy.uix.label import Label
from kivy.uix.popup import Popup
from kivy.uix.scrollview import ScrollView
from kivy.uix.spinner import Spinner
from kivy.uix.switch import Switch
from kivy.uix.tabbedpanel import TabbedPanel, TabbedPanelHeader
from kivy.uix.textinput import TextInput
from kivy.utils import platform

from protocol_core import (
    SERVER_PRESETS,
    AGV_STAT_MAP, CLOSE_VAL, DOOR_MAP, OPEN_VAL,
    REG_AGV_CTRL, REG_AGV_HB, REG_AGV_STAT, REG_DOOR_CTRL, REG_DRIVER,
    REG_FRONT, REG_SYS,
    RUN_MAP, SYS_MAP, TcpConn,
    build_read, build_write, decode_carin, parse_agv_status, parse_response,
    parse_status, rtu_to_tcp_frame, tcp_resp_error, tcp_resp_to_rtu,
)

import usb_conn          # USB-OTG 串口通道（非安卓环境里自动降级为不可用）


# ---------------------------------------------------------------------------
# 中文字体（关键：Kivy 默认字体不含中文，不注册会全部显示成方框）
# ---------------------------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def pick_cjk_font():
    """按优先级探测并注册中文字体为 'CJK'，返回 (font_name, 实际路径)。

    顺序: 随包内置(assets/) > 系统字体目录。
    安卓各家 ROM 的 CJK 字体路径不统一, 所以内置一份 Noto Sans SC (SIL OFL)
    最稳; Windows 上则直接用系统自带字体, 免得多带 8MB。
    """
    bundled = [
        os.path.join(BASE_DIR, "assets", "NotoSansSC-Regular.otf"),
        os.path.join(BASE_DIR, "assets", "NotoSansSC-Regular.ttf"),
    ]
    if platform == "android":
        system = [
            "/system/fonts/NotoSansCJK-Regular.ttc",
            "/system/fonts/NotoSansSC-Regular.otf",
            "/system/fonts/DroidSansFallback.ttf",
        ]
    else:
        windir = os.environ.get("WINDIR", r"C:\Windows")
        fonts = os.path.join(windir, "Fonts")
        system = [
            os.path.join(fonts, "msyh.ttc"),
            os.path.join(fonts, "simhei.ttf"),
            os.path.join(fonts, "simsun.ttc"),
        ]
    for p in bundled + system:
        if p and os.path.exists(p):
            try:
                LabelBase.register("CJK", p)
                return "CJK", p
            except Exception:
                continue
    return "Roboto", "(未找到中文字体)"


FONT, FONT_PATH = pick_cjk_font()

# 配色
C_TX = "#1a4f8a"
C_RX = "#0a7d28"
C_OK = "#0a7d28"
C_FAIL = "#c00000"
C_INFO = "#666666"


def esc(s):
    """Kivy markup 转义（日志文本里可能出现 [ ] &）。"""
    return (str(s).replace("&", "&amp;")
            .replace("[", "&bl;").replace("]", "&br;"))


class Card(BoxLayout):
    """带背景色的状态卡片。"""

    def __init__(self, title, **kw):
        kw.setdefault("orientation", "vertical")
        kw.setdefault("size_hint_y", None)
        kw.setdefault("height", dp(64))
        kw.setdefault("padding", [dp(8), dp(5)])
        super(Card, self).__init__(**kw)
        self._bg = None
        with self.canvas.before:
            from kivy.graphics import Color, Rectangle
            Color(1, 1, 1, 1)
            self._rect = Rectangle(pos=self.pos, size=self.size)
        self.bind(pos=self._sync, size=self._sync)

        self.title_lbl = Label(text=title, font_name=FONT, font_size=sp(11),
                               color=(0.42, 0.42, 0.42, 1), halign="left",
                               size_hint_y=0.4)
        self.title_lbl.bind(size=lambda *a: setattr(
            self.title_lbl, "text_size", (self.width - dp(16), None)))
        self.val_lbl = Label(text="--", font_name=FONT, font_size=sp(14),
                             bold=True, color=(0.15, 0.15, 0.15, 1),
                             halign="left", size_hint_y=0.6)
        self.val_lbl.bind(size=lambda *a: setattr(
            self.val_lbl, "text_size", (self.width - dp(16), None)))
        self.add_widget(self.title_lbl)
        self.add_widget(self.val_lbl)

    def _sync(self, *a):
        self._rect.pos = self.pos
        self._rect.size = self.size

    def set_value(self, text, color="#222222"):
        self.val_lbl.text = text
        r, g, b = (int(color[i:i + 2], 16) / 255.0 for i in (1, 3, 5))
        self.val_lbl.color = (r, g, b, 1)


class MCTCApp(App):
    title = "MCTC 电梯测试（安卓版）"

    # ---- 生命周期 ----
    def build(self):
        self.conn = None
        self.addr = 1
        self.timeout = 0.8
        self.host = "192.168.3.7"
        self.netport = 8887
        self.proto = "rtu"          # rtu | tcp
        self._txid = 0
        self._hb_counter = 0
        self._alive = True
        self.lock = threading.Lock()
        self.action_widgets = []
        self._log_lines = []

        root = BoxLayout(orientation="vertical")
        root.add_widget(self._build_tabs())
        root.add_widget(self._build_statusbar())
        Clock.schedule_once(lambda dt: self._welcome(), 0.1)
        return root

    def on_stop(self):
        self._alive = False
        try:
            if self.conn:
                self.conn.close()
        except Exception:
            pass

    def _welcome(self):
        self.log("MCTC 电梯测试（安卓版）就绪", "info")
        self.log("手机需与串口服务器在同一 WiFi 网段（或直连其 AP）", "info")
        self.log("串口服务器串口侧参数须为 38400/8/N/1，模式 TCP Server", "info")
        self.log("字体: %s" % FONT_PATH, "info")

    # ---- UI 构建 ----
    def _build_tabs(self):
        tabs = TabbedPanel(do_default_tab=False, tab_height=dp(44))
        for text, content in (
            ("状态", self._tab_status()),
            ("控制", self._tab_control()),
            ("AGV", self._tab_agv()),
            ("日志", self._tab_log()),
        ):
            head = TabbedPanelHeader(text=text, font_name=FONT)
            head.font_size = sp(14)
            head.content = content
            tabs.add_widget(head)
        return tabs

    @staticmethod
    def _scrollable(inner):
        sv = ScrollView(do_scroll_x=False)
        inner.size_hint_y = None
        inner.bind(minimum_height=inner.setter("height"))
        sv.add_widget(inner)
        return sv

    @staticmethod
    def _section(title):
        """小标题行。"""
        box = BoxLayout(orientation="vertical", size_hint_y=None,
                        height=dp(28), padding=[dp(4), 0])
        lbl = Label(text=title, font_name=FONT, font_size=sp(13), bold=True,
                    color=(0.10, 0.31, 0.54, 1), halign="left", valign="middle")
        lbl.bind(size=lambda *a: setattr(lbl, "text_size", (a[0].width, None)))
        box.add_widget(lbl)
        return box

    @staticmethod
    def _row(height=dp(42), **kw):
        return BoxLayout(orientation="horizontal", size_hint_y=None,
                         height=height, spacing=dp(6), **kw)

    def _labeled_input(self, row, label, text, w=0.45, hint=""):
        lbl = Label(text=label, font_name=FONT, font_size=sp(13),
                    size_hint_x=0.28, halign="left", valign="middle")
        lbl.bind(size=lambda *a: setattr(lbl, "text_size", (a[0].width, None)))
        row.add_widget(lbl)
        ti = TextInput(text=text, font_name=FONT, font_size=sp(14),
                       size_hint_x=w, multiline=False, hint_text=hint,
                       padding=[dp(8), dp(8), 0, 0])
        row.add_widget(ti)
        return ti

    def _switch_row(self, label, height=dp(44)):
        row = self._row(height=height)
        lbl = Label(text=label, font_name=FONT, font_size=sp(13),
                    size_hint_x=0.62, halign="left", valign="middle")
        lbl.bind(size=lambda *a: setattr(lbl, "text_size", (a[0].width, None)))
        row.add_widget(lbl)
        sw = Switch(size_hint_x=0.2)
        row.add_widget(sw)
        return row, sw

    # ---- 连接方式切换（网络 TCP / USB OTG）----
    def _build_tcp_rows(self):
        row_type = self._row()
        lbl = Label(text="服务器类型", font_name=FONT, font_size=sp(13),
                    size_hint_x=0.28, halign="left", valign="middle")
        lbl.bind(size=lambda *a: setattr(lbl, "text_size", (a[0].width, None)))
        row_type.add_widget(lbl)
        self.sp_type = Spinner(text="NA11x (亿佰特)", font_name=FONT,
                               font_size=sp(13), size_hint_x=0.72,
                               values=list(SERVER_PRESETS.keys()) + ["自定义"])
        self.sp_type.bind(text=self.on_server_type)
        row_type.add_widget(self.sp_type)

        row_ip = self._row()
        self.ent_ip = self._labeled_input(row_ip, "服务器 IP", "192.168.3.7", 0.34)
        self.ent_port = self._labeled_input(row_ip, "端口", "8887", 0.30)

        row_addr = self._row()
        self.ent_addr = self._labeled_input(row_addr, "站址(HEX)", "1", 0.24)
        lbl_p = Label(text="传输协议", font_name=FONT, font_size=sp(13),
                      size_hint_x=0.26, halign="left", valign="middle")
        lbl_p.bind(size=lambda *a: setattr(lbl_p, "text_size", (a[0].width, None)))
        row_addr.add_widget(lbl_p)
        self.sp_proto = Spinner(text="Modbus RTU 透传", font_name=FONT,
                                font_size=sp(12), size_hint_x=0.46,
                                values=["Modbus RTU 透传", "Modbus TCP 网关"])
        self.sp_proto.bind(text=self.on_proto_change)
        row_addr.add_widget(self.sp_proto)

        self.tcp_rows = [row_type, row_ip, row_addr]

    def _build_usb_rows(self):
        row_usb = self._row()
        lbl = Label(text="USB 设备", font_name=FONT, font_size=sp(13),
                    size_hint_x=0.26, halign="left", valign="middle")
        lbl.bind(size=lambda *a: setattr(lbl, "text_size", (a[0].width, None)))
        row_usb.add_widget(lbl)
        self.sp_usb = Spinner(text="（点扫描检测）", font_name=FONT,
                              font_size=sp(11), size_hint_x=0.56,
                              values=["（点扫描检测）"])
        row_usb.add_widget(self.sp_usb)
        self.btn_scan = Button(text="扫描", font_name=FONT, font_size=sp(13),
                               size_hint_x=0.18)
        self.btn_scan.bind(on_release=self._scan_usb)
        row_usb.add_widget(self.btn_scan)

        row_info = self._row(height=dp(34))
        self.lbl_usb = Label(text="串口参数固定 38400 / 8 / N / 1（Modbus RTU）",
                             font_name=FONT, font_size=sp(11),
                             color=(0.35, 0.35, 0.35, 1), halign="left",
                             valign="middle")
        self.lbl_usb.bind(size=lambda *a: setattr(
            self.lbl_usb, "text_size", (a[0].width, None)))
        row_info.add_widget(self.lbl_usb)

        self.usb_rows = [row_usb, row_info]

    def _apply_link_mode(self, text):
        """按连接方式重建参数区。"""
        usb = text.startswith("USB")
        self.link = "usb" if usb else "net"
        self.box_params.clear_widgets()
        for r in (self.usb_rows if usb else self.tcp_rows):
            self.box_params.add_widget(r)

    def on_link_change(self, spinner, text):
        self._apply_link_mode(text)
        if text.startswith("USB"):
            self.log("连接方式=USB OTG 直连：手机 → USB 转 485 → 协议转换板，"
                     "走 Modbus RTU，串口参数 38400/8/N/1", "info")
            if not usb_conn.USB_OK:
                self.log("当前环境未加载 USB 驱动（%s），正式 APK 里才可用"
                         % usb_conn.USB_ERR, "fail")
            self._scan_usb()
        else:
            self.log("连接方式=网络 TCP：手机 → WiFi → 串口服务器，保留上一次的 "
                     "IP/端口与传输协议设置", "info")

    def _scan_usb(self, *a):
        """枚举挂在手机上的 USB 设备并填进下拉框。"""
        devs, err = usb_conn.list_devices()
        self.usb_devices = devs
        labels = [d["label"] for d in devs]
        self.sp_usb.values = labels or ["（未检测到 USB 设备）"]
        if labels:
            pick = next((d for d in devs if d["is_485"]), devs[0])
            self.sp_usb.text = pick["label"]
            self.lbl_usb.text = ("识别到 %d 个设备 · 串口参数 38400/8/N/1"
                                 % len(devs))
        else:
            self.sp_usb.text = "（未检测到 USB 设备）"
            self.lbl_usb.text = (err or "未检测到设备：确认 OTG 转接线接好、"
                                        "手机已开启 OTG 功能")
        self.log("USB 扫描: %d 个设备%s" % (len(devs), ("（%s）" % err) if err else ""),
                 "info" if devs else "fail")

    def _current_usb_device(self):
        """按当前下拉框文字取回设备 dict。"""
        for d in getattr(self, "usb_devices", []):
            if d["label"] == self.sp_usb.text:
                return d
        return None

    # ---- Tab1 状态 ----
    def _tab_status(self):
        inner = BoxLayout(orientation="vertical", spacing=dp(8),
                          padding=[dp(10), dp(10)])

        inner.add_widget(self._section("连接设置"))

        # 连接方式：网络 TCP（串口服务器） / USB OTG 直连
        row_link = self._row()
        lbl = Label(text="连接方式", font_name=FONT, font_size=sp(13),
                    size_hint_x=0.28, halign="left", valign="middle")
        lbl.bind(size=lambda *a: setattr(lbl, "text_size", (a[0].width, None)))
        row_link.add_widget(lbl)
        self.sp_link = Spinner(text="网络 TCP（串口服务器）", font_name=FONT,
                               font_size=sp(13), size_hint_x=0.72,
                               values=["网络 TCP（串口服务器）", "USB OTG 直连"])
        self.sp_link.bind(text=self.on_link_change)
        row_link.add_widget(self.sp_link)
        inner.add_widget(row_link)

        # 参数区（按连接方式切换内容）
        self.box_params = BoxLayout(orientation="vertical", size_hint_y=None,
                                    spacing=dp(6))
        self.box_params.bind(minimum_height=self.box_params.setter("height"))
        inner.add_widget(self.box_params)

        self.tcp_rows = []
        self.usb_rows = []
        self.usb_devices = []
        self._usb_event = None      # USB 拔线看门狗
        self._build_tcp_rows()
        self._build_usb_rows()
        self._apply_link_mode("网络 TCP（串口服务器）")

        row_conn = self._row(height=dp(48))
        self.btn_conn = Button(text="连接", font_name=FONT, font_size=sp(15),
                               size_hint_x=0.4)
        self.btn_conn.bind(on_release=lambda *a: self.toggle_connect())
        row_conn.add_widget(self.btn_conn)
        self.lbl_conn = Label(text="● 未连接", font_name=FONT, font_size=sp(13),
                              color=(0.75, 0, 0, 1), halign="left",
                              valign="middle")
        self.lbl_conn.bind(size=lambda *a: setattr(
            self.lbl_conn, "text_size", (a[0].width, None)))
        row_conn.add_widget(self.lbl_conn)
        inner.add_widget(row_conn)

        # 状态卡片
        inner.add_widget(self._section("电梯状态"))
        grid = GridLayout(cols=2, size_hint_y=None, height=dp(136),
                          spacing=dp(6))
        self.card_sys = Card("系统状态")
        self.card_run = Card("运行状态")
        self.card_door = Card("门状态")
        self.card_carin = Card("轿内开关输入状态")
        for c in (self.card_sys, self.card_run, self.card_door, self.card_carin):
            grid.add_widget(c)
        inner.add_widget(grid)

        # 楼层大卡
        self.floor_card = BoxLayout(orientation="vertical", size_hint_y=None,
                                    height=dp(140), padding=[dp(10), dp(6)])
        self._floor_bg = None
        with self.floor_card.canvas.before:
            from kivy.graphics import Color, Rectangle
            Color(0.918, 0.945, 0.984, 1)
            self._floor_rect = Rectangle(pos=self.floor_card.pos,
                                         size=self.floor_card.size)
        self.floor_card.bind(pos=self._sync_floor, size=self._sync_floor)
        self.lbl_floor_title = Label(text="当前楼层", font_name=FONT,
                                     font_size=sp(16), bold=True,
                                     color=(0.10, 0.31, 0.54, 1),
                                     size_hint_y=0.3)
        self.lbl_floor = Label(text="--", font_name=FONT, font_size=sp(56),
                               bold=True, color=(0.10, 0.31, 0.54, 1),
                               size_hint_y=0.7)
        self.floor_card.add_widget(self.lbl_floor_title)
        self.floor_card.add_widget(self.lbl_floor)
        inner.add_widget(self.floor_card)

        # 一键读取 + 自动刷新
        row_read = self._row(height=dp(50))
        self.btn_read = Button(text="一键读取电梯状态", font_name=FONT,
                               font_size=sp(15))
        self.btn_read.bind(on_release=lambda *a: self.read_status())
        row_read.add_widget(self.btn_read)
        inner.add_widget(row_read)
        self.action_widgets.append(self.btn_read)

        row_auto = self._row(height=dp(44))
        lbl_a = Label(text="自动刷新  间隔", font_name=FONT, font_size=sp(13),
                      size_hint_x=0.42, halign="left", valign="middle")
        lbl_a.bind(size=lambda *a: setattr(lbl_a, "text_size", (a[0].width, None)))
        row_auto.add_widget(lbl_a)
        self.sw_autoref = Switch(size_hint_x=0.18)
        self.sw_autoref.bind(active=self.on_auto_refresh_toggle)
        row_auto.add_widget(self.sw_autoref)
        self.ent_interval = TextInput(text="3", font_name=FONT, font_size=sp(14),
                                      size_hint_x=0.22, multiline=False,
                                      input_filter="float",
                                      padding=[dp(8), dp(8), 0, 0])
        row_auto.add_widget(self.ent_interval)
        lbl_s = Label(text="秒(s)", font_name=FONT, font_size=sp(12),
                      size_hint_x=0.18, halign="left", valign="middle",
                      color=(0.35, 0.35, 0.35, 1))
        row_auto.add_widget(lbl_s)
        inner.add_widget(row_auto)
        self.action_widgets.append(self.sw_autoref)

        return self._scrollable(inner)

    def _sync_floor(self, *a):
        self._floor_rect.pos = self.floor_card.pos
        self._floor_rect.size = self.floor_card.size

    # ---- Tab2 控制 ----
    def _tab_control(self):
        inner = BoxLayout(orientation="vertical", spacing=dp(8),
                          padding=[dp(10), dp(10)])

        inner.add_widget(self._section("1-5 层前门指令（登记到内呼）"))
        grid = GridLayout(cols=3, size_hint_y=None, height=dp(112),
                          spacing=dp(6))
        for f in range(1, 6):
            b = Button(text="%d 层前门" % f, font_name=FONT, font_size=sp(14))
            b.bind(on_release=lambda *a, fl=f: self.front_call(fl))
            grid.add_widget(b)
            self.action_widgets.append(b)
        inner.add_widget(grid)

        inner.add_widget(self._section("开关门控制"))
        row_door = self._row(height=dp(50))
        b_open = Button(text="开门", font_name=FONT, font_size=sp(15))
        b_open.bind(on_release=lambda *a: self.open_door())
        b_close = Button(text="关门", font_name=FONT, font_size=sp(15))
        b_close.bind(on_release=lambda *a: self.close_door())
        row_door.add_widget(b_open)
        row_door.add_widget(b_close)
        inner.add_widget(row_door)
        self.action_widgets += [b_open, b_close]

        inner.add_widget(self._section("司机功能 / 持续开门"))
        row_drv, self.sw_driver = self._switch_row("司机功能开 (写 0x9CA0=1)")
        self.sw_driver.bind(active=self.toggle_driver)
        inner.add_widget(row_drv)
        self.action_widgets.append(self.sw_driver)

        row_cont, self.sw_cont = self._switch_row("持续开门信号 (200ms)")
        self.sw_cont.bind(active=self.toggle_cont_open)
        inner.add_widget(row_cont)
        self.action_widgets.append(self.sw_cont)

        tip = Label(text="持续开门信号：每 200 毫秒循环发送开门指令 (0x9C56=0x0003)，"
                         "取消勾选即停止",
                    font_name=FONT, font_size=sp(11), size_hint_y=None,
                    height=dp(34), color=(0.4, 0.4, 0.4, 1), halign="left",
                    valign="middle")
        tip.bind(size=lambda *a: setattr(tip, "text_size", (a[0].width, None)))
        inner.add_widget(tip)

        return self._scrollable(inner)

    # ---- Tab3 AGV ----
    def _tab_agv(self):
        inner = BoxLayout(orientation="vertical", spacing=dp(8),
                          padding=[dp(10), dp(10)])

        inner.add_widget(self._section("AGV 模式"))
        row1 = self._row(height=dp(50))
        self.btn_enter = Button(text="进入 AGV 模式", font_name=FONT, font_size=sp(14))
        self.btn_enter.bind(on_release=lambda *a: self.enter_agv())
        row1.add_widget(self.btn_enter)
        row1.add_widget(Button(text="退出 AGV 模式", font_name=FONT,
                               font_size=sp(14),
                               on_release=lambda *a: self.exit_agv()))
        inner.add_widget(row1)
        self.action_widgets.append(self.btn_enter)

        row2 = self._row(height=dp(50))
        b_read = Button(text="读取 AGV 状态", font_name=FONT, font_size=sp(14))
        b_read.bind(on_release=lambda *a: self.read_agv_status())
        row2.add_widget(b_read)
        inner.add_widget(row2)
        self.action_widgets.append(b_read)

        row_st = self._row(height=dp(40))
        lbl = Label(text="AGV 状态", font_name=FONT, font_size=sp(13),
                    size_hint_x=0.3, halign="left", valign="middle")
        lbl.bind(size=lambda *a: setattr(lbl, "text_size", (a[0].width, None)))
        row_st.add_widget(lbl)
        self.lbl_agv = Label(text="--", font_name=FONT, font_size=sp(14),
                             bold=True, halign="left", valign="middle")
        self.lbl_agv.bind(size=lambda *a: setattr(self.lbl_agv,
                                                  "text_size", (a[0].width, None)))
        row_st.add_widget(self.lbl_agv)
        inner.add_widget(row_st)

        inner.add_widget(self._section("AGV 心跳"))
        row_hb, self.sw_hb = self._switch_row("心跳自动发送")
        self.sw_hb.bind(active=self.on_agv_hb_toggle)
        inner.add_widget(row_hb)
        self.action_widgets.append(self.sw_hb)

        row_iv = self._row(height=dp(44))
        lbl_i = Label(text="间隔", font_name=FONT, font_size=sp(13),
                      size_hint_x=0.25, halign="left", valign="middle")
        lbl_i.bind(size=lambda *a: setattr(lbl_i, "text_size", (a[0].width, None)))
        row_iv.add_widget(lbl_i)
        self.ent_hb = TextInput(text="30", font_name=FONT, font_size=sp(14),
                                size_hint_x=0.22, multiline=False,
                                input_filter="float",
                                padding=[dp(8), dp(8), 0, 0])
        row_iv.add_widget(self.ent_hb)
        lbl_u = Label(text="秒(s)  须≤120s，建议≤30s", font_name=FONT,
                      font_size=sp(11), size_hint_x=0.53, halign="left",
                      valign="middle", color=(0.4, 0.4, 0.4, 1))
        lbl_u.bind(size=lambda *a: setattr(lbl_u, "text_size", (a[0].width, None)))
        row_iv.add_widget(lbl_u)
        inner.add_widget(row_iv)

        tip = Label(text="心跳为向 0x9CA6 写任意值；超过 120 秒未写，"
                         "电梯系统会自动退出 AGV 模式",
                    font_name=FONT, font_size=sp(11), size_hint_y=None,
                    height=dp(34), color=(0.4, 0.4, 0.4, 1), halign="left",
                    valign="middle")
        tip.bind(size=lambda *a: setattr(tip, "text_size", (a[0].width, None)))
        inner.add_widget(tip)

        return self._scrollable(inner)

    # ---- Tab4 日志 ----
    def _tab_log(self):
        inner = BoxLayout(orientation="vertical", spacing=dp(6),
                          padding=[dp(8), dp(8)])
        row_btn = self._row(height=dp(44))
        row_btn.add_widget(Button(text="清空日志", font_name=FONT,
                                  font_size=sp(14),
                                  on_release=lambda *a: self.clear_log()))
        inner.add_widget(row_btn)

        sv = ScrollView(do_scroll_x=False, size_hint_y=1)
        self.log_label = Label(text="", font_name=FONT, font_size=sp(11),
                               markup=True, size_hint_y=None, halign="left",
                               valign="top")
        self.log_label.bind(size=lambda *a: setattr(
            self.log_label, "text_size", (a[0].width, None)))
        self.log_label.bind(texture_size=lambda *a: setattr(
            self.log_label, "height", max(a[0].texture_size[1], sv.height)))
        sv.add_widget(self.log_label)
        inner.add_widget(sv)
        return inner

    def _build_statusbar(self):
        self.statusbar = Label(text="就绪 · 未连接", font_name=FONT,
                               font_size=sp(11), size_hint_y=None,
                               height=dp(26), halign="left", valign="middle",
                               color=(0.25, 0.25, 0.25, 1), padding=[dp(8), 0])
        self.statusbar.bind(size=lambda *a: setattr(
            self.statusbar, "text_size", (a[0].width, None)))
        bar = BoxLayout(orientation="vertical", size_hint_y=None,
                        height=dp(26))
        with bar.canvas.before:
            from kivy.graphics import Color, Rectangle
            Color(0.933, 0.945, 0.964, 1)
            self._bar_rect = Rectangle(pos=bar.pos, size=bar.size)
        bar.bind(pos=self._sync_bar, size=self._sync_bar)
        bar.add_widget(self.statusbar)
        return bar

    def _sync_bar(self, instance, *a):
        self._bar_rect.pos = instance.pos
        self._bar_rect.size = instance.size

    # ---- 交互回调 ----
    def on_server_type(self, spinner, text):
        preset = SERVER_PRESETS.get(text)
        if preset:
            ip, port = preset
            self.ent_ip.text = ip
            self.ent_port.text = port
            self.log("已选预设 %s -> %s:%s" % (text, ip, port), "info")

    def on_proto_change(self, spinner, text):
        self.proto = "tcp" if text.startswith("Modbus TCP") else "rtu"
        if self.proto == "tcp":
            self.log("传输协议=Modbus TCP 网关：发送 MBAP+PDU（无 CRC，站址放 Unit ID）；"
                     "需先在串口服务器开启 Modbus 网关，端口不变", "info")
        else:
            self.log("传输协议=Modbus RTU 透传：发送原始 RTU 帧（带 CRC），"
                     "串口服务器保持透明/透传模式", "info")

    def use_tcp_mbap(self):
        # USB-OTG 直连走纯 Modbus RTU（设备侧没有 MBAP 网关），永远不封装
        if getattr(self, "link", "net") != "net":
            return False
        return self.proto == "tcp"

    def ensure_params(self):
        # 站址两种模式都要校验
        try:
            self.addr = int(self.ent_addr.text.strip(), 16)
        except ValueError:
            self.alert("站址必须为十六进制（如 1）")
            return False
        if not (1 <= self.addr <= 247):
            self.alert("站址范围 1-247")
            return False
        # USB-OTG 直连：校验是否已选到设备（无 IP/端口）
        if getattr(self, "link", "net") == "usb":
            dev = self._current_usb_device()
            if not dev:
                self.alert("请先点击「扫描」并选择一个 USB 设备\n"
                           "（列表为空时确认：OTG 转接线接好、手机已开启 OTG）")
                return False
            self.usb_device = dev
            return True
        # 网络模式：校验 IP + 端口
        self.host = self.ent_ip.text.strip()
        try:
            self.netport = int(self.ent_port.text.strip())
        except ValueError:
            self.alert("端口必须为数字")
            return False
        if not self.host:
            self.alert("请填写服务器 IP")
            return False
        if not self._valid_ip(self.host):
            self.alert("IP 格式不对（示例 192.168.3.7，"
                       "四段 0-255 的十进制数字）")
            return False
        if not (1 <= self.netport <= 65535):
            self.alert("端口范围 1-65535")
            return False
        return True

    @staticmethod
    def _valid_ip(s: str) -> bool:
        parts = s.split(".")
        if len(parts) != 4:
            return False
        for p in parts:
            if not p.isdigit() or not (0 <= int(p) <= 255):
                return False
        return True

    def toggle_connect(self):
        if self.conn and self.conn.is_open():
            threading.Thread(target=self._disc_worker, daemon=True).start()
            return
        if not self.ensure_params():
            return
        self.btn_conn.disabled = True
        self.btn_conn.text = "连接中…"
        # 安卓主线程禁止网络操作，必须放后台线程
        threading.Thread(target=self._conn_worker, daemon=True).start()

    def _conn_worker(self):
        try:
            if getattr(self, "link", "net") == "usb":
                dev = getattr(self, "usb_device", None) or self._current_usb_device()
                if not dev:
                    raise RuntimeError("未选择 USB 设备")
                conn = usb_conn.UsbConn(dev["name"])
                conn.open()
                if not conn.is_open():
                    raise RuntimeError("串口未真正打开：可能系统未授权 USB 设备，"
                                       "请在弹出的对话框中允许访问后重试")
                self.conn = conn
                label = "USB %s" % dev["label"]
            else:
                conn = TcpConn(self.host, self.netport)
                conn.open()
                self.conn = conn
                label = "%s:%d" % (self.host, self.netport)
            Clock.schedule_once(lambda dt: self._on_connected(label), 0)
        except Exception as e:
            Clock.schedule_once(lambda dt: self._on_connect_failed(str(e)), 0)

    def _on_connected(self, label):
        self.lbl_conn.text = "● 已连接 %s" % label
        self.lbl_conn.color = (0, 0.49, 0, 1)
        self.btn_conn.text = "断开"
        self.btn_conn.disabled = False
        self._set_actions_enabled(True)
        self.log("已连接: %s  站址=0x%02X" % (label, self.addr), "info")
        self._update_statusbar("已连接 %s" % label)
        if getattr(self, "link", "net") == "usb":
            # 拔线看门狗：底层未必在拔线时抛错，定时探活自动断连
            self._usb_event = Clock.schedule_interval(self._watch_usb, 2)

    def _on_connect_failed(self, err):
        self.conn = None
        self.lbl_conn.text = "● 未连接"
        self.lbl_conn.color = (0.75, 0, 0, 1)
        self.btn_conn.text = "连接"
        self.btn_conn.disabled = False
        self._set_actions_enabled(False)
        self.log("连接失败: %s" % err, "fail")
        self._update_statusbar("未连接")
        if getattr(self, "link", "net") == "usb":
            self.alert("USB 连接失败：\n%s\n\n请检查：OTG 转接线插好、手机已开启 OTG、"
                       "转换器芯片为 FTDI/CP210x/CH340/PL2303 之一、并已授权该 USB 设备"
                       % err)
        else:
            self.alert("连接失败：\n%s\n\n请检查：手机与串口服务器是否同网段、"
                       "IP/端口是否正确、服务器是否为 TCP Server 模式" % err)

    def _watch_usb(self, dt):
        """拔线看门狗：USB 设备断开时自动停止连接。"""
        if self.conn is None or not self.conn.is_open():
            self.log("检测到 USB 串口已断开，自动断开连接", "fail")
            self._cancel_usb_watch()
            self._on_disconnected()
        return True

    def _cancel_usb_watch(self):
        ev = getattr(self, "_usb_event", None)
        if ev is not None:
            try:
                Clock.unschedule(ev)
            except Exception:
                pass
            self._usb_event = None

    def _disc_worker(self):
        try:
            if self.conn:
                self.conn.close()
        except Exception:
            pass
        self.conn = None
        self._cancel_usb_watch()
        Clock.schedule_once(lambda dt: self._on_disconnected(), 0)

    def _on_disconnected(self):
        self.lbl_conn.text = "● 未连接"
        self.lbl_conn.color = (0.75, 0, 0, 1)
        self.btn_conn.text = "连接"
        self._set_actions_enabled(False)
        self.log("已断开连接", "info")
        self._update_statusbar("未连接")

    def _set_actions_enabled(self, on):
        for w in self.action_widgets:
            try:
                w.disabled = not on
            except Exception:
                pass
        if not on:
            # 断开时关闭所有定时任务
            for sw in (self.sw_autoref, self.sw_cont, self.sw_hb):
                try:
                    if sw.active:
                        sw.active = False
                except Exception:
                    pass

    # ---- 指令发送（后台线程，UI 更新切回主线程）----
    def send_async(self, frame, label, parser=None, quiet=False):
        threading.Thread(target=self._send_worker,
                         args=(frame, label, parser, quiet), daemon=True).start()

    def _send_worker(self, frame, label, parser, quiet):
        if self.conn is None or not self.conn.is_open():
            self.log("尚未连接，请先点击“连接”", "fail")
            return
        tcp_mode = self.use_tcp_mbap()
        wire = frame
        txid = 0
        if tcp_mode:
            with self.lock:
                self._txid = (self._txid + 1) & 0xFFFF
                txid = self._txid
            wire = rtu_to_tcp_frame(frame, txid)
        with self.lock:
            try:
                self.conn.write(wire)
                time.sleep(0.01)
                resp = self.conn.read_frame(self.timeout, 0.05)
            except Exception as e:
                self.log("%s → 收发异常: %s" % (label, e), "fail")
                return
        if tcp_mode and resp:
            merr = tcp_resp_error(resp, txid)
            if merr:
                self.log("%s\n  TX: %s" % (label, wire.hex(" ").upper()), "tx")
                self.log("  RX: %s" % resp.hex(" ").upper(), "rx")
                self.log("  → %s" % merr, "fail")
                return
            resp = tcp_resp_to_rtu(resp)
        if quiet:
            self.log("%s  ✓" % label if resp else "%s → 无响应" % label,
                     "ok" if resp else "fail")
            return
        self.log("%s\n  TX: %s" % (label, wire.hex(" ").upper()), "tx")
        if resp:
            p = parser if parser else parse_response
            res = p(frame, resp)
            self.log("  RX: %s" % resp.hex(" ").upper(), "rx")
            self.log("  → %s" % res["msg"], "ok" if res["ok"] else "fail")
            if parser is parse_status and res["ok"]:
                Clock.schedule_once(lambda dt: self._update_status(res), 0)
            elif parser is parse_agv_status and res["ok"]:
                Clock.schedule_once(
                    lambda dt: setattr(
                        self.lbl_agv, "text",
                        AGV_STAT_MAP.get(res["val"], "--")), 0)
        else:
            self.log("  RX: (无响应)", "fail")

    def _update_status(self, res):
        self.card_sys.set_value(SYS_MAP.get(res["sys"], "未知(%d)" % res["sys"]),
                                self._sys_color(res["sys"]))
        self.card_run.set_value(RUN_MAP.get(res["run"], "未知(%d)" % res["run"]),
                                self._run_color(res["run"]))
        self.card_door.set_value(DOOR_MAP.get(res["door"], "0x%02X" % res["door"]),
                                 self._door_color(res["door"]))
        bits = decode_carin(res["carin"])
        self.card_carin.set_value(
            "0x%04X (%s)" % (res["carin"], "/".join(bits) if bits else "无"),
            "#222222")
        arrow = {1: "↑ ", 2: "↓ "}.get(res["run"], "")
        self.lbl_floor.text = "%s%d 楼" % (arrow, res["floor"])

    @staticmethod
    def _sys_color(v):
        if v == 3:
            return "#1a7a1a"
        if v in (1, 4):
            return "#c00000"
        return "#d98a00"

    @staticmethod
    def _run_color(v):
        return {0: "#888888", 1: "#1a7a1a", 2: "#1a4f8a"}.get(v, "#888888")

    @staticmethod
    def _door_color(v):
        if v in (1, 2):
            return "#1a7a1a"
        if v in (3, 4):
            return "#888888"
        return "#d98a00"

    # ---- 定时任务（自动刷新 / 持续开门 / AGV 心跳）----
    def _interval_of(self, text, default, lo, hi):
        try:
            v = float(text)
        except (ValueError, TypeError):
            return default
        return max(lo, min(hi, v))

    def on_auto_refresh_toggle(self, sw, active):
        if active:
            self.log("开启自动刷新: 每 %ss 读取一次电梯状态"
                     % self.ent_interval.text, "info")
            self._auto_refresh_tick()
        else:
            self.log("关闭自动刷新", "info")

    def _auto_refresh_tick(self, dt=None):
        if not self._alive or not self.sw_autoref.active:
            return
        if self.conn and self.conn.is_open():
            self.read_status()
        Clock.schedule_once(
            self._auto_refresh_tick,
            self._interval_of(self.ent_interval.text, 3.0, 0.5, 60))

    def toggle_cont_open(self, sw, active):
        if active:
            self.log("开启持续开门信号: 每 200 毫秒发送一次开门指令", "info")
            self._cont_open_tick()
        else:
            self.log("关闭持续开门信号", "info")

    def _cont_open_tick(self, dt=None):
        if not self._alive or not self.sw_cont.active:
            return
        if self.conn and self.conn.is_open():
            self.send_async(build_write(self.addr, REG_DOOR_CTRL, OPEN_VAL),
                            "持续开门", quiet=True)
        Clock.schedule_once(self._cont_open_tick, 0.2)

    def on_agv_hb_toggle(self, sw, active):
        if active:
            self.log("开启 AGV 心跳自动发送: 每 %ss 写一次 0x9CA6"
                     % self.ent_hb.text, "info")
            self._hb_counter = 0
            self._heartbeat_tick()
        else:
            self.log("关闭 AGV 心跳自动发送", "info")

    def _heartbeat_tick(self, dt=None):
        if not self._alive or not self.sw_hb.active:
            return
        secs = self._interval_of(self.ent_hb.text, 30.0, 1.0, 120.0)
        if self.conn and self.conn.is_open():
            self._hb_counter = (self._hb_counter + 1) % 65536 or 1
            frame = build_write(self.addr, REG_AGV_HB, self._hb_counter)
            self.send_async(frame,
                            "AGV心跳(0x9CA6=0x%04X)" % self._hb_counter,
                            quiet=True)
        Clock.schedule_once(self._heartbeat_tick, secs)

    # ---- 具体指令 ----
    def read_status(self):
        self.send_async(build_read(self.addr, REG_SYS, 5),
                        "一键读取电梯状态(0x9C41×5)", parse_status)

    def front_call(self, floor):
        self.send_async(build_write(self.addr, REG_FRONT, floor),
                        "登记 %d 楼前门指令到内呼" % floor)

    def open_door(self):
        self.send_async(build_write(self.addr, REG_DOOR_CTRL, OPEN_VAL), "开门指令")

    def close_door(self):
        self.send_async(build_write(self.addr, REG_DOOR_CTRL, CLOSE_VAL), "关门指令")

    def toggle_driver(self, sw, active):
        val = 1 if active else 0
        self.send_async(build_write(self.addr, REG_DRIVER, val),
                        "司机功能%s (0x9CA0=%d)" % ("开" if val else "关", val),
                        quiet=True)

    def enter_agv(self):
        self.send_async(build_write(self.addr, REG_AGV_CTRL, 1),
                        "进入AGV模式(0x9CA4=1)")

    def exit_agv(self):
        self.send_async(build_write(self.addr, REG_AGV_CTRL, 0),
                        "退出AGV模式(0x9CA4=0)")

    def read_agv_status(self):
        self.send_async(build_read(self.addr, REG_AGV_STAT, 1),
                        "读取AGV状态(0x9CA5)", parse_agv_status)

    # ---- 日志 ----
    def log(self, text, tag="info"):
        """线程安全：统一切回主线程渲染。"""
        Clock.schedule_once(lambda dt: self._render_log(text, tag), 0)

    def _render_log(self, text, tag):
        color = {"tx": C_TX, "rx": C_RX, "ok": C_OK,
                 "fail": C_FAIL, "info": C_INFO}.get(tag, C_INFO)
        ts = time.strftime("%H:%M:%S")
        line = "[color=%s]%s  %s[/color]" % (color, ts, esc(text))
        self._log_lines.append(line)
        if len(self._log_lines) > 400:
            self._log_lines = self._log_lines[-400:]
        self.log_label.text = "\n".join(self._log_lines)
        self._update_statusbar(None)

    def clear_log(self):
        self._log_lines = []
        self.log_label.text = ""

    def _update_statusbar(self, conn_text):
        if conn_text is not None:
            self._conn_text = conn_text
        base = getattr(self, "_conn_text", "未连接")
        self.statusbar.text = "%s  ·  站址 0x%02X  ·  最近 %s" % (
            base, self.addr, time.strftime("%H:%M:%S"))

    def alert(self, msg):
        try:
            lbl = Label(text=msg, font_name=FONT, font_size=sp(13), halign="left",
                        valign="middle")
            lbl.bind(size=lambda *a: setattr(lbl, "text_size",
                                             (a[0].width - dp(16), None)))
            Popup(title="提示", content=lbl, size_hint=(0.88, 0.36),
                  title_font=FONT, title_size=sp(15)).open()
        except Exception:
            pass


if __name__ == "__main__":
    MCTCApp().run()
