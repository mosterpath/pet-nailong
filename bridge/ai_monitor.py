# -*- coding: utf-8 -*-
"""
奶娃桌宠 - 全 AI 统一状态监控器
================================
让桌宠看到【所有 AI 软件】的工作状态，而不只是某一个。

数据源（可扩展，见 bridge/ai_apps.json）：
  - Codex 源（type=codex）  : 读取 ~/.codex/sessions 下的会话 JSONL，
                            精确解析 thinking / tool_call / streaming / user_msg / idle
  - 进程活动源（type=process）: 按进程名（如 Doubao / Yuanbao / Kimi ...）采样 CPU + 网络，
                            推断 thinking / streaming / idle（与 auto_monitor 同逻辑）

合并规则：
  精确事件源（Codex）优先；否则取「最近有动静的那个软件」；全部空闲则推 idle。
  状态卡上会显示是哪个软件（如 🛠 豆包 / 🛠 Codex）。

用法：
  python bridge/ai_monitor.py                # 用 bridge/ai_apps.json（默认清单）
  python bridge/ai_monitor.py --config my_apps.json
  python bridge/ai_monitor.py --bridge http://127.0.0.1:18923 --verbose
  python bridge/ai_monitor.py --apps codex,doubao,kimi   # 只监控部分软件
"""
import argparse
import glob
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime

try:
    import ctypes
    from ctypes import wintypes
    _HAS_WIN32 = True
except Exception:
    _HAS_WIN32 = False

try:
    import psutil
except ImportError:
    psutil = None

# 复用 auto_monitor 的状态推断（thinking/streaming/idle）
from auto_monitor import StateInferrer
# 系统事件监控（电量/深夜/久坐/音乐）
from system_events import SystemMonitor

# ============================================================
# 默认配置
# ============================================================
DEFAULT_BRIDGE_URL = "http://127.0.0.1:18923"
DEFAULT_INTERVAL = 0.5            # 采样间隔（秒）
DEFAULT_IDLE_STALE = 30.0         # Codex 事件超过该秒数无更新，视为空闲（安全网）
CODEX_SESSIONS_DIR = os.path.join(os.path.expanduser("~"), ".codex", "sessions")

# 内置兜底清单（优先读 bridge/ai_apps.json，读不到才用这个）
DEFAULT_APPS = [
    {"id": "codex", "name": "Codex", "type": "codex"},
    {"id": "doubao", "name": "豆包", "type": "process", "processes": ["Doubao"]},
    {"id": "yuanbao", "name": "腾讯元宝", "type": "process", "processes": ["Yuanbao", "yuanbao"]},
    {"id": "kimi", "name": "Kimi", "type": "process", "processes": ["Kimi"]},
    {"id": "deepseek", "name": "DeepSeek", "type": "process", "processes": ["DeepSeek"]},
    {"id": "tongyi", "name": "通义千问", "type": "process", "processes": ["Tongyi", "qianwen"]},
    {"id": "zhipu", "name": "智谱清言", "type": "process", "processes": ["Zhipu", "ChatGLM"]},
    {"id": "wenxin", "name": "文心一言", "type": "process", "processes": ["wenxin", "Wenxin", "BaiduWenxin"]},
    {"id": "xinghuo", "name": "讯飞星火", "type": "process", "processes": ["Spark", "Xinghuo", "iFlyAI"]},
    {"id": "cursor", "name": "Cursor", "type": "process", "processes": ["Cursor"], "cpu_threshold": 10, "detect_title_change": False},
    {"id": "vscode", "name": "VSCode", "type": "process", "processes": ["Code"], "cpu_threshold": 10, "detect_title_change": False},
    {"id": "trae", "name": "Trae", "type": "process", "processes": ["Trae"], "cpu_threshold": 10, "detect_title_change": False},
    {"id": "windsurf", "name": "Windsurf", "type": "process", "processes": ["Windsurf"], "cpu_threshold": 10, "detect_title_change": False},
    {"id": "chatbox", "name": "Chatbox", "type": "process", "processes": ["Chatbox"]},
    {"id": "cherry", "name": "Cherry Studio", "type": "process", "processes": ["Cherry Studio"]},
    {"id": "lmstudio", "name": "LM Studio", "type": "process", "processes": ["LM Studio"]},
    {"id": "ollama", "name": "Ollama", "type": "process", "processes": ["ollama"]},
    {"id": "jimeng", "name": "即梦", "type": "process", "processes": ["Dreamina"]},
    {"id": "jianying", "name": "剪映", "type": "process", "processes": ["JianyingPro"]},
]

