# -*- coding: utf-8 -*-
"""
奶娃桌宠 - 一体化入口（单 exe）
================================
把 桥接(HTTP+状态机) + 全 AI 状态监控 + 桌宠窗口 合并进同一个进程，
无终端弹窗、无子进程、无外部依赖路径，双击即用。

打包（无终端窗口）：
  python -m PyInstaller --noconfirm --onefile --windowed --name 奶娃桌宠 ^
    --add-data "packs;packs" --add-data "icons;icons" ^
    --icon icons/pet-icon.ico helper/all_in_one.py

结构：
  main 线程   : PyQt 桌宠窗口 + 托盘
  后台线程 1  : HTTP 桥接服务（127.0.0.1:18923，进程内回调驱动窗口）
  后台线程 2  : AI 监控（Codex 事件 + 进程活动 → 推给桥接）
"""
import os
import subprocess
import sys
import threading
import time

# 允许从源码目录直接运行（helper/ 与 bridge/ 同级）
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_HERE, os.path.join(_ROOT, "bridge")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from PyQt5.QtCore import QObject, pyqtSignal
from PyQt5.QtWidgets import QApplication, QMessageBox

from packs import PackLoader
from pet_window import PetWindow
from tray import TrayIcon

import server as bridge_server
import ai_monitor


class Bridge(QObject):
    """跨线程消息桥：后台线程 emit 信号 → Qt 主线程驱动窗口（信号槽自动排队）。"""
    message = pyqtSignal(dict)


def _log_path():
    """日志路径：%APPDATA%/pet-nailong/error.log（windowed 模式下所有输出与异常都写这里）。"""
    log_dir = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "pet-nailong")
    try:
        os.makedirs(log_dir, exist_ok=True)
    except Exception:
        pass
    return os.path.join(log_dir, "error.log")


def _app_data_dir():
    return os.path.dirname(_log_path())


# ---- 崩溃自重启 ----
_USER_EXIT_FLAG = os.path.join(_app_data_dir(), ".user_exit")
_CRASH_LOG = os.path.join(_app_data_dir(), ".crash_times")
MAX_CRASHES_IN_WINDOW = 3   # 60秒内最多重启3次，超过则停止（防无限循环）
CRASH_WINDOW_SEC = 60


def _mark_user_exit():
    """用户主动退出时标记，excepthook 检测到该标记则不重启。"""
    try:
        with open(_USER_EXIT_FLAG, "w", encoding="utf-8") as f:
            f.write("1")
    except Exception:
        pass


def _is_user_exit():
    try:
        return os.path.exists(_USER_EXIT_FLAG)
    except Exception:
        return False


def _clear_user_exit():
    try:
        if os.path.exists(_USER_EXIT_FLAG):
            os.remove(_USER_EXIT_FLAG)
    except Exception:
        pass


def _record_crash_and_check():
    """记录本次崩溃时间，返回是否允许重启（60秒内崩溃次数<=3才允许）。"""
    try:
        now = time.time()
        times = []
        if os.path.exists(_CRASH_LOG):
            with open(_CRASH_LOG, "r", encoding="utf-8") as f:
                times = [float(line.strip()) for line in f if line.strip()]
        times = [t for t in times if now - t < CRASH_WINDOW_SEC]
        times.append(now)
        with open(_CRASH_LOG, "w", encoding="utf-8") as f:
            for t in times:
                f.write("%.3f\n" % t)
        return len(times) <= MAX_CRASHES_IN_WINDOW
    except Exception:
        return True


