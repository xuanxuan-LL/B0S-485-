#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
从桌面版 protocol_converter_test.py 自动抽取「协议层」生成 protocol_core.py
================================================================
目的: 让 Windows 桌面版(tkinter) 与 安卓版(Kivy) 共用同一份协议实现,
      避免 CRC / 帧构建 / 解析逻辑出现两份代码而走偏。

用法: python sync_protocol.py
产物: 同目录 protocol_core.py (自动生成, 请勿手工编辑)
"""
import io
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(os.path.dirname(HERE), "protocol_converter_test.py")
DST = os.path.join(HERE, "protocol_core.py")

BANNER = '''#!/usr/bin/env python3
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
'''


def main():
    if not os.path.exists(SRC):
        print("找不到源文件: %s" % SRC)
        return 1
    with io.open(SRC, encoding="utf-8") as f:
        text = f.read()

    marker = "\nclass App"
    idx = text.find(marker)
    if idx < 0:
        print("源文件中未找到 'class App' 标记, 抽取中止")
        return 1

    core = text[:idx]
    with io.open(DST, "w", encoding="utf-8") as f:
        f.write(BANNER)
        f.write(core)

    # 统计
    n_func = core.count("\ndef ")
    n_class = core.count("\nclass ")
    print("已生成: %s" % DST)
    print("  抽取行数: %d  |  函数 %d 个  |  类 %d 个" %
          (core.count("\n"), n_func, n_class))
    return 0


if __name__ == "__main__":
    sys.exit(main())