# 默认进程活动阈值（与 auto_monitor 一致）
DEFAULT_NET_THRESHOLD = 2000   # 网络活动阈值（字节/采样周期0.5s ≈ 4KB/s），过滤后台心跳/同步
DEFAULT_CPU_THRESHOLD = 8.0    # CPU 阈值（百分比），过滤后台波动
DEFAULT_PROCESS_IDLE_DELAY = 2.0  # 进程源活动停止后多久转 idle（秒），短暂波动不延长
MIN_ACTIVE_STREAK = 3         # 需要连续 N 次采样有活动才显示非 idle，过滤单次后台活动
PROCESS_LAUGH_MIN_DURATION_MS = 5000  # 进程源触发大笑所需的最短活动持续时间（毫秒）

# 窗口标题辅助检测：AI 软件在生成/思考时窗口标题可能包含这些关键词
# 比纯 CPU 检测更直接、更准确（豆包等 Electron 应用后台 CPU 波动大，但标题变化是明确信号）
WINDOW_TITLE_ACTIVE_KEYWORDS = [
    # 通用生成/思考状态
    "正在生成", "生成中", "思考中", "回答中", "正在思考",
    "正在回答", "正在输入", "生成回答", "正在创作",
    # 具体动作类（联网搜索、画图、写代码等）
    "正在搜索", "正在联网", "正在阅读", "正在分析", "正在总结",
    "正在翻译", "正在写", "正在画", "正在编码", "正在生成代码",
    # 英文（ChatGPT / Claude 桌面版等）
    "generating", "thinking", "answering", "composing",
]


def _get_process_window_titles(pids):
    """枚举指定 PID 集合的所有可见顶层窗口标题，返回标题列表。"""
    if not _HAS_WIN32 or not pids:
        return []
    pid_set = set(pids)
    found = []
    user32 = ctypes.windll.user32

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def _enum_cb(hwnd, _lparam):
        try:
            if not user32.IsWindowVisible(hwnd):
                return True
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value not in pid_set:
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            if length <= 0:
                return True
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            title = buf.value.strip()
            if title:
                found.append(title)
        except Exception:
            pass
        return True

    try:
        user32.EnumWindows(_enum_cb, 0)
    except Exception:
        pass
    return found


def _window_title_is_active(titles):
    """检查窗口标题列表中是否有包含活动关键词的标题（不区分大小写）。"""
    for t in titles:
        tl = t.lower()
        for kw in WINDOW_TITLE_ACTIVE_KEYWORDS:
            if kw.lower() in tl:
                return True
    return False


def _titles_changed(prev, cur):
    """比较两次采样的窗口标题集合是否发生变化（增/删/改都算变化）。"""
    return set(prev or []) != set(cur or [])

CONFIG_CANDIDATES = [
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "ai_apps.json"),
    os.path.join(os.getcwd(), "ai_apps.json"),
]


def _norm_proc(name):
    """进程名规范化：去 .exe 后缀、转小写。psutil 可能返回 'doubao.exe'。"""
    n = (name or "").strip().lower()
    if n.endswith(".exe"):
        n = n[:-4]
    return n