def _restart_app():
    """启动新实例后退出当前实例。"""
    try:
        if hasattr(sys, "_MEIPASS"):
            subprocess.Popen([sys.executable], creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
        else:
            subprocess.Popen([sys.executable, os.path.abspath(__file__)])
    except Exception:
        pass


def _fix_stdio():
    """PyInstaller windowed 模式（无控制台）下 sys.stdout/stderr 为 None。
    重定向到 %APPDATA%/pet-nailong/error.log（行缓冲），方便排查；源码模式保留控制台。"""
    if sys.stdout is None:
        try:
            log = open(_log_path(), "a", encoding="utf-8", buffering=1)
            sys.stdout = log
            sys.stderr = log
        except Exception:
            sys.stdout = open(os.devnull, "w", encoding="utf-8")
            sys.stderr = open(os.devnull, "w", encoding="utf-8")

    def excepthook(tp, val, tb):
        try:
            import datetime
            import traceback
            with open(_log_path(), "a", encoding="utf-8") as f:
                f.write("==== %s ====\n" % datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
                traceback.print_exception(tp, val, tb, file=f)
        except Exception:
            pass
        # 崩溃自重启：用户主动退出不重启，60秒内崩溃超3次不重启（防无限循环）
        if _is_user_exit():
            _clear_user_exit()
            return
        if _record_crash_and_check():
            _restart_app()

    sys.excepthook = excepthook


def _resource_path(rel):
    """打包后资源路径：onefile 解压到 _MEIPASS，源码模式用项目根。"""
    base = getattr(sys, "_MEIPASS", _ROOT)
    return os.path.join(base, rel)


def _default_packs_dir():
    """表情包搜索路径（优先级从高到低，返回 list，可多个目录合并）：
    exe 同目录 packs/（自定义扩展，优先）→ 打包内置 _MEIPASS/packs（兜底，永远可用）→ 源码 packs/。
    这样默认奶龙包不需要任何外部文件夹，自定义包丢进 exe 同目录 packs/ 只是叠加、不覆盖内置包。"""
    if hasattr(sys, "_MEIPASS"):
        dirs = []
        ext = os.path.join(os.path.dirname(sys.executable), "packs")
        if os.path.isdir(ext):
            dirs.append(ext)
        bundled = _resource_path("packs")
        if os.path.isdir(bundled):
            dirs.append(bundled)
        return dirs
    return [os.path.join(_ROOT, "packs")]


def main():
    _fix_stdio()
    _clear_user_exit()  # 启动时清除上次的用户退出标记
    app = QApplication(sys.argv[:1])
    app.setApplicationName("pet-nailong")

    packs_dir = _default_packs_dir()
    loader = PackLoader(packs_dir)
    window = PetWindow(loader, "nailong")   # 位置/模式等配置统一由 PetWindow 读写

    tray = TrayIcon(window)
    window.show()
    window.raise_()

    # 跨线程消息桥：后台线程 emit → 主线程驱动窗口
    bridge = Bridge()
    bridge.message.connect(window.on_host_message)
    window.event_out.connect(lambda msg: None)  # 一体化模式不向 stdout 回传

    # ---- 进程内 HTTP 桥接（后台线程）----
    root_dir = _ROOT
    if hasattr(sys, "_MEIPASS"):
        # 打包模式：packs 在内置目录，root 也指向内置目录以便 PackRegistry 扫描
        root_dir = _resource_path(".")
    # 端口被占用时自动顺延，避免静默失效；全被占用则弹窗提示
    http_server = None
    bridge_port = None
    last_err = None
    for port in range(18923, 18923 + 6):
        try:
            http_server = bridge_server.BridgeServer(
                port, root_dir, min_display_ms=800,
                emit_cb=bridge.message.emit, packs_dir=packs_dir)
            bridge_port = port
            break
        except OSError as e:
            last_err = e
            print(f"[all-in-one] 端口 {port} 被占用，尝试下一个: {e}", flush=True)
            http_server = None
    if http_server is None:
        QMessageBox.critical(
            None, "奶娃桌宠",
            "桥接端口 18923-18928 均被占用，无法启动 AI 状态监控。\n"
            "请关闭占用端口的程序后重新运行。\n\n%s" % last_err)
        return 1

    print(f"[all-in-one] 桥接已启动: http://127.0.0.1:{bridge_port}", flush=True)
    t_http = threading.Thread(target=http_server.serve_forever, daemon=True)
    t_http.start()
    # 推送一次初始状态
    http_server.helper.send_state(http_server.session, http_server.packs)

    # ---- AI 监控（后台线程）----
    apps, cfg_path = ai_monitor.load_apps(None)
    sources = ai_monitor.build_sources(apps, ai_monitor.DEFAULT_IDLE_STALE, verbose=False)
    pusher = ai_monitor.BridgePusher("http://127.0.0.1:%d" % bridge_port, verbose=False)
    stop_event = threading.Event()
    t_mon = threading.Thread(
        target=ai_monitor.run_forever,
        args=(pusher, sources),
        kwargs={"interval": ai_monitor.DEFAULT_INTERVAL, "stop_event": stop_event},
        daemon=True,
    )
    t_mon.start()

    try:
        rc = app.exec_()
    finally:
        stop_event.set()
        if http_server is not None:
            try:
                http_server.shutdown()
            except Exception:
                pass
            try:
                http_server.server_close()
            except Exception:
                pass
        tray.hide()

    return rc


if __name__ == "__main__":
    sys.exit(main())
