# -*- coding: utf-8 -*-
"""
奶娃桌宠 - 独立桥接 Host（非 Cordis / DSH 环境）
提供本地 HTTP API 接收状态推送，通过 stdio JSON-lines 协议驱动桌宠 helper。
用法：python bridge/server.py [--port 18923] [--exe path/to/pet-nailong.exe]
"""
import argparse
import json
import os
import subprocess
import sys
import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse

# 允许从源码目录直接运行（bridge/ 与 helper/ 同级）
_HERE = os.path.dirname(os.path.abspath(__file__))
_HELPER = os.path.join(os.path.dirname(_HERE), "helper")
if _HELPER not in sys.path:
    sys.path.insert(0, _HELPER)

from state_table import (SESSION_STATES, STATE_ERROR, STATE_IDLE, STATE_STREAMING,
                         STATE_TASK_DONE, STATE_THINKING, STATE_TOOL_CALL, STATE_USER_MSG)

from packs import PackLoader

# ============================================================
# 状态定义（与 host/index.js 完全一致）
# ============================================================
VALID_STATES = list(SESSION_STATES)

STATE_COOLDOWN_MS = {
    # task_done 大笑时长要覆盖 laugh.gif 完整播放（nailong 包 GIF 约 4.3s），
    # 否则 GIF 没播完就被 force_set(STATE_IDLE) 掐断。留余量取 6s。
    STATE_TASK_DONE: 6000,
    STATE_ERROR: 3000,
    STATE_USER_MSG: 1500,
}

STATE_PRIORITY = {
    STATE_ERROR: 100,
    STATE_USER_MSG: 80,
    STATE_TASK_DONE: 60,
}

DEFAULT_PACK_ID = "nailong"

# 状态最小显示时间（ms）：防止快速模式下状态一闪而过
# 高优先级状态（error/task_done/user_msg）不受此限制，可立即打断
DEFAULT_MIN_DISPLAY_MS = 800

# 大笑防抖：两次大笑最小间隔，避免“每轮工具结束都笑”造成高频打扰
MIN_LAUGH_INTERVAL_MS = 30000
# 距上次活跃状态超过该时长视为“过期空闲”（如 30s 安全网兜底），不触发大笑
ROUND_STALE_MS = 15000


def _now_ms():
    return int(time.time() * 1000)


# ============================================================
# 会话状态机（与 host/index.js 的 SessionState 对齐 + 最小显示时间）
# ============================================================
class SessionState:
    def __init__(self, min_display_ms=DEFAULT_MIN_DISPLAY_MS, on_state_change=None):
        self.state = STATE_IDLE
        self.pack_id = DEFAULT_PACK_ID
        self.tool_count = 0
        self.has_used_tool_this_round = False
        self.last_tool_name = ""
        self.cooldown_until = 0
        self.min_display_ms = min_display_ms
        self.last_state_change = 0
        self.last_laugh_at = 0      # 上次大笑时间，用于防抖
        self._pending_state = None
        self._pending_timer = None
        self._on_state_change = on_state_change  # 状态实际变化时的回调
        self.lock = threading.Lock()

    def set_state(self, new_state, suppress_callback=False):
        """请求切换状态。
        - 高优先级状态（error/task_done/user_msg）：立即切换，取消 pending
        - 冷却期内的低优先级状态：拒绝
        - 最小显示时间内的新状态：延迟切换（存为 pending，定时器到期后应用）
        - 其他：立即切换
        返回 True 表示立即切换，False 表示被延迟或拒绝。
        suppress_callback=True 时不触发 on_state_change（HTTP handler 自己手动 send_state，避免重复发送）。
        """
        now = _now_ms()

        # 冷却期内，低优先级状态被拒绝
        if now < self.cooldown_until and not self._is_higher_priority(new_state):
            return False

        # 高优先级状态：立即切换，取消 pending
        if self._is_higher_priority(new_state):
            self._cancel_pending()
            self._do_set_state(new_state, now, suppress_callback=suppress_callback)
            return True

        # 相同状态：无需切换，但取消 pending（避免旧 pending 覆盖）
        if new_state == self.state:
            self._cancel_pending()
            return True

        # 检查最小显示时间
        elapsed = now - self.last_state_change
        if elapsed < self.min_display_ms:
            # 延迟切换：更新 pending，重设定时器
            remaining = self.min_display_ms - elapsed
            self._pending_state = new_state
            if self._pending_timer:
                self._pending_timer.cancel()
            self._pending_timer = threading.Timer(
                remaining / 1000.0, self._apply_pending
            )
            self._pending_timer.daemon = True
            self._pending_timer.start()
            return False  # 延迟切换

        # 立即切换
        self._cancel_pending()
        self._do_set_state(new_state, now, suppress_callback=suppress_callback)
        return True

    def _do_set_state(self, new_state, now=None, suppress_callback=False):
        """实际执行状态切换（内部方法，不做检查）。"""
        if now is None:
            now = _now_ms()
        self.state = new_state
        self.last_state_change = now
        if new_state in STATE_COOLDOWN_MS:
            self.cooldown_until = now + STATE_COOLDOWN_MS[new_state]
        # 触发回调（通知外部推送新状态给 helper）；批量更新时由调用方手动 send
        if not suppress_callback and self._on_state_change:
            try:
                self._on_state_change(self)
            except Exception:
                pass

    def _apply_pending(self):
        """定时器回调：应用待切换的状态。"""
        with self.lock:
            if self._pending_state:
                target = self._pending_state
                self._pending_state = None
                self._pending_timer = None
                self._do_set_state(target)

    def _cancel_pending(self):
        """取消待切换的状态。"""
        if self._pending_timer:
            self._pending_timer.cancel()
            self._pending_timer = None
        self._pending_state = None

    def force_set(self, new_state):
        """强制立即切换（用于 reset 等场景，忽略最小显示时间和冷却）。"""
        self._cancel_pending()
        self._do_set_state(new_state)

    def _is_higher_priority(self, new_state):
        return STATE_PRIORITY.get(new_state, 0) > STATE_PRIORITY.get(self.state, 0)

    def to_dict(self):
        return {
            "state": self.state,
            "packId": self.pack_id,
            "toolCount": self.tool_count,
            "lastTool": self.last_tool_name,
            "hasUsedToolThisRound": self.has_used_tool_this_round,
            "pendingState": self._pending_state,
        }