# ============================================================
# Codex 会话事件解析
# ============================================================
def _parse_ts(ts):
    """解析 '2026-08-21T03:14:06.415Z' → epoch 秒。解析失败返回 0。"""
    if not ts:
        return 0.0
    s = ts.strip()
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(s)
        except ValueError:
            dt = datetime.strptime(s, "%Y-%m-%dT%H:%M:%S%z")
        return dt.timestamp()
    except Exception:
        return 0.0


class CodexEventParser:
    """把一条 Codex 会话 JSONL 事件映射为 (state, tool_name) 或 None。"""

    @staticmethod
    def map(line):
        try:
            evt = json.loads(line)
        except Exception:
            return None
        if not isinstance(evt, dict):
            return None

        ts = _parse_ts(evt.get("timestamp"))
        typ = evt.get("type")
        payload = evt.get("payload") or {}

        if typ == "response_item":
            pt = payload.get("type")
            if pt == "reasoning":
                return {"ts": ts, "state": "thinking", "tool": ""}
            if pt == "function_call":
                return {"ts": ts, "state": "tool_call", "tool": payload.get("name", "")}
            if pt == "function_call_output":
                # 工具结束：交给下一条 reasoning / message 决定，不直接推送
                return None
            if pt == "message":
                role = payload.get("role")
                if role == "assistant":
                    return {"ts": ts, "state": "streaming", "tool": ""}
                if role == "user":
                    return {"ts": ts, "state": "user_msg", "tool": ""}
        elif typ == "event_msg":
            pt = payload.get("type")
            if pt == "task_started":
                return {"ts": ts, "state": "running", "tool": ""}
            if pt == "task_complete":
                return {"ts": ts, "state": "idle", "tool": ""}
        return None


class CodexSource:
    """tail 所有 Codex 会话 rollout 文件。

    每个文件独立维护自己的最新状态（同一会话可能有多个 rollout 文件在写，
    也允许多个线程并存），合并时取「最近有动静且非 idle 的文件」——
    避免某个文件刚 task_complete(idle) 把另一个文件正在进行的
    tool_call/thinking 覆盖掉。
    """

    def __init__(self, app_cfg, idle_stale=DEFAULT_IDLE_STALE, verbose=False):
        self.app_id = app_cfg.get("id", "codex")
        self.app_name = app_cfg.get("name", "Codex")
        self.idle_stale = idle_stale
        self.verbose = verbose
        self._files = {}     # path -> {"offset": int, "latest": evt|None, "last_change": float}
        self._last_change = 0.0
        self.priority = 2    # 精确事件源优先

    def _iter_files(self):
        pattern = os.path.join(CODEX_SESSIONS_DIR, "**", "rollout-*.jsonl")
        return glob.glob(pattern, recursive=True)

    def _tail_file(self, path, offset):
        """读取文件新增内容，返回 (新偏移, [完整行])。"""
        try:
            size = os.path.getsize(path)
        except OSError:
            return offset, []
        if size < offset:          # 文件被轮转/重写
            offset = 0
        if size == offset:
            return offset, []
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                f.seek(offset)
                data = f.read(size - offset)
        except OSError:
            return offset, []
        lines = data.splitlines()
        return size, lines

    def update(self):
        """采样一次，返回合并后的 (state, tool_name, last_change_ts)。"""
        now = time.time()

        # 清理已删除/轮转的旧会话文件，防止 _files 无限增长
        for path in list(self._files.keys()):
            if not os.path.exists(path):
                del self._files[path]

        for path in self._iter_files():
            st = self._files.get(path)
            if st is None:
                # 新文件：从末尾开始，不重放历史
                try:
                    offset = os.path.getsize(path)
                except OSError:
                    offset = 0
                self._files[path] = {"offset": offset, "latest": None, "last_change": 0.0}
                continue
            new_offset, lines = self._tail_file(path, st["offset"])
            st["offset"] = new_offset
            newest = None
            for line in lines:
                evt = CodexEventParser.map(line)
                if evt and evt["ts"] >= 0:
                    if newest is None or evt["ts"] >= newest["ts"]:
                        newest = evt
            if newest is not None:
                old = st["latest"]
                if old is None or newest["ts"] >= old["ts"]:
                    if old is None or newest["state"] != old["state"]:
                        st["last_change"] = newest["ts"] or now
                    st["latest"] = newest

        # 合并：取「最近有动静且非 idle」的文件；全部 idle → idle
        best = None
        for path, st in self._files.items():
            evt = st["latest"]
            if evt is None:
                continue
            state = evt["state"]
            tool = evt.get("tool", "")
            last_ts = evt["ts"] or 0.0

            # 安全网：非 idle 且长时间无新事件 → 视为空闲（防崩溃/卡死残留）
            if state in ("thinking", "streaming", "running") and last_ts:
                age = now - last_ts
                if age > self.idle_stale:
                    if self.verbose:
                        print(f"[codex] {path} {age:.0f}s 无新事件 → 视为 idle")
                    state = "idle"

            if state == "idle":
                continue
            if best is None or last_ts > best[0]:
                best = (last_ts, state, tool)

        if best is None:
            # 全部 idle：记录最近一次状态变化时间（供外部排序）
            latest_ts = 0.0
            for st in self._files.values():
                if st["latest"] and st["latest"]["ts"] > latest_ts:
                    latest_ts = st["latest"]["ts"]
            self._last_change = latest_ts
            return "idle", "", latest_ts

        last_ts, state, tool = best
        self._last_change = last_ts
        return state, tool, last_ts


