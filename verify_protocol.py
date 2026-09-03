#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""安卓协议层端到端验证（不依赖 Kivy / tkinter，无硬件即可跑）。

期望值只取「协议文档 / 使用说明书里印出来的固定帧」，不臆造 CRC；
其余帧用 CRC 自洽（crc16_modbus(frame)==0）+ 独立实现交叉校验。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import protocol_core as P

ok_n = fail_n = 0

def chk(name, got, want):
    global ok_n, fail_n
    if got == want:
        ok_n += 1
        print("  [OK]   %-34s %s" % (name, got))
    else:
        fail_n += 1
        print("  [FAIL] %-34s got=%r want=%r" % (name, got, want))

def hx(b):
    return " ".join("%02X" % x for x in b)

def crc_ref(data: bytes) -> int:
    """独立实现的 CRC16-Modbus，用于交叉校验（不复用被测代码里的实现）。"""
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc

print("=" * 72)
print("1) 帧构建 vs 文档固定值（只比对说明书里印出来的帧）")
print("=" * 72)
DOC_FRAMES = [
    ("读取物理楼层",   P.build_read(1, P.REG_FLOOR, 1),       "01 03 9C 45 00 01 BB 8F"),
    ("登记5楼前门",    P.build_write(1, P.REG_FRONT, 5),      "01 06 9C 99 00 05 B7 B6"),
    ("开门",           P.build_write(1, P.REG_DOOR_CTRL, P.OPEN_VAL),  "01 06 9C 56 00 03 07 8B"),
    ("关门",           P.build_write(1, P.REG_DOOR_CTRL, P.CLOSE_VAL), "01 06 9C 56 00 04 46 49"),
    ("读系统/运行/门", P.build_read(1, P.REG_SYS, 3),         "01 03 9C 41 00 03 7B 8F"),
    ("进入AGV模式",    P.build_write(1, P.REG_AGV_CTRL, 1),   "01 06 9C A4 00 01 27 B9"),
    ("退出AGV模式",    P.build_write(1, P.REG_AGV_CTRL, 0),   "01 06 9C A4 00 00 E6 79"),
]
for name, frame, doc in DOC_FRAMES:
    chk(name, hx(frame), doc.upper())

print()
print("=" * 72)
print("2) CRC 自洽 + 独立实现交叉校验（覆盖全部指令帧）")
print("=" * 72)
frames = {
    "一键读状态(9C41/5)": P.build_read(1, P.REG_SYS, 5),
    "1层前门": P.build_write(1, P.REG_FRONT, 1),
    "2层前门": P.build_write(1, P.REG_FRONT, 2),
    "3层前门": P.build_write(1, P.REG_FRONT, 3),
    "4层前门": P.build_write(1, P.REG_FRONT, 4),
    "5层前门": P.build_write(1, P.REG_FRONT, 5),
    "司机有效": P.build_write(1, P.REG_DRIVER, 1),
    "司机取消": P.build_write(1, P.REG_DRIVER, 0),
    "持续开门(=开门帧)": P.build_write(1, P.REG_DOOR_CTRL, P.OPEN_VAL),
    "AGV心跳": P.build_write(1, P.REG_AGV_HB, 1),
    "读AGV状态": P.build_read(1, P.REG_AGV_STAT, 1),
}
for name, f in frames.items():
    low = f[-2] | (f[-1] << 8)      # 帧内 CRC（低位在前）
    chk("CRC自洽 " + name, P.crc16_modbus(f), 0)
    chk("CRC交叉 " + name, low, crc_ref(f[:-2]))

print()
print("=" * 72)
print("3) 一键读取 5 寄存器解析（系统/运行/门/轿内/楼层）")
print("=" * 72)
req = P.build_read(1, P.REG_SYS, 5)
body = bytes([0x03, 0x0A,
              0x00, 0x03,   # 系统=3 正常
              0x00, 0x01,   # 运行=1 上运行
              0x00, 0x02,   # 门=2 开门到位保持
              0x10, 0x00,   # 轿内 bit12 = 司机
              0x00, 0x03])  # 楼层=3