# ============================================================
# Helper 驱动：子进程模式 / 进程内回调模式
# ============================================================
def _build_state_msg(session, pack_registry):
    """把当前会话打包成给桌宠的 state 消息（子进程 / 进程内共用）。"""
    pack = pack_registry.get(session.pack_id) if pack_registry else None
    return {
        "kind": "state",
        "state": session.state,
        "packId": session.pack_id,
        "toolCount": session.tool_count,
        "lastTool": session.last_tool_name or "",
        "bubbles": pack.bubbles if pack else {},
        "clickBubbles": pack.click_bubbles if pack else [],
        "laugh": pack.laugh if pack else {},
        "thinkingLines": pack.thinking_lines if pack else [],
        "timestamp": _now_ms(),
    }


class InProcessHelper:
    """进程内模式：不 spawn 子进程，通过回调（Qt 信号）直接驱动窗口。
    接口与 HelperManager 对齐，BridgeServer 无感切换。"""

    def __init__(self, emit_cb):
        self._emit = emit_cb
        self._stopped = False

    @property
    def alive(self):
        return not self._stopped

    def start(self):
        return True

    def send(self, msg):
        try:
            self._emit(msg)
            return True
        except Exception:
            return False

    def send_state(self, session, pack_registry):
        return self.send(_build_state_msg(session, pack_registry))

    def send_pack(self, pack_id):
        return self.send({"kind": "pack", "packId": pack_id})

    def stop(self):
        self._stopped = True
        try:
            self._emit({"kind": "shutdown"})
        except Exception:
            pass