# ============================================================
# 进程活动源（豆包 / 元宝 / Kimi / 任意软件）
# ============================================================
class ProcessSource:
    """按进程名采样 CPU/网络，推断 thinking/streaming/idle。"""

    def __init__(self, app_cfg, verbose=False):
        self.app_id = app_cfg.get("id", "proc")
        self.app_name = app_cfg.get("name", "AI")
        self.processes = {_norm_proc(p) for p in app_cfg.get("processes", [])}
        self.verbose = verbose
        # per-app 配置：IDE 类工具可覆盖默认值
        self.cpu_threshold = float(app_cfg.get("cpu_threshold", DEFAULT_CPU_THRESHOLD))
        self.detect_title_change = bool(app_cfg.get("detect_title_change", True))
        self.detect_title_keyword = bool(app_cfg.get("detect_title_keyword", True))
        self._last_net = {}
        self._last_cpu = {}
        self._last_time = None
        self._cpu_count = (psutil.cpu_count() or 1) if psutil else 1
        self.inferrer = StateInferrer(
            net_threshold=DEFAULT_NET_THRESHOLD,
            cpu_threshold=self.cpu_threshold,
            idle_delay=DEFAULT_PROCESS_IDLE_DELAY,
        )
        self._last_change = 0.0
        self._last_state = "idle"
        self._warmup = 3
        self._active_streak = 0  # 连续活动采样计数，达到 MIN_ACTIVE_STREAK 才显示非 idle
        self._last_titles = []   # 上次采样的窗口标题，用于检测标题变化
        self.priority = 1    # 进程推断源兜底

    def _get_processes(self):
        if not psutil:
            return []
        out = []
        for p in psutil.process_iter(["pid", "name"]):
            try:
                name = _norm_proc(p.info.get("name"))
                if name in self.processes:
                    out.append(p)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return out

    def _sample(self):
        procs = self._get_processes()
        now = time.time()
        cur_net, cur_cpu = {}, {}
        for p in procs:
            try:
                io = p.io_counters()
                cur_net[p.pid] = (io.bytes_sent, io.bytes_recv)
            except Exception:
                pass
            try:
                ct = p.cpu_times()
                cur_cpu[p.pid] = (ct.user, ct.system)
            except Exception:
                pass

        net_bytes, cpu_pct = 0.0, 0.0
        if self._last_time is not None:
            dt = now - self._last_time
            if dt > 0:
                for pid, (s, r) in cur_net.items():
                    if pid in self._last_net:
                        ls, lr = self._last_net[pid]
                        net_bytes += max(0, s - ls) + max(0, r - lr)
                last_total = sum(u + s for u, s in self._last_cpu.values())
                cur_total = sum(u + s for u, s in cur_cpu.values())
                cpu_pct = ((cur_total - last_total) / dt) * 100.0 / self._cpu_count

        self._last_net, self._last_cpu, self._last_time = cur_net, cur_cpu, now
        return net_bytes, cpu_pct, len(procs)

    def update(self):
        if not psutil:
            return "idle", "", 0.0
        net_bytes, cpu_pct, proc_count = self._sample()
        # 窗口标题检测：关键词匹配 OR 标题变化（比纯CPU更直接，豆包生成时标题可能变化但不含关键词）
        # IDE 类工具（Cursor/VSCode）切文件时标题频繁变化，应关闭 detect_title_change 防误触发
        procs = self._get_processes()
        pids = [p.pid for p in procs]
        titles = _get_process_window_titles(pids)
        title_keyword = _window_title_is_active(titles) if self.detect_title_keyword else False
        title_changed = _titles_changed(self._last_titles, titles) if self.detect_title_change else False
        title_active = title_keyword or title_changed
        self._last_titles = list(titles)

        if self._warmup > 0:
            self._warmup -= 1
            return self._last_state, "", self._last_change

        state = self.inferrer.infer(net_bytes, cpu_pct, proc_count)

        # 标题关键词（如"正在生成"）是强信号，直接判定 thinking
        # 标题变化（切会话/通知）是弱信号，和 CPU 一样走 active_streak 过滤
        if title_keyword:
            state = "thinking"
            self._active_streak = min(self._active_streak + 1, MIN_ACTIVE_STREAK)
        elif title_changed or state != "idle":
            self._active_streak += 1
            if self._active_streak < MIN_ACTIVE_STREAK:
                state = "idle"  # 活动还不够持续，压制为 idle
        else:
            self._active_streak = 0

        if state != self._last_state:
            self._last_state = state
            self._last_change = time.time()
            if self.verbose:
                sig = "kw" if title_keyword else ("chg" if title_changed else "-")
                print(f"[{self.app_id}] {state}  (cpu={cpu_pct:.1f}% title={sig} streak={self._active_streak})")
        return state, "", self._last_change


