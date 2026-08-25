# -*- coding: utf-8 -*-
"""
奶娃桌宠 - 状态机文法单源 (Single Source of Truth)

所有状态名、优先级、类型、默认时长在此统一定义。
pet_window.py、packs.py、server.py、门禁校验脚本均从这里导入，
禁止在别处硬编码状态名字符串。

参考鲸鱼娘 STATE_TABLE 设计：行序即优先级，首个命中即返回。
"""

# ============================================================
# 状态名常量（唯一允许的引用方式：from state_table import STATE_xxx）
# 新增状态：在 STATE_TABLE 加一行，并在此导出同名常量（validate() 会校验一致性）
# ============================================================
STATE_DRAGGING = "dragging"
STATE_LAUGH = "laugh"
STATE_ERROR = "error"
STATE_USER_MSG = "user_msg"
STATE_CLICK_JUMP = "click_jump"
STATE_CLICK_SWAY = "click_sway"
STATE_MICRO_JUMP = "micro_jump"
STATE_MICRO_SWAY = "micro_sway"
STATE_MICRO_FACE = "micro_face"
STATE_LOW_BATTERY = "low_battery"
STATE_LATE_NIGHT = "late_night"
STATE_LONG_SITTING = "long_sitting"
STATE_MUSIC_PLAYING = "music_playing"
STATE_TASK_DONE = "task_done"
STATE_STREAMING = "streaming"
STATE_TOOL_CALL = "tool_call"
STATE_THINKING = "thinking"
STATE_IDLE = "idle"

# ============================================================
# 状态类型
# ============================================================
PERSISTENT = "persistent"   # 持续态：保持直到被新状态覆盖
BURST = "burst"             # 事件爆发态：有固定时长，超时自动回退
TRANSIENT = "transient"     # 瞬发交互态：用户触发，短时间后回退

# ============================================================
# 状态表（行序 = 优先级，从上到下递减）
# 字段：state, type, duration_ms(仅burst/transient), desc, bubble_style
# bubble_style 取值是 pet_window.BUBBLE_STYLES 的 key（气泡样式名，不是状态名）
# ============================================================
STATE_TABLE = [
    # ---- 最高优先：用户直接操作 ----
    {"state": STATE_DRAGGING,    "type": PERSISTENT, "duration_ms": 0,    "desc": "拖拽中",          "bubble_style": None},

    # ---- 事件爆发：任务完成/失败/来消息 ----
    {"state": STATE_LAUGH,       "type": BURST,      "duration_ms": 8000, "desc": "大笑（任务完成）",  "bubble_style": "task_done"},
    {"state": STATE_ERROR,       "type": BURST,      "duration_ms": 4000, "desc": "出错/失败",        "bubble_style": "error"},
    {"state": STATE_USER_MSG,    "type": BURST,      "duration_ms": 1500, "desc": "来消息",          "bubble_style": "normal"},

    # ---- 瞬发交互：点击互动 ----
    {"state": STATE_CLICK_JUMP,  "type": TRANSIENT,  "duration_ms": 1500, "desc": "点击蹦跳",        "bubble_style": None},
    {"state": STATE_CLICK_SWAY,  "type": TRANSIENT,  "duration_ms": 1500, "desc": "点击摇摆",        "bubble_style": None},
    {"state": STATE_MICRO_JUMP,  "type": TRANSIENT,  "duration_ms": 2000, "desc": "空闲微动作-蹦跳",  "bubble_style": None},
    {"state": STATE_MICRO_SWAY,  "type": TRANSIENT,  "duration_ms": 2500, "desc": "空闲微动作-摇摆",  "bubble_style": None},
    {"state": STATE_MICRO_FACE,  "type": TRANSIENT,  "duration_ms": 1800, "desc": "空闲微动作-变脸",  "bubble_style": None},

    # ---- 系统事件爆发 ----
    {"state": STATE_LOW_BATTERY,   "type": BURST,    "duration_ms": 20000, "desc": "低电量提醒",      "bubble_style": "system"},
    {"state": STATE_LATE_NIGHT,    "type": BURST,    "duration_ms": 30000, "desc": "深夜提醒",        "bubble_style": "system"},
    {"state": STATE_LONG_SITTING,  "type": BURST,    "duration_ms": 25000, "desc": "久坐提醒",        "bubble_style": "system"},
    {"state": STATE_MUSIC_PLAYING, "type": BURST,    "duration_ms": 15000, "desc": "音乐播放中",      "bubble_style": "system"},

    # ---- AI 持续态（底层）----
    {"state": STATE_TASK_DONE,   "type": PERSISTENT, "duration_ms": 0,    "desc": "任务完成（无大笑）","bubble_style": "task_done"},
    {"state": STATE_STREAMING,   "type": PERSISTENT, "duration_ms": 0,    "desc": "AI 输出中",       "bubble_style": "thinking"},
    {"state": STATE_TOOL_CALL,   "type": PERSISTENT, "duration_ms": 0,    "desc": "AI 调工具中",     "bubble_style": "thinking"},
    {"state": STATE_THINKING,    "type": PERSISTENT, "duration_ms": 0,    "desc": "AI 思考中",       "bubble_style": "thinking"},

    # ---- 兜底 ----
    {"state": STATE_IDLE,        "type": PERSISTENT, "duration_ms": 0,    "desc": "空闲待机",        "bubble_style": "normal"},
]

