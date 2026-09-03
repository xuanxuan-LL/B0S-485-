#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
protocol_core.py —— 协议层（自动生成，请勿手工编辑）
====================================================
由 sync_protocol.py 从上级目录的 protocol_converter_test.py 抽取生成。
Windows 桌面版(tkinter) 与 安卓版(Kivy) 共用本文件, 保证协议实现单一来源。

抽取范围: 文件开头 ~ "class App" 之前
  包含: CRC16/Modbus、读/写帧构建、MBAP 封装与解封装、响应解析、
        寄存器常量、状态映射表、连接类(SerialConn / TcpConn / MockSerial)

如需修改协议, 请改 protocol_converter_test.py 后重新运行:
    python sync_protocol.py
"""
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
协议转换板 1-5 层 交互式测试软件（串口 / 串口服务器 双模式 + AGV）
============================================================
协议: MCTC-KZ-B0S 开放协议 / Modbus-RTU (站址 0x01, 38400/8/N/1, CRC16/Modbus)
依据文档:
  * MCTC-KZ-B0S通信协议-开放协议V1.7.pdf        (寄存器/指令定义 + AGV 方案附录)
  * 协议转换板1-5层测试指令.docx               (测试指令帧)
  * NA11x用户手册_V1.8.pdf  (串口服务器 亿佰特 NA11x, 默认 192.168.3.7:8887)
  * C2000-B1-THE0101-BB6使用说明书 (串口服务器 康耐德 C2000, 默认 192.168.4.1:8000)

两种连接方式（界面可切换）:
  1) 本地串口:   直接选 COM 口连接 485 设备
  2) 网络串口服务器: 通过 NA11x / C2000 等串口服务器, 以 TCP 客户端连到服务器 IP:端口
                    - 服务器保持默认 TCP Server / 透传(透明)模式, 串口参数设为 38400/8/N/1
                    - 两种服务器默认参数不同, 可在“串口服务器类型”下拉中一键选择预设
                    - 两种方式发送的 Modbus-RTU 帧完全相同, 只是传输通道不同

传输协议（仅网络模式可选，本地串口固定 Modbus RTU）:
  1) Modbus RTU 透传(默认): 直接发原始 RTU 帧(带 CRC16)，串口服务器保持透明/透传模式
  2) Modbus TCP 网关:       发送 MBAP(7B) + PDU，无 CRC，站址放进 Unit ID
                            - 需先在串口服务器开启 Modbus 网关/协议转换
                              (NA11x: 简单协议转换；C2000: Modbus TCP/RTU 转换)
                            - 服务器自动完成 TCP<->RTU 互转，端口不变(8887 / 8000)
                            - 程序侧仅在收发层做 MBAP 封装/解封装，解析逻辑完全复用

功能:
  * 连接方式选择（本地串口 / 网络串口服务器），服务器类型预设 + 端口/IP 自定义
  * 一键读取电梯状态 -> 读 0x9C41~0x9C45, 分别显示 系统/运行/门/轿内开关/楼层
      - 支持“自动刷新”开关 + 刷新间隔(秒) 设置
  * 1-5 层前门按键    -> 登记 N 楼前门指令到内呼 (0x06 0x9C99 0x000N)
  * 开关门按键        -> 开门 (0x06 0x9C56 0x0003) / 关门 (0x06 0x9C56 0x0004)
  * 司机功能开关      -> 勾选写 0x9CA0=1(司机输入有效) / 取消写 0x9CA0=0
  * 持续开门信号开关  -> 勾选后每 200ms 循环发送开门指令 (0x06 0x9C56 0x0003)
  * AGV 控制          -> 进入/退出 AGV 模式 (0x06 0x9CA4 0/1)、读取 AGV 状态 (0x03 0x9CA5)
                         AGV 心跳自动发送开关 + 间隔(秒) 调整 (0x06 0x9CA6 任意值)
  * 显示当前楼层（大号数字）
  * 显示接收数据（TX/RX 原始帧 + 解析说明，带时间戳）

依赖: pip install pyserial   (tkinter 为标准库，Windows 自带)
运行: python protocol_converter_test.py      （无硬件可用 --selftest 自检）
"""
import os
import sys
import time
import socket
import threading
try:
    import serial
    import serial.tools.list_ports