# ============================================================
# 桥接推送
# ============================================================
class BridgePusher:
    def __init__(self, url=DEFAULT_BRIDGE_URL, verbose=False):
        self.url = url.rstrip("/")
        self.verbose = verbose
        self._last_key = None

    def _post(self, path, data=None):
        body = json.dumps(data or {}, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            self.url + path, data=body, method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=3) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.URLError as e:
            return {"error": str(e)}

    def _get(self, path):
        try:
            with urllib.request.urlopen(self.url + path, timeout=3) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception:
            return {}

    def check_health(self):
        data = self._get("/api/health")
        return bool(data.get("ok"))

    def push(self, state, tool_name="", app_name="", source_type="codex"):
        """推送状态。
        - tool_call：走 /api/tool/start（带工具名 + 工具计数 + 大笑判定）
        - running：走 /api/state/running（桥接转 thinking）
        - idle：走 /api/state/idle（本轮用过工具 → task_done 大笑）
        - 其他：走 /api/state/{state}，再用 /api/state 更新状态卡上的软件名
        """
        # 去重：同样的 (state, tool, app, source_type) 不重复推
        # source_type 纳入 key：系统事件结束推 idle(system) 与 AI 结束推 idle(process) 是不同事件
        key = (state, tool_name, app_name, source_type)
        if key == self._last_key:
            return False
        self._last_key = key

        if state == "tool_call":
            r = self._post("/api/tool/start", {"name": tool_name or app_name})
        else:
            if state == "running":
                state_path = "running"
            else:
                state_path = state
            body = {}
            if state == "idle":
                # 空闲：带上 sourceType 让桥接判断是否该大笑；
                # 始终清空 lastTool，避免系统事件标签（如"在听音乐"）残留
                body = {"sourceType": source_type, "lastTool": ""}
            r = self._post(f"/api/state/{state_path}", body)

            # 非结束态：把软件名写到状态卡（🛠 豆包 / 🛠 Codex）
            if state not in ("idle", "task_done", "error"):
                label = tool_name if tool_name and source_type == "codex" else app_name
                self._post("/api/state", {"state": state, "lastTool": label or ""})

        if self.verbose:
            ok = not r.get("error")
            print(f"[push] {app_name} {state} {tool_name} -> {'OK' if ok else r.get('error')}")
        return True

    def push_system_event(self, state, label):
        """推送系统事件状态（电量/深夜/久坐/音乐）。
        与普通 push 不同：始终把 label 写到状态卡，且不触发大笑（sourceType=system）。
        """
        key = ("system", state, label)
        if key == self._last_key:
            return False
        self._last_key = key

        body = {"sourceType": "system"}
        self._post(f"/api/state/{state}", body)
        # 系统事件始终写状态卡标签（如 "电量不足" / "深夜了"）
        self._post("/api/state", {"state": state, "lastTool": label or ""})

        if self.verbose:
            print(f"[push-system] {state} ({label})")
        return True


