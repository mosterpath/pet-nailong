# 0002: 进程源大笑推断参数

- 状态：已采纳
- 日期：2026-08-25
- 相关文件：bridge/ai_monitor.py, bridge/server.py

## 背景

Windows 上 psutil.io_counters() 读不到进程网络 IO（权限问题），进程源只能靠 CPU 活动推断 AI 状态。早期参数（CPU阈值6%、idle_delay 5秒、MIN_ACTIVE_STREAK 2、大笑最短4秒）导致频繁误触发大笑——短暂的 CPU 波动被 idle_delay 拉长为 6 秒以上的"持续活动"，超过 4 秒阈值就误笑。

## 决策

调整为：CPU阈值8%、idle_delay 2秒、MIN_ACTIVE_STREAK 3（需1.5秒连续活动）、大笑最短5秒。窗口标题变化不再直接判定 thinking，改为和 CPU 一样走 active_streak 过滤；仅标题含明确关键词（"正在生成"等）直接判定 thinking。

## 理由

- idle_delay 从 5 秒降到 2 秒是最关键的修复：之前 CPU 高 1 秒 + idle_delay 5 秒 = active_duration 6 秒，必然误触发；现在 CPU 高 1 秒 + 2 秒 = 3 秒 < 5 秒阈值
- MIN_ACTIVE_STREAK 从 2 提到 3，过滤更多单次后台波动
- 标题变化是弱信号（切会话/通知都会变标题），不应绕过 streak 过滤；关键词是强信号，可以即时响应
- 大笑最短从 4 秒提到 5 秒，进一步提高门槛

## 后果

正面：误触发大笑大幅减少。
负面：特别短的回答（< 5 秒持续活动）可能不笑。这是精度和召回的权衡，进程源天然无法精确区分，接受这个代价。Codex CLI JSONL 源不受影响（精确事件）。
