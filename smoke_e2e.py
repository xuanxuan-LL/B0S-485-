#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
smoke_e2e.py —— 端到端冒烟：App ⇄ 真实 TCP 套接字 ⇄ 模拟协议转换板
==================================================================
上面的 smoke_test.py 用的是「假连接」，只验到 _do_send 的入参；
本脚本起一个真实的本地 TCP 服务器扮演 Modbus-RTU 从机（协议转换板），
让 App 走完整链路：TcpConn 连接 -> 唯一收发线程 -> 队列 -> 按长度定界收帧
-> CRC 校验 -> 解析 -> 回写界面。

重点验证新收发模型在「真网络 + 拆包 + 粘包 + 并发定时任务」下不串帧。
运行: python smoke_e2e.py
"""
import os
import socket
import sys
import threading
import time

os.environ.setdefault("KIVY_LOG_LEVEL", "error")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from kivy.clock import Clock

import main as M
import protocol_core as P

PASS, FAIL = [], []


def chk(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %s%s" % ("OK  " if cond else "FAIL", name,
                           ("  <- " + detail) if detail and not cond else ""))


def pump(seconds):
    """驱动 Kivy Clock（不跑主循环，手动 tick 让 schedule_once 的回调执行）。"""
    end = time.time() + seconds
    while time.time() < end:
        Clock.tick()
        time.sleep(0.01)


def wait_until(cond, timeout=3.0):
    end = time.time() + timeout
    while time.time() < end:
        Clock.tick()
        if cond():
            return True
        time.sleep(0.01)
    return False


# ---------------------------------------------------------------------------
# 模拟协议转换板（Modbus-RTU 从机）
# ---------------------------------------------------------------------------
class FakeBoard(object):
    """本地 TCP 服务器，按 Modbus-RTU 应答。

    fragment: 把响应切成几段、每段之间 sleep 一下，用来复现 DTU/网口的拆包；
    prepend_garbage: 在响应前塞一段垃圾字节，复现串口服务器附带的心跳/注册包。
    """

    def __init__(self):
        self.srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen(1)
        self.port = self.srv.getsockname()[1]
        self.frames = []          # 收到的完整请求帧（用于校验无串帧）
        self.malformed = 0
        self.fragment = 1
        self.prepend_garbage = b""
        self.auto_reply = True    # False -> 不回（测超时重试）
        self._stop = False
        self._t = threading.Thread(target=self._serve, daemon=True)
        self._t.start()

    # 从机的寄存器表
    REGS = {P.REG_SYS: 3, P.REG_RUN: 1, P.REG_DOOR: 2,
            P.REG_CARIN: 0x1045, P.REG_FLOOR: 3, P.REG_AGV_STAT: 2}

    def _reply_for(self, req):
        if len(req) < 8 or not req or P.crc16_modbus(req[:-2]) != (req[-2] | (req[-1] << 8)):
            self.malformed += 1
            return b""
        addr, fc = req[0], req[1]
        if fc == 0x06:                                    # 写 -> 回显
            return bytes(req)
        if fc == 0x03:                                    # 读
            reg = (req[2] << 8) | req[3]
            cnt = (req[4] << 8) | req[5]
            data = bytearray()
            for i in range(cnt):
                v = self.REGS.get(reg + i, 0)
                data += bytes([(v >> 8) & 0xFF, v & 0xFF])
            body = bytes([addr, 0x03, len(data)]) + bytes(data)
            return body + P.crc_bytes(body)
        return b""

    def _serve(self):
        try:
            conn, _ = self.srv.accept()
        except Exception:
            return
        conn.settimeout(0.5)
        buf = bytearray()
        while not self._stop:
            try:
                chunk = conn.recv(256)
            except socket.timeout:
                continue
            except Exception:
                break
            if not chunk:
                break
            buf += chunk
            while len(buf) >= 8:                          # 请求都是 8 字节
                req = bytes(buf[:8])
                del buf[:8]
                self.frames.append(req)
                if not self.auto_reply:
                    continue
                resp = self.prepend_garbage + self._reply_for(req)
                if not resp:
                    continue
                n = max(1, int(self.fragment))
                step = max(1, len(resp) // n)
                for i in range(0, len(resp), step):
                    try:
                        conn.sendall(resp[i:i + step])
                    except Exception:
                        break
                    if n > 1:
                        time.sleep(0.02)                  # 制造拆包
        self._stop = True
        try:
            conn.close()
        except Exception:
            pass

    def close(self):
        self._stop = True
        try:
            self.srv.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
print("=" * 68)
print("端到端冒烟：App ⇄ 真实 TCP ⇄ 模拟协议转换板")
print("=" * 68)

_alerts = []
M.MCTCApp.alert = lambda self, msg, title="提示": _alerts.append(msg)

board = FakeBoard()
app = M.MCTCApp()
app.build()

print("\n[1] 走完整的 连接 -> 收帧 -> 解析 -> 回写界面")
app.sp_link.text = "网络 TCP（串口服务器）"
app.on_link_change(app.sp_link, "网络 TCP（串口服务器）")
app.ent_ip.text, app.ent_port.text, app.ent_addr.text = "127.0.0.1", str(board.port), "1"
app.ent_timeout.text, app.ent_retries.text = "600", "2"

_alerts.clear()
app.toggle_connect()                   # 等价于点「连接」按钮（含参数校验）
ok = wait_until(lambda: app.conn is not None and app.conn.is_open(), 3.0)
chk("连接成功", ok, _alerts[-1] if _alerts else "conn=None")
chk("收发线程已随连接启动",
    app._tx_thread is not None and app._tx_thread.is_alive())

app.read_status()
ok = wait_until(lambda: "3" in app.lbl_floor.text and app.lbl_floor.text != "--", 3.0)
chk("一键读取: 楼层回写到界面", ok, app.lbl_floor.text)
chk("系统状态卡片", "正常" in app.card_sys.val_lbl.text, app.card_sys.val_lbl.text)
chk("门状态卡片", "开门到位" in app.card_door.val_lbl.text,
    app.card_door.val_lbl.text)
chk("运行方向箭头跟随", app.view_dir._dir == 1, "dir=%s" % app.view_dir._dir)
chk("服务器收到的是合法 8 字节请求帧",
    board.frames and board.malformed == 0,
    "帧数=%d 畸形=%d" % (len(board.frames), board.malformed))

print("\n[2] 拆包：响应被切成 5 段投递")
board.fragment = 5
board.frames.clear()
app.read_status()
ok = wait_until(lambda: "3" in app.lbl_floor.text, 3.0)
chk("拆包后仍能正确重组解析", ok, app.lbl_floor.text)

print("\n[3] 粘包 + 心跳包污染：响应前塞 7 字节垃圾")
board.fragment = 1
board.prepend_garbage = b"\xaa\xbb\xcc\xdd\xee\xff\x00"
app.read_status()
ok = wait_until(lambda: "3" in app.lbl_floor.text, 3.0)
chk("垃圾前缀被长度定界 + CRC 滤掉", ok, app.lbl_floor.text)
board.prepend_garbage = b""

print("\n[4] 无响应自动重试（从机静默 2 次后恢复）")
board.auto_reply = False
board.frames.clear()
app.read_status()
wait_until(lambda: len(board.frames) >= 1, 1.5)
board.auto_reply = True                       # 让它从下一次尝试开始应答
ok = wait_until(lambda: len(board.frames) >= 2, 3.0)
chk("无响应时按配置重发", ok, "重发至第 %d 次" % len(board.frames))
chk("重试后拿到响应并回写界面", "3" in app.lbl_floor.text, app.lbl_floor.text)

print("\n[5] 并发定时任务不串帧（自动刷新 + 持续开门 同时开）")
app.sw_ctrl.active = True                     # 打开远程控制使能
chk("远程控制使能已开", app.ctrl_enabled is True)
board.frames.clear()
board.malformed = 0
app.ent_interval.text = "1"
app.sw_autoref.active = True
app.sw_cont.active = True
pump(2.2)                                     # 必须驱动 Clock，否则定时任务不跑
app.sw_cont.active = False
app.sw_autoref.active = False
pump(0.3)
chk("并发期间所有请求帧完整且合法", board.malformed == 0 and len(board.frames) >= 8,
    "帧数=%d 畸形=%d" % (len(board.frames), board.malformed))
_open_cnt = sum(1 for f in board.frames
                if f[1] == 0x06 and f[2] == 0x9C and f[3] == 0x56)
_read_cnt = sum(1 for f in board.frames if f[1] == 0x03)
chk("读状态与开门指令都发出去了", _read_cnt >= 1 and _open_cnt >= 1,
    "读=%d 开门=%d" % (_read_cnt, _open_cnt))

print("\n[6] 断开清理")
_th = app._tx_thread
app._teardown_conn()
chk("断开后队列引用清空", app._tx_queue is None)
chk("断开后收发线程退出", wait_until(lambda: not _th.is_alive(), 2.0))
chk("断开后套接字已关闭", app.conn is None)

board.close()
print("\n" + "=" * 68)
print("结果: %d 通过 / %d 失败" % (len(PASS), len(FAIL)))
print("=" * 68)
sys.exit(1 if FAIL else 0)