except ImportError:
    serial = None

# 注意: tkinter 必须延迟/容错导入, 以便本模块的协议层(CRC/帧构建/解析/连接类)
# 能在无 GUI 环境(如安卓版 Kivy APP)中被 import 复用。
try:
    import tkinter as tk
    from tkinter import ttk, messagebox, scrolledtext
except ImportError:
    tk = None          # 无图形界面环境(如 --selftest 或安卓端)仍可复用协议层
    ttk = None
    messagebox = None
    scrolledtext = None

APP_DIR = os.path.dirname(os.path.abspath(__file__))

# 串口服务器类型预设（透明模式 / TCP Server）
SERVER_PRESETS = {
    "NA11x (亿佰特)": ("192.168.3.7", "8887"),
    "C2000 (康耐德)": ("192.168.4.1", "8000"),
}


# ---------------------------------------------------------------------------
# CRC16 / Modbus  (poly=0xA001, init=0xFFFF, 低字节在前)
# ---------------------------------------------------------------------------
def crc16_modbus(data: bytes) -> int:
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            if crc & 0x0001:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return crc & 0xFFFF


def crc_bytes(data: bytes) -> bytes:
    c = crc16_modbus(data)
    return bytes([c & 0xFF, (c >> 8) & 0xFF])


def build_write(addr: int, reg: int, value: int) -> bytes:
    """构造写单寄存器帧 (0x06)。"""
    frame = bytes([addr, 0x06, (reg >> 8) & 0xFF, reg & 0xFF,
                   (value >> 8) & 0xFF, value & 0xFF])
    return frame + crc_bytes(frame)


def build_read(addr: int, reg: int, count: int) -> bytes:
    """构造读寄存器帧 (0x03)。"""
    frame = bytes([addr, 0x03, (reg >> 8) & 0xFF, reg & 0xFF,
                   (count >> 8) & 0xFF, count & 0xFF])
    return frame + crc_bytes(frame)


# ---------------------------------------------------------------------------
# Modbus TCP (MBAP) 封装 / 解封装
#   串口服务器开启 Modbus 网关(TCP<->RTU 转换)时使用:
#   线上传输 MBAP(7B) + PDU(功能码+数据), 无 CRC; 站址放进 Unit ID
# ---------------------------------------------------------------------------
def rtu_to_tcp_frame(rtu: bytes, txid: int) -> bytes:
    """把 Modbus-RTU 帧(含 CRC)转成 Modbus TCP 帧(MBAP + PDU)。"""
    if len(rtu) < 4:
        return rtu
    uid = rtu[0]                 # 站址 -> Unit ID
    pdu = rtu[1:-2]              # 功能码 + 数据（去掉站址与 CRC）
    length = 1 + len(pdu)        # Unit ID(1) + PDU
    return (bytes([(txid >> 8) & 0xFF, txid & 0xFF, 0x00, 0x00,
                   (length >> 8) & 0xFF, length & 0xFF, uid]) + pdu)


def tcp_resp_to_rtu(resp: bytes) -> bytes:
    """把 Modbus TCP 响应(MBAP + PDU)转成等效 RTU 帧(去掉 MBAP, 补 CRC),
    以便复用现有的 RTU 解析与 CRC 校验逻辑。"""
    if len(resp) < 8:
        return b""
    body = resp[6:]              # Unit ID + 功能码 + 数据
    return body + crc_bytes(body)


def tcp_resp_error(resp: bytes, txid: int):
    """校验 MBAP 头, 返回 None 表示正常, 否则返回错误消息。"""
    if len(resp) < 8:
        return "TCP 响应过短（%d 字节）" % len(resp)
    got_txid = (resp[0] << 8) | resp[1]
    proto = (resp[2] << 8) | resp[3]
    length = (resp[4] << 8) | resp[5]
    if proto != 0:
        return "MBAP 协议标识错误 0x%04X" % proto
    if length != len(resp) - 6:
        return "MBAP 长度不符（声明 %d, 实际 %d）" % (length, len(resp) - 6)
    if got_txid != txid:
        return "事务标识不匹配（发 0x%04X / 收 0x%04X）" % (txid, got_txid)
    # Modbus 异常响应: 功能码最高位置 1 (0x83 / 0x86 ...), 第 8 字节起为错误码
    fc = resp[7] if len(resp) > 7 else 0
    if fc & 0x80:
        exc = resp[8] if len(resp) > 8 else 0
        MODBUS_EXC = {1: "非法功能", 2: "非法数据地址", 3: "非法数据值",
                      4: "从站设备故障", 5: "确认(需等待)", 6: "从站设备忙",
                      8: "存储奇偶性错误", 10: "网关路径不可用",
                      11: "网关目标无响应"}
        return "设备返回异常 功能码=0x%02X 错误码=0x%02X (%s)" % (
            fc, exc, MODBUS_EXC.get(exc, "未知"))
    return None


