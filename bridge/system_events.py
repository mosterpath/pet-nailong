# -*- coding: utf-8 -*-
"""
奶娃桌宠 - 系统事件监控
========================
在 AI 空闲时，检测系统状态并让桌宠做出反应：
- 电量低 (<20%)     → 担忧表情 + 提醒充电
- 深夜 (0:00-6:00)  → 打哈欠 + 提醒睡觉
- 久坐 (>2小时)     → 伸懒腰 + 提醒活动
- 音乐播放中        → 哼歌 + 气泡

系统事件只在所有 AI 源空闲时生效，不打断 AI 工作状态。
"""
import os
import time
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


# 音乐播放器进程名（不区分大小写，去 .exe）
MUSIC_PROCESSES = {
    "spotify", "cloudmusic", "netease cloud music", "qqmusic",
    "kuwomusic", "kugou", "apple music", "itunes",
    "foobar2000", "aimp", "winamp", "vlc", "musicbee",
    "amazing", "lxmusic", "yesplaymusic",
}

# 系统事件定义：(id, 状态, 状态卡文字, 冷却秒数, 显示秒数, 检测方法名)
# 状态只用 thinking / idle，不用 error（error 会自动回退到 idle）
SYSTEM_EVENTS = [
    {
        "id": "low_battery",
        "state": "thinking",
        "label": "电量不足",
        "cooldown": 600,
        "display": 20,
        "check": "check_battery",
    },
    {
        "id": "late_night",
        "state": "idle",
        "label": "深夜了",
        "cooldown": 1800,
        "display": 30,
        "check": "check_late_night",
    },
    {
        "id": "long_sitting",
        "state": "thinking",
        "label": "该休息了",
        "cooldown": 3600,
        "display": 25,
        "check": "check_long_sitting",
    },
    {
        "id": "music_playing",
        "state": "idle",
        "label": "在听音乐",
        "cooldown": 300,
        "display": 15,
        "check": "check_music",
    },
]


class _LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]


def _get_idle_seconds():
    """获取用户最后一次输入（键盘/鼠标）到现在的秒数。"""
    if not _HAS_WIN32:
        return 0
    lii = _LASTINPUTINFO()
    lii.cbSize = ctypes.sizeof(_LASTINPUTINFO)
    try:
        ctypes.windll.user32.GetLastInputInfo(ctypes.byref(lii))
        tick = ctypes.windll.kernel32.GetTickCount()
        return (tick - lii.dwTime) / 1000.0
    except Exception:
        return 0


class SystemMonitor:
    """系统事件监控器。在 AI 空闲时调用 get_event() 获取当前应触发的事件。"""

    def __init__(self, verbose=False):
        self.verbose = verbose
        self._last_fired = {}   # event_id -> 上次触发时间戳
        self._last_tick = time.time()
        self._active_seconds = 0.0  # 累计活跃使用秒数
        self._music_cache = False
        self._music_cache_time = 0.0

    def tick(self):
        """每个采样周期调用一次（无论 AI 是否空闲），累计用户活跃时间。"""
        now = time.time()
        dt = now - self._last_tick
        self._last_tick = now
        if dt <= 0 or dt > 60:  # 异常大的间隔（休眠/挂起）不累计
            return
        idle_secs = _get_idle_seconds()
        if idle_secs < 300:  # 用户在活跃使用（空闲 < 5 分钟）
            self._active_seconds += dt

    # ---- 各事件检测方法 ----

    def check_battery(self):
        """电量低于 20% 且未充电时触发。"""
        if not psutil:
            return False
        try:
            bat = psutil.sensors_battery()
            if bat is None:
                return False
            if bat.power_plugged:
                return False
            return bat.percent < 20
        except Exception:
            return False

    def check_late_night(self):
        """0:00 - 6:00 触发。"""
        hour = datetime.now().hour
        return 0 <= hour < 6

    def check_long_sitting(self):
        """用户活跃使用电脑超过 2 小时触发（累计时间在 tick() 中维护）。"""
        if self._active_seconds >= 7200:
            self._active_seconds = 0.0  # 触发后重置
            return True
        return False

    def check_music(self):
        """检测音乐播放器进程是否在运行（缓存 10 秒，避免每 0.5 秒遍历所有进程）。"""
        if not psutil:
            return False
        now = time.time()
        if now - self._music_cache_time < 10:
            return self._music_cache
        self._music_cache_time = now
        found = False
        try:
            for p in psutil.process_iter(["name"]):
                try:
                    name = (p.info.get("name") or "").lower()
                    if name.endswith(".exe"):
                        name = name[:-4]
                    if name in MUSIC_PROCESSES:
                        found = True
                        break
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
        except Exception:
            pass
        self._music_cache = found
        return found

    # ---- 主接口 ----

    def get_event(self):
        """返回当前应触发的系统事件，或 None。
        按 SYSTEM_EVENTS 顺序优先级检测，命中且过了冷却则返回。
        """
        now = time.time()
        for evt in SYSTEM_EVENTS:
            check_fn = getattr(self, evt["check"], None)
            if check_fn is None:
                continue
            try:
                triggered = check_fn()
            except Exception:
                continue
            if not triggered:
                continue
            # 冷却检查
            last = self._last_fired.get(evt["id"], 0)
            if now - last < evt["cooldown"]:
                continue
            self._last_fired[evt["id"]] = now
            if self.verbose:
                print(f"[system] 事件触发: {evt['id']} ({evt['label']})")
            return evt
        return None

    def reset(self):
        """AI 开始工作时调用：仅同步 tick 时间，避免休眠后大间隔误累计。
        不清空久坐时间（用户是否久坐与 AI 是否工作无关）。
        """
        self._last_tick = time.time()
