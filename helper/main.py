# -*- coding: utf-8 -*-
"""
奶娃桌宠 - 桌面 helper 入口
用法：
  python helper/main.py --packs <packs目录>            # 协议模式（由 host 插件 spawn）
  python helper/main.py --demo --packs <packs目录>     # 独立演示（自动循环状态）
依赖：Python 3.8+ / PyQt5（pip install PyQt5）
"""
import argparse
import datetime
import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PyQt5.QtCore import QObject, pyqtSignal
from PyQt5.QtWidgets import QApplication

import protocol

from state_table import (STATE_ERROR, STATE_IDLE, STATE_STREAMING, STATE_TASK_DONE,
                         STATE_THINKING, STATE_TOOL_CALL, STATE_USER_MSG)
from packs import PackLoader
from pet_window import PetWindow
from tray import TrayIcon

DEMO_STATES = [STATE_IDLE, STATE_THINKING, STATE_TOOL_CALL, STATE_STREAMING,
               STATE_TASK_DONE, STATE_ERROR, STATE_USER_MSG, STATE_IDLE]


class Bridge(QObject):
    """跨线程消息桥：stdin 读取线程 → Qt 主线程（信号槽自动排队）。"""

    message = pyqtSignal(dict)


class StdinReader(threading.Thread):
    """后台线程读 host 的 JSON-lines 消息，经 Bridge 转发到主线程。"""

    def __init__(self, bridge):
        super().__init__(daemon=True)
        self._bridge = bridge
        self._stop = threading.Event()

    def run(self):
        while not self._stop.is_set():
            msg = protocol.read_message(sys.stdin)
            if msg is None:
                break
            self._bridge.message.emit(msg)

    def stop(self):
        self._stop.set()


def parse_args():
    parser = argparse.ArgumentParser(description="奶娃桌宠桌面 helper")
    parser.add_argument("--demo", action="store_true", help="独立演示模式（自动循环状态）")
    parser.add_argument("--packs", default=None, help="packs 目录（缺省为 helper 上级目录的 packs）")
    parser.add_argument("--pack", default="nailong", help="初始表情包 id")
    return parser.parse_args()


def default_packs_dir():
    """packs 目录解析：
    1) exe 同目录的 packs/（外部扩展，加表情包放这里）
    2) 打包内置的 packs/（_MEIPASS）
    3) 源码模式：项目根目录的 packs/
    """
    if hasattr(sys, "_MEIPASS"):
        ext = os.path.join(os.path.dirname(sys.executable), "packs")
        if os.path.isdir(ext):
            return ext
        return os.path.join(sys._MEIPASS, "packs")
    return os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "packs"))


def _fix_stdio():
    """PyInstaller windowed 模式（无控制台）下 sys.stdout/stderr 为 None，
    print / protocol.write_message 会崩。重定向到 devnull 并接管异常写日志。"""
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")

    def excepthook(tp, val, tb):
        try:
            import traceback
            log_dir = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "pet-nailong")
            os.makedirs(log_dir, exist_ok=True)
            with open(os.path.join(log_dir, "error.log"), "a", encoding="utf-8") as f:
                f.write("==== %s ====\n" % datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
                traceback.print_exception(tp, val, tb, file=f)
        except Exception:  # noqa: BLE001
            pass

    sys.excepthook = excepthook


def main():
    _fix_stdio()
    args = parse_args()
    if args.packs:
        packs_dir = os.path.abspath(args.packs)
    else:
        packs_dir = default_packs_dir()

    app = QApplication(sys.argv[:1])
    app.setApplicationName("pet-nailong")
    app.setQuitOnLastWindowClosed(True)

    loader = PackLoader(packs_dir)
    window = PetWindow(loader, args.pack)   # 位置/模式等配置统一由 PetWindow 读写

    tray = TrayIcon(window)
    window.show()
    window.raise_()

    if args.demo:
        # 独立模式：不自动切换状态；单击桌宠依次预览各状态
        idx = [0]

        def on_demo_event(msg):
            if msg.get("kind") == "event" and msg.get("name") == "click":
                state = DEMO_STATES[idx[0] % len(DEMO_STATES)]
                idx[0] += 1
                window.apply_state(state)

        window.event_out.connect(on_demo_event)
        rc = app.exec_()
        tray.hide()
        return rc

    # 协议模式
    bridge = Bridge()
    bridge.message.connect(window.on_host_message)
    reader = None
    if sys.stdin is not None:
        reader = StdinReader(bridge)
        reader.start()
    window.event_out.connect(lambda msg: protocol.write_message(msg))
    protocol.write_message(protocol.make("ready", packId=window.pack_id, packs=loader.list()))

    rc = app.exec_()
    if reader is not None:
        reader.stop()
    tray.hide()
    return rc


if __name__ == "__main__":
    sys.exit(main())