# ---------------------------------------------------------------------------
# 协议寄存器 / 指令定义（依据 MCTC-KZ-B0S 开放协议 V1.7）
# ---------------------------------------------------------------------------
REG_SYS = 0x9C41       # 系统状态
REG_RUN = 0x9C42       # 运行状态 0:停梯 1:上运行 2:下运行
REG_DOOR = 0x9C43      # 门状态
REG_CARIN = 0x9C44     # 轿内开关输入状态 (bit 位)
REG_FLOOR = 0x9C45     # 当前(物理)楼层
REG_FRONT = 0x9C99     # 登记 N 楼前门指令到内呼
REG_DOOR_CTRL = 0x9C56 # 开门/关门控制
OPEN_VAL = 0x0003
CLOSE_VAL = 0x0004
REG_DRIVER = 0x9CA0   # 司机控制 0:司机输入取消 1:司机输入有效 (R/W)
# AGV 相关
REG_AGV_CTRL = 0x9CA4  # AGV 控制请求 0:退出 1:进入 (W)
REG_AGV_STAT = 0x9CA5  # AGV 状态查询 0正常 1等待进入 2AGV运行 (R)
REG_AGV_HB = 0x9CA6    # AGV 心跳 (W, 任意值, 须≤2min写一次, 建议≤30s)

FLOOR_NAMES = {1: "1 楼", 2: "2 楼", 3: "3 楼", 4: "4 楼", 5: "5 楼", 6: "6 楼"}

# 解码映射
SYS_MAP = {0: "故障状态", 1: "消防状态", 2: "其他非服务状态", 3: "正常状态", 4: "地震状态"}
RUN_MAP = {0: "停梯", 1: "上运行", 2: "下运行"}
DOOR_MAP = {0: "未知状态", 1: "开门过程", 2: "开门到位保持",
            3: "关门过程", 4: "关门到位保持"}
CARIN_BITS = [
    ("前门光幕", 0), ("后门光幕", 1), ("前门开门到位", 2), ("后门开门到位", 3),
    ("前门关门到位", 4), ("后门关门到位", 5), ("满载开关", 6), ("超载开关", 7),
    ("保留", 8), ("保留", 9), ("保留", 10), ("直达", 11), ("司机", 12),
    ("换向", 13), ("独立", 14), ("二次消防", 15),
]
AGV_STAT_MAP = {0: "正常状态", 1: "等待进入 AGV 状态", 2: "AGV 运行状态"}


def decode_carin(v: int):
    return [name for name, bit in CARIN_BITS if v & (1 << bit)]


def floor_name(floor: int):
    return FLOOR_NAMES.get(floor, "%d 楼" % floor if 1 <= floor <= 64 else "未知(%d)" % floor)


# ---------------------------------------------------------------------------
# 串口读取：首字节阻塞等待，之后以帧间隔(字节间超时)判定一帧结束
# ---------------------------------------------------------------------------
def read_response(ser, timeout_s, inter_byte_s=0.04):
    ser.timeout = timeout_s
    first = ser.read(1)
    if not first:
        return b""
    buf = bytearray(first)
    ser.timeout = inter_byte_s
    while True:
        chunk = ser.read(1)
        if not chunk:
            break
        buf += chunk
        if len(buf) >= 256:
            break
    return bytes(buf)


