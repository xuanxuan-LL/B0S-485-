#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
usb_conn.py —— 安卓 USB-OTG 串口通道（手机直连 USB 转 485）
============================================================
依赖 usb4a + usbserial4a（只有打包进安卓 APK 后才存在），
因此在 Windows / 桌面环境下导入本模块不会报错，只是 USB_OK=False。

接线：
    手机(OTG) ── USB 转 RS485 转换器 ── A/B 端子 ── 协议转换板
    转换器芯片必须是下列之一（其余芯片无驱动，识别不出来）：
        FTDI FT232/FT230X(0x0403)   CP210x(0x10C4)
        CH340/CH341(0x1A86)         PL2303(0x067B)
        其它 CDC/ACM 类设备（走通用驱动）

串口参数固定 38400 / 8 / N / 1（与协议转换板一致）。

接口与 protocol_core.TcpConn 完全一致：open / close / is_open /
write / read_frame，所以上层 UI 不用区分走的是网口还是 USB。
"""
import time

try:  # 只有安卓打包环境里才有这两个库
    from usb4a import usb
    from usbserial4a import serial4a
    USB_OK = True
    USB_ERR = ""
except Exception as _e:            # 桌面 / 未打包 USB 驱动时降级
    usb = None
    serial4a = None
    USB_OK = False
    USB_ERR = "%s: %s" % (type(_e).__name__, _e)

# 芯片厂商 VID（与 usbserial4a 内部一致，仅用于显示识别结果）
VENDORS = {
    0x0403: "FTDI",
    0x10C4: "Silicon Labs (CP210x)",
    0x1A86: "QinHeng (CH340/CH341)",
    0x067B: "Prolific (PL2303)",
}

BAUDRATE = 38400
BYTESIZE = 8
PARITY = "N"
STOPBITS = 1

# 常见的 USB 转 485 转换器 VID:PID，用于把"看起来像 485 转换器"的设备排前面
COMMON_485 = {
    (0x0403, 0x6001): "FTDI FT232 串口",
    (0x0403, 0x6015): "FTDI FT230X/231X 串口",
    (0x1A86, 0x7523): "CH340 串口",
    (0x1A86, 0x5523): "CH341 串口",
    (0x10C4, 0xEA60): "CP210x 串口",
    (0x067B, 0x2303): "PL2303 串口",
}


def driver_of(vid, pid):
    """按 VID/PID 判断会用哪个驱动（与 usbserial4a.get_serial_port 同逻辑）。"""
    if vid in VENDORS:
        return VENDORS[vid]
    return "CDC/ACM (通用)"


def describe(vid, pid, manufacturer="", product=""):
    """给设备起一个人类可读的名字。"""
    known = COMMON_485.get((vid, pid))
    name = known or product or manufacturer or "未知设备"
    return name


def list_devices():
    """枚举当前挂着的 USB 设备。

    返回 (devices, err):
      devices: [{name, vid, pid, vendor, product, driver, label, is_485}]
      err:     出错原因（成功时为 ""）
    """
    if not USB_OK:
        return [], "非安卓环境或 APK 未打包 USB 驱动（%s）" % USB_ERR
    try:
        raw = usb.get_usb_device_list()
    except Exception as e:
        return [], "枚举 USB 设备失败：%s" % e

    out = []
    for d in raw:
        try:
            name = d.getDeviceName()
            vid = d.getVendorId()
            pid = d.getProductId()
        except Exception:
            continue
        try:
            manufacturer = d.getManufacturerName() or ""
        except Exception:       # Android 9 以下没有这些 getter
            manufacturer = ""
        try:
            product = d.getProductName() or ""
        except Exception:
            product = ""
        drv = driver_of(vid, pid)
        label = "%04X:%04X %s [%s]" % (vid, pid, describe(vid, pid,
                                                           manufacturer, product),
                                       drv)
        out.append({
            "name": name, "vid": vid, "pid": pid,
            "vendor": manufacturer, "product": product,
            "driver": drv, "label": label,
            "is_485": (vid, pid) in COMMON_485,
        })
    # 已知 485 转换器排前面，其余按名称排
    out.sort(key=lambda x: (not x["is_485"], x["label"]))
    return out, ""


def find_device(name, devices=None):
    """按 device_name 在列表里找设备。"""
    if devices is None:
        devices, _ = list_devices()
    for d in devices:
        if d["name"] == name:
            return d
    return None


def has_permission(name):
    """是否已经拿到该设备的 USB 访问权限。"""
    if not USB_OK:
        return False
    dev = usb.get_usb_device(name)
    if not dev:
        return False
    try:
        return bool(usb.has_usb_permission(dev))
    except Exception:
        return False


def request_permission(name):
    """发起权限申请（异步，系统会弹窗）。返回是否已弹窗。"""
    if not USB_OK:
        return False
    dev = usb.get_usb_device(name)
    if not dev:
        return False
    try:
        usb.request_usb_permission(dev)
        return True
    except Exception:
        return False


class UsbConn(object):
    """USB-OTG 串口通道，接口与 protocol_core.TcpConn / SerialConn 一致。"""

    def __init__(self, device_name, baud=BAUDRATE):
        self.device_name = device_name
        self.baud = baud
        self.port = None

    def open(self):
        """打开串口。

        注意：usbserial4a 在没有 USB 权限时 open() 不会报错、也不会真正打开，
        只弹授权框。调用方必须检查 is_open() 并提示用户再点一次连接。
        """
        if not USB_OK:
            raise RuntimeError("当前环境不支持 USB 串口（%s）" % USB_ERR)
        self.port = serial4a.get_serial_port(
            self.device_name, self.baud, BYTESIZE, PARITY, STOPBITS,
            timeout=0.6)

    def close(self):
        if self.port is not None:
            try:
                self.port.close()
            except Exception:
                pass
        self.port = None

    def is_open(self):
        try:
            return self.port is not None and bool(self.port.is_open)
        except Exception:
            return False

    def write(self, data):
        if not self.is_open():
            raise RuntimeError("USB 串口未打开")
        self.port.write(data)

    def read_frame(self, timeout=0.6, inter=0.04):
        """读一帧：首字节阻塞等 timeout 秒，之后以字节间超时判定帧结束。"""
        if not self.is_open():
            return b""
        p = self.port
        p.timeout = timeout
        first = p.read(1)
        if not first:
            return b""
        buf = bytearray(first)
        p.timeout = inter
        deadline = time.time() + 0.5
        while len(buf) < 256:
            chunk = p.read(1)
            if not chunk:
                break
            buf += chunk
            if time.time() > deadline:
                break
        return bytes(buf)

    def reset_buffers(self):
        try:
            if self.port is not None:
                self.port.reset_input_buffer()
                self.port.reset_output_buffer()
        except Exception:
            pass