# ============================================================
# 配置加载 + 主循环
# ============================================================
def load_apps(config_path=None):
    """加载应用清单：--config > bridge/ai_apps.json > 内置默认。"""
    candidates = []
    if config_path:
        candidates.append(config_path)
    candidates += CONFIG_CANDIDATES

    for path in candidates:
        if path and os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                apps = cfg.get("apps") if isinstance(cfg, dict) else cfg
                if isinstance(apps, list) and apps:
                    return apps, path
            except Exception as e:
                print(f"[警告] 配置文件解析失败 {path}: {e}")
    return list(DEFAULT_APPS), None


def build_sources(apps, idle_stale, verbose):
    sources = []
    for cfg in apps:
        typ = cfg.get("type", "process")
        if typ == "codex":
            sources.append(CodexSource(cfg, idle_stale=idle_stale, verbose=verbose))
        else:
            sources.append(ProcessSource(cfg, verbose=verbose))
    return sources


def run_forever(pusher, sources, interval=DEFAULT_INTERVAL, stop_event=None, verbose=False):
    """监控主循环（可嵌入后台线程）。
    返回后自动推一次 idle 兜底。
    AI 空闲时叠加系统事件（电量/深夜/久坐/音乐）。
    """
    # 预热：进程源先采几个基线样本
    for _ in range(3):
        for s in sources:
            if isinstance(s, ProcessSource):
                s.update()
        if stop_event is not None and stop_event.is_set():
            return
        time.sleep(interval)

    sys_mon = SystemMonitor(verbose=verbose)
    last_active = None  # (state, tool, app_name, source_type)
    last_src_type = "process"  # 最近一次活跃来源类型（兜底按进程源处理）
    active_sys_evt = None  # (evt_dict, start_time) — 正在显示的系统事件
    try:
        while True:
            if stop_event is not None and stop_event.is_set():
                break

            # 每周期累计用户活跃时间（久坐检测，与 AI 状态无关）
            sys_mon.tick()

            best = None  # 高优先级且最近有动静的非空闲源
            for s in sources:
                state, tool, last_ts = s.update()
                if state == "idle":
                    continue
                # 优先级高的源直接胜出；同优先级取最近
                if best is None or s.priority > best[4] or (
                        s.priority == best[4] and last_ts > best[0]):
                    best = (last_ts, state, tool, s.app_name,
                            s.priority,
                            "codex" if isinstance(s, CodexSource) else "process")

            if best is None:
                # === 全部 AI 空闲 ===
                now = time.time()
                # 检查是否有新系统事件触发
                evt = sys_mon.get_event()
                if evt:
                    active_sys_evt = (evt, now)
                # 如果有正在显示的系统事件且未超时，继续推它
                if active_sys_evt:
                    evt, start = active_sys_evt
                    if now - start < evt.get("display", 20):
                        pusher.push_system_event(evt["state"], evt["label"])
                        last_active = None
                    else:
                        active_sys_evt = None
                        # 系统事件结束：用 system 源推 idle，避免被误判为"AI 一轮完成"触发大笑
                        pusher.push("idle", "", "", "system")
                        last_active = ("idle", "", "", "system")
                        last_src_type = "system"
                else:
                    key = ("idle", "", "", last_src_type)
                    if key != last_active:
                        pusher.push("idle", "", "", last_src_type)
                        last_active = key
            else:
                # === AI 活跃中 ===
                active_sys_evt = None
                _, state, tool, app_name, _prio, src_type = best
                last_src_type = src_type
                key = (state, tool, app_name, src_type)
                if key != last_active:
                    pusher.push(state, tool, app_name, src_type)
                    last_active = key

            time.sleep(interval)
    except KeyboardInterrupt:
        pass