resp = bytes([0x01]) + body + P.crc_bytes(bytes([0x01]) + body)
st = P.parse_status(req, resp)
chk("ok 标志", st["ok"], True)
chk("系统状态", P.SYS_MAP.get(st["sys"]), "正常状态")
chk("运行状态", P.RUN_MAP.get(st["run"]), "上运行")
chk("门状态", P.DOOR_MAP.get(st["door"]), "开门到位保持")
chk("轿内开关位", "/".join(P.decode_carin(st["carin"])), "司机")
chk("当前楼层", st["floor"], 3)
chk("楼层显示名", P.floor_name(st["floor"]), "3 楼")

print()
print("=" * 72)
print("4) AGV 状态解析")
print("=" * 72)
for val, want in ((0, "正常状态"), (1, "等待进入 AGV 状态"), (2, "AGV 运行状态")):
    rq = P.build_read(1, P.REG_AGV_STAT, 1)
    bd = bytes([0x03, 0x02, (val >> 8) & 0xFF, val & 0xFF])
    rs = bytes([0x01]) + bd + P.crc_bytes(bytes([0x01]) + bd)
    r = P.parse_agv_status(rq, rs)
    chk("AGV状态=%d" % val, P.AGV_STAT_MAP.get(r["val"]) if r["ok"] else r["msg"], want)

print()
print("=" * 72)
print("5) Modbus TCP(MBAP) 全链路：封包 → 响应 → 还原 RTU → 解析")
print("=" * 72)
TID = 0x1234
rtu = P.build_read(1, P.REG_SYS, 5)
tcp = P.rtu_to_tcp_frame(rtu, TID)
chk("RTU→TCP 完整封装", hx(tcp), "12 34 00 00 00 06 01 03 9C 41 00 05")
chk("MBAP 事务标识", hx(tcp[0:2]), "12 34")
chk("MBAP 协议标识", hx(tcp[2:4]), "00 00")
chk("MBAP 长度 = UnitID+PDU", int.from_bytes(tcp[4:6], "big"), len(rtu) - 3 + 1)
chk("MBAP Unit ID = 站址", tcp[6], 1)
chk("PDU 已去掉 CRC", hx(tcp[7:]), "03 9C 41 00 05")

data = bytes([0x00, 0x03, 0x00, 0x01, 0x00, 0x02, 0x00, 0x00, 0x00, 0x05])
payload = bytes([0x01, 0x03, 0x0A]) + data
tcp_resp = bytes([0x12, 0x34, 0x00, 0x00, len(payload) >> 8, len(payload) & 0xFF]) + payload
chk("MBAP 头校验通过", P.tcp_resp_error(tcp_resp, TID), None)
back = P.tcp_resp_to_rtu(tcp_resp)
chk("还原 RTU 站址", hx(back[0:1]), "01")
chk("还原 RTU 功能码/字节数", hx(back[1:3]), "03 0A")
chk("还原帧长度 = PDU+CRC", len(back), len(payload) + 2)
chk("还原后 CRC 自洽", P.crc16_modbus(back), 0)
st2 = P.parse_status(rtu, back)
chk("TCP 路径 系统状态", P.SYS_MAP.get(st2["sys"]) if st2["ok"] else st2["msg"], "正常状态")
chk("TCP 路径 运行状态", P.RUN_MAP.get(st2["run"]) if st2["ok"] else st2["msg"], "上运行")
chk("TCP 路径 门状态", P.DOOR_MAP.get(st2["door"]) if st2["ok"] else st2["msg"], "开门到位保持")
chk("TCP 路径 楼层", st2["floor"], 5)
chk("TID 错配被检出", P.tcp_resp_error(tcp_resp, 0x9999) is not None, True)
err_resp = bytes([0x12, 0x34, 0x00, 0x00, 0x00, 0x03, 0x01, 0x83, 0x02])
chk("异常响应(0x83)被检出", P.tcp_resp_error(err_resp, TID) is not None, True)

print()
print("=" * 72)
print("结果: %d 通过 / %d 失败" % (ok_n, fail_n))
print("=" * 72)
sys.exit(1 if fail_n else 0)
