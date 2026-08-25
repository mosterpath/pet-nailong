# 奶娃桌宠 - 独立桥接服务（bridge）

让桌宠在**非 Cordis / DSH 环境**下也能感知 AI 工作状态。

## 三个组件

| 组件 | 作用 |
|------|------|
| `server.py` | 桥接 Host：本地 HTTP 服务，接收状态推送，转发给桌宠（stdin JSON-lines） |
| `ai_monitor.py` | **全 AI 统一状态监控器**（推荐）：自动监控所有主流 AI 软件，推送给桥接 |
| `auto_monitor.py` | 旧版单软件监控器：只监控豆包进程活动（已被 ai_monitor.py 取代） |

## 快速开始

### 1. 启动桥接服务

双击 `bridge/start-bridge.bat`，或命令行：

```bash
python bridge/server.py
```

启动后桌宠窗口会自动弹出，服务监听 `127.0.0.1:18923`。

### 2. 启动全 AI 状态监控（推荐）

双击 `bridge/start-ai-monitor.bat`，或命令行：

```bash
python bridge/ai_monitor.py
```

这样桌宠会自动跟随**所有主流 AI 软件**的工作状态：

- **Codex**（精确事件源）：读 `~/.codex/sessions` 会话 JSONL，精确识别
  思考中 / 调工具（含工具名）/ 回复中 / 来消息 / 空闲
- **进程活动源**（豆包、腾讯元宝、Kimi、DeepSeek、Claude、Gemini、
  通义千问、智谱清言、文心一言、讯飞星火、即梦、剪映、Cursor、Trae、
  Windsurf、Chatbox、Cherry Studio、LM Studio、Ollama、Jan、Perplexity、
  Copilot）：按进程名采样 CPU + 网络，推断 思考中 / 回复中 / 空闲

> 谁在干活桌宠就反映谁，状态卡会显示软件名（如 `🛠 豆包` / `🛠 Codex`）。
> Codex 调工具时优先显示真实工具名。

### 3. 管理监控清单

所有软件都在 `bridge/ai_apps.json` 里，**想加/删软件直接编辑这个文件**
（保存后重启 ai_monitor 生效）。进程名不区分大小写，没装的软件自动跳过。

```bash
python bridge/ai_monitor.py --list-apps      # 查看当前清单
python bridge/ai_monitor.py --apps codex,doubao   # 只监控部分软件
python bridge/ai_monitor.py --config my.json      # 用自定义清单
python bridge/ai_monitor.py --verbose             # 详细日志
```

## 手动推送（可选）

如果不想用自动监控，也可以用 `push.py` 手动推：

```bash
python bridge/push.py thinking            # AI 开始思考
python bridge/push.py tool_call --name calculator
python bridge/push.py streaming           # AI 开始回复
python bridge/push.py idle                # 空闲（本轮用过工具→大笑）
python bridge/push.py error               # 出错（抖动）
python bridge/push.py pack nailong        # 切换表情包
python bridge/push.py status              # 查询当前状态
```

## HTTP API 一览

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/health` | 健康检查（含 helper 进程是否存活） |
| GET | `/api/state` | 获取当前状态 |
| POST | `/api/state` | 完整状态更新（JSON body，可带 lastTool） |
| POST | `/api/state/{state}` | 快捷推送状态 |
| POST | `/api/tool/start` | 标记工具调用开始（`{name}`） |
| POST | `/api/tool/end` | 标记工具调用结束 |
| POST | `/api/pack` | 切换表情包（`{packId}`） |
| GET | `/api/packs` | 列出可用表情包 |
| POST | `/api/reset` | 重置状态为 idle |

### 可用状态值

- `idle` — 空闲（本轮用过工具时自动转 `task_done` 大笑）
- `thinking` — 思考中（托腮）
- `tool_call` — 调工具中（震惊围观）
- `streaming` — 回复中（严肃）
- `task_done` — 任务完成（大笑 GIF + 笑声）
- `error` — 出错（抖动）
- `user_msg` — 来消息
- `running` — agent 开始运行（自动转 thinking）

## 架构

```
AI 软件（Codex 会话事件 / 豆包等进程活动）
       │  ai_monitor.py 统一采样 + 合并
       ▼
bridge/server.py  (本地 HTTP 服务 + 状态机 + 表情包注册表)
       │  stdin JSON-lines
       ▼
helper/main.py  (PyQt5 透明置顶桌宠窗口)
       │
       ▼
packs/<表情包>/pack.json + 图片/GIF/音频
```

## 注意事项

1. **只能有一个桌宠实例**：桥接服务会自己拉起 helper，如果之前手动开了桌宠 exe，先退出再启动桥接。
2. **端口被占用**：默认 18923，被占用用 `--port` 换一个，同时 push.py 要加 `--url` 指定新端口。
3. **旧 auto_monitor.py 已停用**：它只监控豆包，会和 ai_monitor.py 重复推送打架。统一用 ai_monitor.py。
4. **Codex 桌面（ChatGPT.exe）**：它就是 Codex 的壳，已由 codex 事件源精确覆盖，不要单独加 chatgpt 进程源。
5. **Python 依赖**：桥接和监控器只用标准库 + psutil（`pip install psutil`），拉起 helper 需要 PyQt5；用 exe 模式则无需 Python/PyQt5。