# ---------------------------------------------------------------------------
# 响应解析
# ---------------------------------------------------------------------------
def validate_basic(resp: bytes, req: bytes):
    """返回 None 表示通过，否则返回错误消息。"""
    if not resp:
        return "无响应（超时）"
    if len(resp) < 5:
        return "响应过短（%d 字节）" % len(resp)
    calc = crc16_modbus(resp[:-2])
    got = resp[-2] | (resp[-1] << 8)
    if calc != got:
        return "CRC 校验错误"
    if resp[1] & 0x80:
        return "异常响应 错误码 0x%02X" % (resp[2] if len(resp) > 2 else 0)
    if resp[1] != req[1]:
        return "功能码不匹配"
    return None


def parse_response(req: bytes, resp: bytes):
    """返回 dict: {ok, kind, floor, door, msg}"""
    err = validate_basic(resp, req)
    if err:
        return {"ok": False, "kind": None, "floor": None, "door": None, "msg": err}

    if req[1] == 0x06:  # 写单寄存器 -> 回显
        if len(resp) != 8 or resp[:6] != req[:6]:
            return {"ok": False, "kind": "write", "floor": None, "door": None,
                    "msg": "写回帧与发送不一致"}
        return {"ok": True, "kind": "write", "floor": None, "door": None,
                "msg": "写入成功（从机回显一致）"}

    if req[1] == 0x03:  # 读寄存器
        bc = resp[2]
        if len(resp) != 5 + bc:
            return {"ok": False, "kind": "read", "floor": None, "door": None,
                    "msg": "读响应长度错误（字节数 %d）" % bc}
        data = resp[3:3 + bc]
        if req[2] == (REG_FLOOR >> 8) and req[3] == (REG_FLOOR & 0xFF) and bc == 2:
            floor = data[1]
            return {"ok": True, "kind": "floor", "floor": floor, "door": None,
                    "msg": "当前楼层 = %s" % floor_name(floor)}
        if req[2] == (REG_DOOR >> 8) and req[3] == (REG_DOOR & 0xFF) and bc == 2:
            val = data[1]
            return {"ok": True, "kind": "door", "floor": None, "door": val,
                    "msg": "门状态 = %s" % DOOR_MAP.get(val, "0x%02X" % val)}
        return {"ok": True, "kind": "read", "floor": None, "door": None,
                "msg": "读取成功 数据=%s" % data.hex(" ").upper()}
    return {"ok": True, "kind": "other", "floor": None, "door": None,
            "msg": "响应有效"}


def parse_status(req: bytes, resp: bytes):
    """一键读取电梯状态: 读 0x9C41 起 5 个寄存器。
    返回 dict: {ok, sys, run, door, carin, floor, msg}"""
    err = validate_basic(resp, req)
    if err:
        return {"ok": False, "sys": None, "run": None, "door": None,
                "carin": None, "floor": None, "msg": err}
    if req[1] != 0x03:
        return {"ok": False, "sys": None, "run": None, "door": None,
                "carin": None, "floor": None, "msg": "功能码不匹配"}
    bc = resp[2]
    if bc != 10:
        return {"ok": False, "sys": None, "run": None, "door": None,
                "carin": None, "floor": None,
                "msg": "状态响应字节数应为 10, 实际 %d" % bc}
    data = resp[3:3 + bc]
    regs = [(data[i * 2] << 8) | data[i * 2 + 1] for i in range(5)]
    sys_s, run_s, door_s, carin, floor = regs
    msg = ("系统状态=%s | 运行状态=%s | 门状态=%s | 轿内开关=0x%04X(%s) | 当前楼层=%s"
           % (SYS_MAP.get(sys_s, "未知(%d)" % sys_s),
              RUN_MAP.get(run_s, "未知(%d)" % run_s),
              DOOR_MAP.get(door_s, "0x%02X" % door_s),
              carin, "/".join(decode_carin(carin)) or "无",
              floor_name(floor)))
    return {"ok": True, "sys": sys_s, "run": run_s, "door": door_s,
            "carin": carin, "floor": floor, "msg": msg}


def parse_agv_status(req: bytes, resp: bytes):
    """读取 AGV 状态 (0x9CA5)。返回 dict: {ok, val, msg}"""
    err = validate_basic(resp, req)
    if err:
        return {"ok": False, "val": None, "msg": err}
    if req[1] != 0x03:
        return {"ok": False, "val": None, "msg": "功能码不匹配"}
    bc = resp[2]
    if bc != 2:
        return {"ok": False, "val": None, "msg": "长度错误（字节数 %d）" % bc}
    val = (resp[3] << 8) | resp[4]
    return {"ok": True, "val": val, "msg": "AGV 状态 = %s" % AGV_STAT_MAP.get(val, "0x%02X" % val)}