class HelperManager:
    def __init__(self, root_dir, exe_path=None):
        self.root_dir = root_dir
        self.packs_dir = os.path.join(root_dir, "packs")
        self.exe_path = exe_path
        self.proc = None
        self._lock = threading.Lock()
        self._shutting_down = False
        self._user_exited = False

    def start(self):
        with self._lock:
            if self.proc and self.proc.poll() is None:
                return True
            self._shutting_down = False
            self._user_exited = False

            if self.exe_path and os.path.isfile(self.exe_path):
                cmd = [self.exe_path, "--packs", self.packs_dir]
            else:
                helper_main = os.path.join(self.root_dir, "helper", "main.py")
                python_cmd = os.environ.get("PET_PYTHON", "python")
                cmd = [python_cmd, helper_main, "--packs", self.packs_dir]

            try:
                self.proc = subprocess.Popen(
                    cmd,
                    cwd=self.root_dir,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    bufsize=0,
                )
                t = threading.Thread(target=self._read_loop, daemon=True)
                t.start()
                print(f"[bridge] helper 已启动 pid={self.proc.pid}")
                return True
            except Exception as e:
                print(f"[bridge] helper 启动失败: {e}")
                self.proc = None
                return False

    def _read_loop(self):
        if not self.proc or not self.proc.stdout:
            return
        for raw in self.proc.stdout:
            line = raw.strip()
            if not line:
                continue
            try:
                msg = json.loads(line.decode("utf-8"))
                self._on_message(msg)
            except Exception:
                continue

    def _on_message(self, msg):
        kind = msg.get("kind")
        if kind == "ready":
            ids = [p.get("id") for p in msg.get("packs", [])]
            print(f"[bridge] helper ready, packs: {', '.join(ids)}")
        elif kind == "event":
            name = msg.get("name")
            print(f"[bridge] helper event: {name}")
            if name == "exited":
                self._user_exited = True
        elif kind == "pong":
            pass

    def send(self, msg):
        if not self.proc or self.proc.poll() is not None or not self.proc.stdin:
            return False
        try:
            data = (json.dumps(msg, ensure_ascii=False) + "\n").encode("utf-8")
            self.proc.stdin.write(data)
            self.proc.stdin.flush()
            return True
        except Exception:
            return False

    def send_state(self, session, pack_registry):
        return self.send(_build_state_msg(session, pack_registry))

    def send_pack(self, pack_id):
        return self.send({"kind": "pack", "packId": pack_id})

    def stop(self):
        self._shutting_down = True
        if self.proc and self.proc.poll() is None:
            try:
                self.send({"kind": "shutdown"})
            except Exception:
                pass
            try:
                self.proc.wait(timeout=2)
            except Exception:
                self.proc.kill()
        self.proc = None

    @property
    def alive(self):
        return self.proc is not None and self.proc.poll() is None