# ============================================================
# 派生常量（从 STATE_TABLE 自动生成，禁止手动维护）
# ============================================================
ALL_STATES = [row["state"] for row in STATE_TABLE]
STATE_NAMES = set(ALL_STATES)
PERSISTENT_STATES = {row["state"] for row in STATE_TABLE if row["type"] == PERSISTENT}
BURST_STATES = {row["state"] for row in STATE_TABLE if row["type"] == BURST}
TRANSIENT_STATES = {row["state"] for row in STATE_TABLE if row["type"] == TRANSIENT}

# 优先级映射：state -> priority index（越小越优先）
PRIORITY = {row["state"]: i for i, row in enumerate(STATE_TABLE)}

# 默认时长映射
DEFAULT_DURATION = {row["state"]: row["duration_ms"] for row in STATE_TABLE if row["duration_ms"] > 0}

# 气泡样式映射
BUBBLE_STYLE = {row["state"]: row["bubble_style"] for row in STATE_TABLE if row["bubble_style"]}

# 桥接/会话层可接收的逻辑状态白名单（HTTP API 只接受这些；与 host/index.js PET_STATE 对齐）
SESSION_STATES = (
    STATE_IDLE, STATE_THINKING, STATE_TOOL_CALL, STATE_STREAMING,
    STATE_TASK_DONE, STATE_ERROR, STATE_USER_MSG,
)

# 需要素材的状态（pack.json 中应提供图片）
# laugh 用 laugh.gif 单独配置，不在 states 里
ASSET_STATES = [STATE_IDLE, STATE_THINKING, STATE_STREAMING, STATE_TASK_DONE, STATE_ERROR]
# 可选状态（没有也能运行，回退 idle）
OPTIONAL_STATES = [STATE_CLICK_JUMP, STATE_CLICK_SWAY, STATE_MICRO_JUMP, STATE_MICRO_SWAY,
                   STATE_MICRO_FACE, STATE_LOW_BATTERY, STATE_LATE_NIGHT,
                   STATE_LONG_SITTING, STATE_MUSIC_PLAYING]


def validate():
    """校验状态名常量与 STATE_TABLE 一致性，返回错误列表（空列表 = 一致）。
    门禁脚本调用；新增状态时若漏加常量会在此被拦下。"""
    errors = []
    for row in STATE_TABLE:
        const_name = "STATE_" + row["state"].upper()
        if globals().get(const_name) != row["state"]:
            errors.append("缺少常量 %s（应为 %r）" % (const_name, row["state"]))
    known = set(ALL_STATES)
    for name, value in list(globals().items()):
        if name.startswith("STATE_") and isinstance(value, str) and value not in known:
            errors.append("常量 %s=%r 不在 STATE_TABLE 中" % (name, value))
    return errors


def is_valid_state(state):
    """检查状态名是否合法。"""
    return state in STATE_NAMES


def higher_priority(state_a, state_b):
    """返回优先级更高的状态。a 比 b 优先返回 a，否则返回 b。"""
    if PRIORITY.get(state_a, 999) < PRIORITY.get(state_b, 999):
        return state_a
    return state_b


def resolve_state(requested, current):
    """
    状态冲突解决：请求状态与当前状态比较，返回应采用的状态。
    高优先级状态可以覆盖低优先级；同优先级取请求状态。
    """
    if requested == current:
        return current
    if PRIORITY.get(requested, 999) <= PRIORITY.get(current, 999):
        return requested
    return current