# ---------------------------------------------------------------------------
# 传输通道抽象（本地串口 / 网络串口服务器）
# ---------------------------------------------------------------------------
class BaseConn:
    def open(self):
        raise NotImplementedError

    def close(self):
        raise NotImplementedError

    def is_open(self):
        raise NotImplementedError

    def write(self, data: bytes):
        raise NotImplementedError

    def read_frame(self, timeout, inter=0.04) -> bytes:
        raise NotImplementedError


class SerialConn(BaseConn):
    def __init__(self, port, baud):
        self.port = port
        self.baud = baud
        self.ser = None

    def open(self):
        self.ser = serial.Serial(port=self.port, baudrate=self.baud, bytesize=8,
                                 stopbits=1, parity="N", timeout=0.6)

    def close(self):
        if self.ser:
            try:
                self.ser.close()
            except Exception:
                pass
        self.ser = None

    def is_open(self):
        return self.ser is not None and self.ser.is_open

    def write(self, data):
        self.ser.write(data)

    def read_frame(self, timeout, inter=0.04):
        return read_response(self.ser, timeout, inter)


class TcpConn(BaseConn):
    """连接串口服务器(NA11x / C2000 等)的 TCP 端口, 透明转发 Modbus-RTU 帧。"""

    def __init__(self, host, port):
        self.host = host
        self.port = port
        self.sock = None

    def open(self):
        self.sock = socket.create_connection((self.host, self.port), timeout=3.0)

    def close(self):
        if self.sock:
            try:
                self.sock.close()
            except Exception:
                pass
        self.sock = None

    def is_open(self):
        return self.sock is not None

    def write(self, data):
        self.sock.sendall(data)

    def read_frame(self, timeout, inter=0.05):
        self.sock.settimeout(timeout)
        try:
            first = self.sock.recv(1)
        except socket.timeout:
            return b""
        if not first:
            return b""
        buf = bytearray(first)
        self.sock.settimeout(inter)
        while True:
            try:
                chunk = self.sock.recv(1)
            except socket.timeout:
                break
            if not chunk:
                break
            buf += chunk
            if len(buf) >= 256:
                break
        return bytes(buf)


# ---------------------------------------------------------------------------
# 模拟从机（仅 --selftest 使用，无需真实硬件）
# ---------------------------------------------------------------------------
class MockSerial(BaseConn):
    def __init__(self, *a, **k):
        self._last = b""
        self._buf = bytearray()

    def open(self):
        pass

    def close(self):
        pass

    def is_open(self):
        return True

    @staticmethod
    def sim_val(addr):
        return {
            REG_SYS: 0x0003,    # 正常状态
            REG_RUN: 0x0001,    # 上运行
            REG_DOOR: 0x0002,   # 开门到位保持
            REG_CARIN: 0x0045,  # BIT0前门光幕 BIT2前门开门到位 BIT6满载
            REG_FLOOR: 0x0003,  # 3 楼
            REG_AGV_STAT: 0x0002,  # AGV 运行状态
        }.get(addr, 0x0001)

    def write(self, data):
        self._last = bytes(data)
        req = self._last
        resp = bytearray()
        if len(req) >= 8 and req[1] == 0x06:
            resp = bytearray(req)            # 写指令 -> 回显
        elif len(req) >= 8 and req[1] == 0x03:
            reg = (req[2] << 8) | req[3]
            cnt = (req[4] << 8) | req[5]
            data = bytearray()
            for i in range(cnt):
                val = self.sim_val(reg + i)
                data += bytes([(val >> 8) & 0xFF, val & 0xFF])
            resp = bytearray([req[0], 0x03, len(data)]) + data
            resp += crc_bytes(bytes(resp))
        self._buf = resp

    def read(self, n):
        if not self._buf:
            time.sleep(0.03)
            return b""
        out = bytes(self._buf[:n])
        self._buf = self._buf[n:]
        time.sleep(0.001)
        return out

    def read_frame(self, timeout, inter=0.04):
        time.sleep(0.005)
        return bytes(self._buf)


# ===========================================================================
#  GUI
# ===========================================================================