# ============================================================
# HTTP 请求处理
# ============================================================
class BridgeHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # 静默访问日志

    def _send_json(self, code, data):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self):
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            return {}
        body = self.rfile.read(length)
        try:
            return json.loads(body.decode("utf-8"))
        except Exception:
            return {}

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        srv = self.server

        if path == "/api/health":
            self._send_json(200, {
                "ok": True,
                "helperAlive": srv.helper.alive,
                "state": srv.session.state,
                "packId": srv.session.pack_id,
            })
        elif path == "/api/state":
            self._send_json(200, srv.session.to_dict())
        elif path == "/api/packs":
            self._send_json(200, {"packs": srv.packs.list()})
        else:
            self._send_json(404, {"error": "not found"})

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        data = self._read_json()
        session = self.server.session
        helper = self.server.helper
        packs = self.server.packs

        # ---- 完整状态更新 ----
        if path == "/api/state":
            state = data.get("state")
            if state and state in VALID_STATES:
                with session.lock:
                    session.set_state(state, suppress_callback=True)
                    if "packId" in data:
                        session.pack_id = data["packId"]
                    if "toolCount" in data:
                        session.tool_count = int(data["toolCount"])
                    if "lastTool" in data:
                        session.last_tool_name = data.get("lastTool") or ""
                helper.send_state(session, packs)
            self._send_json(200, {"ok": True, **session.to_dict()})
            return

        # ---- 快捷状态更新: /api/state/{state} ----
        if path.startswith("/api/state/"):
            state = path[len("/api/state/"):]
            if state not in VALID_STATES and state != "running":
                self._send_json(400, {
                    "error": f"invalid state: {state}",
                    "valid": VALID_STATES + ["running"],
                })
                return

            with session.lock:
                if state == "running":
                    # agent 开始运行：没用过工具 → thinking
                    if not session.has_used_tool_this_round:
                        session.set_state(STATE_THINKING, suppress_callback=True)
                        helper.send_state(session, packs)
                elif state == STATE_IDLE:
                    # agent 回到 idle：
                    # - Codex 精确源：本轮用过工具 → task_done 大笑
                    # - 进程源（豆包等）：从 thinking/streaming 转 idle 且持续超过 5 秒才算完成一轮
                    #   （CPU 阈值 8%，idle_delay 2s，MIN_ACTIVE_STREAK 3，配合 30 秒大笑冷却防误触发）
                    if "lastTool" in (data or {}):
                        session.last_tool_name = data.get("lastTool") or ""
                    prev = session.state
                    source_type = (data or {}).get("sourceType", "")
                    now_ms = _now_ms()
                    active_duration = now_ms - session.last_state_change
                    is_system = source_type == "system"
                    process_finished = (
                        not is_system and source_type == "process"
                        and prev in (STATE_THINKING, STATE_STREAMING)
                        and active_duration >= 5000
                    )
                    # Codex 源：本轮用过工具，且是“新鲜的轮次结束”（排除 30s 安全网的过期空闲误笑）
                    codex_finished = not is_system and session.has_used_tool_this_round and active_duration <= ROUND_STALE_MS
                    finished_round = codex_finished or process_finished
                    can_laugh = now_ms - session.last_laugh_at >= MIN_LAUGH_INTERVAL_MS
                    if finished_round and can_laugh:
                        session.set_state(STATE_TASK_DONE, suppress_callback=True)
                        # 大笑时长跟随表情包（pack.json laugh.duration_ms），缺省 6s
                        duration = _laugh_duration_ms(session, packs)
                        session.cooldown_until = _now_ms() + duration
                        session.has_used_tool_this_round = False
                        session.tool_count = 0
                        session.last_laugh_at = _now_ms()
                        helper.send_state(session, packs)
                        _schedule_cooldown(STATE_TASK_DONE, STATE_IDLE, session, helper, packs, delay_ms=duration)
                    else:
                        # 不满足大笑条件（过期空闲 / 大笑防抖中）：清工具标记，避免残留误笑。
                        # 若正处于上次大笑的展示/冷却期，set_state(STATE_IDLE) 会被拒绝——此时保持
                        # task_done 不重发，避免窗口二次触发大笑 GIF/音频。
                        session.has_used_tool_this_round = False
                        session.tool_count = 0
                        session.set_state(STATE_IDLE, suppress_callback=True)
                        if session.state == STATE_IDLE:
                            helper.send_state(session, packs)
                elif state in (STATE_ERROR, STATE_USER_MSG):
                    session.set_state(state, suppress_callback=True)
                    helper.send_state(session, packs)
                    target = STATE_IDLE if state == STATE_ERROR else STATE_THINKING
                    _schedule_cooldown(state, target, session, helper, packs)
                else:
                    session.set_state(state, suppress_callback=True)
                    helper.send_state(session, packs)

            self._send_json(200, {"ok": True, **session.to_dict()})
            return

        # ---- 工具调用开始 ----
        if path == "/api/tool/start":
            tool_name = data.get("name", "")
            with session.lock:
                session.tool_count += 1
                session.has_used_tool_this_round = True
                session.last_tool_name = tool_name
                changed = session.set_state(STATE_TOOL_CALL, suppress_callback=True)
            # 仅在状态确实切到 tool_call 时推送；冷却期被拒绝（如 task_done 展示中）不重发，
            # 避免把 task_done 再发给窗口导致二次大笑
            if changed or session.state == STATE_TOOL_CALL:
                helper.send_state(session, packs)
            self._send_json(200, {"ok": True, **session.to_dict()})
            return

        # ---- 工具调用结束 ----
        if path == "/api/tool/end":
            self._send_json(200, {"ok": True, **session.to_dict()})
            return

        # ---- 切换表情包 ----
        if path == "/api/pack":
            pack_id = data.get("packId")
            if not pack_id or pack_id not in packs.packs:
                self._send_json(400, {"error": f"unknown pack: {pack_id}"})
                return
            with session.lock:
                session.pack_id = pack_id
            helper.send_pack(pack_id)
            helper.send_state(session, packs)
            self._send_json(200, {"ok": True, "packId": pack_id})
            return

        # ---- 重置状态 ----
        if path == "/api/reset":
            with session.lock:
                session.tool_count = 0
                session.has_used_tool_this_round = False
                session.last_tool_name = ""
                session.force_set(STATE_IDLE)
            self._send_json(200, {"ok": True, **session.to_dict()})
            return

        # ---- 优雅关闭（stop-all.bat 调用）----
        if path == "/api/shutdown":
            self._send_json(200, {"ok": True, "shuttingDown": True})
            srv = self.server

            def _shutdown_worker():
                time.sleep(0.3)
                try:
                    srv.helper.stop()   # 发 shutdown 给桌宠，优雅退出
                except Exception:
                    pass
                try:
                    srv.shutdown()      # 停止 HTTP 服务
                except Exception:
                    pass

            threading.Thread(target=_shutdown_worker, daemon=True).start()
            return

        self._send_json(404, {"error": "not found"})