def main():
    parser = argparse.ArgumentParser(description="奶娃桌宠 - 全 AI 统一状态监控器")
    parser.add_argument("--bridge", default=DEFAULT_BRIDGE_URL, help=f"桥接服务地址（默认 {DEFAULT_BRIDGE_URL}）")
    parser.add_argument("--config", default=None, help="应用清单 JSON（默认 bridge/ai_apps.json）")
    parser.add_argument("--apps", default=None, help="只监控指定 id，逗号分隔，如 codex,doubao")
    parser.add_argument("--interval", type=float, default=DEFAULT_INTERVAL, help=f"采样间隔秒（默认 {DEFAULT_INTERVAL}）")
    parser.add_argument("--idle-stale", type=float, default=DEFAULT_IDLE_STALE,
                        help=f"Codex 事件超时视为空闲秒（默认 {DEFAULT_IDLE_STALE}）")
    parser.add_argument("--list-apps", action="store_true", help="列出可用应用清单")
    parser.add_argument("--verbose", action="store_true", help="详细输出")
    args = parser.parse_args()

    apps, cfg_path = load_apps(args.config)
    if args.apps:
        wanted = {a.strip() for a in args.apps.split(",") if a.strip()}
        apps = [a for a in apps if a.get("id") in wanted]

    if args.list_apps:
        print(f"配置文件: {cfg_path or '(内置默认)'}")
        for a in apps:
            extra = ""
            if a.get("type") == "process":
                extra = " 进程=" + ",".join(a.get("processes", []))
            print(f"  {a.get('id'):<10} {a.get('name',''):<12} [{a.get('type')}]{extra}")
        return

    if not apps:
        print("[错误] 没有可监控的软件，请在 --apps / --config 里指定")
        return

    print("=" * 60)
    print("  奶娃桌宠 - 全 AI 统一状态监控器")
    print("=" * 60)
    print(f"  桥接服务   : {args.bridge}")
    print(f"  采样间隔   : {args.interval}s")
    print(f"  配置文件   : {cfg_path or '(内置默认)'}")
    for a in apps:
        extra = ""
        if a.get("type") == "process":
            extra = " 进程=" + ",".join(a.get("processes", []))
        print(f"  监控       : {a.get('name','')} ({a.get('id')}) [{a.get('type')}]{extra}")
    print("=" * 60)

    pusher = BridgePusher(args.bridge, verbose=args.verbose)
    if not pusher.check_health():
        print("[警告] 桥接服务不可用，请先启动 bridge/server.py")
        print("       监控会继续运行，桥接恢复后自动推送")

    sources = build_sources(apps, args.idle_stale, args.verbose)
    run_forever(pusher, sources, interval=args.interval, verbose=args.verbose)


if __name__ == "__main__":
    main()