def _laugh_duration_ms(session, pack_registry):
    """大笑展示时长：表情包可在 pack.json 的 laugh.duration_ms 里自定义，
    缺省用 STATE_COOLDOWN_MS[STATE_TASK_DONE]（6s），适配更长/更短的 GIF 或音频。"""
    try:
        pack = pack_registry.get(session.pack_id) if pack_registry else None
        if pack:
            d = int(pack.laugh.get("duration_ms", 0) or 0)
            if d > 0:
                return d
    except Exception:
        pass
    return STATE_COOLDOWN_MS.get(STATE_TASK_DONE, 6000)


def _schedule_cooldown(from_state, to_state, session, helper, packs, delay_ms=None):
    """冷却时间后自动切回目标状态（独立线程，不阻塞 HTTP 响应）。
    用 force_set 确保冷却到期后立即切换，不受最小显示时间限制。
    """
    delay = (delay_ms if delay_ms is not None else STATE_COOLDOWN_MS.get(from_state, 0)) / 1000.0

    def _worker():
        time.sleep(delay)
        with session.lock:
            if session.state == from_state:
                session.force_set(to_state)

    threading.Thread(target=_worker, daemon=True).start()


# ============================================================
# 服务器
# ============================================================
class BridgeServer(HTTPServer):
    def __init__(self, port, root_dir, exe_path=None, min_display_ms=DEFAULT_MIN_DISPLAY_MS,
                 emit_cb=None, packs_dir=None):
        """emit_cb: 进程内模式回调（如 Qt 信号的 emit），非 None 则不 spawn 子进程。
        packs_dir: 表情包目录（默认 root_dir/packs），一体化 exe 用它统一窗口与桥接。"""
        super().__init__(("127.0.0.1", port), BridgeHandler)
        self.root_dir = root_dir
        self.packs_dir = packs_dir or os.path.join(root_dir, "packs")
        self.packs = PackLoader(self.packs_dir)
        self.min_display_ms = min_display_ms
        # 状态变化回调：立即切换或延迟切换到期时，自动推送给 helper
        def _on_change(session):
            self.helper.send_state(session, self.packs)
        self.session = SessionState(min_display_ms=min_display_ms, on_state_change=_on_change)

        if emit_cb is not None:
            self.helper = InProcessHelper(emit_cb)
            self.helper.start()
            time.sleep(0.1)
        else:
            self.helper = HelperManager(root_dir, exe_path)
            self.helper.start()
            # 等 helper 初始化后推送初始状态
            time.sleep(0.6)
        self.helper.send_state(self.session, self.packs)


def main():
    parser = argparse.ArgumentParser(description="奶娃桌宠 - 独立桥接 Host")
    parser.add_argument("--port", type=int, default=18923, help="HTTP 端口（默认 18923）")
    parser.add_argument("--root", default=None, help="项目根目录（默认脚本上级目录）")
    parser.add_argument("--exe", default=None, help="pet-nailong.exe 路径（默认自动查找）")
    parser.add_argument("--min-display", type=int, default=DEFAULT_MIN_DISPLAY_MS,
                        help=f"状态最小显示时间毫秒（默认 {DEFAULT_MIN_DISPLAY_MS}，快速模式下调大可看清变化）")
    args = parser.parse_args()

    root_dir = args.root or os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    exe_path = args.exe
    if not exe_path:
        candidate = os.path.join(root_dir, "pet-nailong.exe")
        if os.path.isfile(candidate):
            exe_path = candidate

    print("=" * 50)
    print(f"[bridge] 项目根目录: {root_dir}")
    print(f"[bridge] 表情包目录: {os.path.join(root_dir, 'packs')}")
    print(f"[bridge] HTTP 地址: http://127.0.0.1:{args.port}")
    print(f"[bridge] helper 来源: {exe_path or 'python helper/main.py'}")
    print(f"[bridge] 最小显示时间: {args.min_display} ms")
    print(f"[bridge] 可用表情包: {', '.join(p['id'] for p in PackLoader(os.path.join(root_dir, 'packs')).list())}")
    print("=" * 50)
    print("[bridge] 服务已启动，按 Ctrl+C 退出")
    print()

    server = BridgeServer(args.port, root_dir, exe_path, min_display_ms=args.min_display)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[bridge] 正在关闭...")
        server.helper.stop()
        server.server_close()
        print("[bridge] 已退出")


if __name__ == "__main__":
    main()
