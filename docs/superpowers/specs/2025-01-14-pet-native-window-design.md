# 奶娃桌宠 v2.0 设计：透明桌面原生窗口

日期：2025-01-14
状态：已获用户批准（对话中确认，含取舍：不做喂食）

## 目标

把桌宠从"浏览器 overlay 里的一张图"升级到 DSH 大肥鱼（dsh-dafeiyu）级别：
**透明无边框置顶原生窗口，直接显示在 Windows 桌面上**，由 DSH 真实 Agent 事件驱动，
离开浏览器也能看到；同时保留表情包框架与浏览器 overlay 降级能力。

## 参考

- dsh-dafeiyu（QCYTSN/dsh-dafeiyu）：DSH host 插件 spawn Python helper，
  JSON-lines over stdio 协议，PySide6 透明置顶窗口。
- dafeiyu-pet（1190fasheqi/dafeiyu-pet）：三视图行走 / 拖拽 / 交互菜单 / 台词系统。

## 总体架构

```
DSH / Cordis 应用
   └─ pet-nailong 插件 host/index.js（增强）
        │ spawn 子进程 + JSON-lines（stdio）
        ▼
   helper/main.py   PyQt5 桌面程序
        │ 无边框透明置顶窗口 + 托盘
        ▼
   packs/<表情包>/pack.json   （素材框架复用，helper 直接读同一 schema）
```

helper 不可用时 host 优雅降级：继续走浏览器 overlay（v1.x 能力不丢）。

## helper/ 文件结构

```
helper/
├── main.py          入口：--demo 独立演示 / 默认协议模式；QApplication + 托盘
├── protocol.py      JSON-lines 协议：消息编码/解码、常量
├── packs.py         表情包加载：扫描 packs/、解析 pack.json、取素材路径
├── pet_window.py    透明置顶窗口：精灵渲染、动画、交互、气泡、状态卡
├── animations.py    动画：呼吸/摇摆/蹦跳/走路/抖动/大笑
├── tray.py          托盘图标与菜单
├── requirements.txt PyQt5
└── README.md
```

## 窗口能力

- 无边框透明置顶：`Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool` +
  `WA_TranslucentBackground`；窗口尺寸约 200×260，精灵居中
- **动画**（基于现有静态 PNG，QGraphicsItem 变换模拟）：
  - 呼吸：scale 1.00↔1.02 循环
  - 摇摆：rotation ±2°（待机随机触发）
  - 蹦跳：点击时 scale 弹跳
  - 走路：屏幕底部随机漫步（x 移动 + y 浮动 + 左右镜像 `scale(-1,1)`）
  - 大笑：task_done 播放包内 laugh.gif（QMovie）；出错抖动
- **三种移动模式**：自由散步 / 跟随鼠标 / 原地待着（右键菜单或托盘切换）
- **交互**：
  - 左键拖拽（拖拽期间停下，松手随机说话）
  - 单击：蹦跳 + 随机梗气泡（pack.clickBubbles）
  - 右键：菜单（移动模式 / 大小 / 鼠标穿透 / 置顶 / 隐藏 / 退出）
  - 托盘：同款菜单 + 显示/隐藏 + 退出
- **状态卡**：窗口内小卡片，显示当前 Agent 状态文案、工具次数、表情包名
- **气泡系统**：状态进入气泡（pack.bubbles）+ 随机梗（clickBubbles），2.5s 自动隐藏
- **思维链心声**：thinking 时小概率冒灰色斜体气泡（pack.thinkingLines，缺省内置）
- 配置记忆：位置/大小/模式/穿透/置顶 存 helper/config.json

## 协议（JSON lines over stdio，UTF-8）

host → helper：
- `{"kind":"state","state":"thinking","packId":"nailong","toolCount":2,"bubbles":{...},"clickBubbles":[...],"laugh":{...},"thinkingLines":[...]}`
- `{"kind":"pack","packId":"variants"}`
- `{"kind":"ping"}` / `{"kind":"shutdown"}`

helper → host：
- `{"kind":"ready"}`
- `{"kind":"event","name":"click"|"drag"|"hidden"|"exited"|"mode",...}`
- `{"kind":"pong"}`

## Host 集成（host/index.js 增强）

- 激活时探测 `python`，spawn `helper/main.py --packs <绝对路径>`（try/catch，失败降级浏览器 overlay）
- 状态机变更 → 组 state 消息写 helper stdin；`pet-pack-set` → 发 pack 消息
- helper 崩溃/退出 → 自动重启（限频）；插件停用 → shutdown + kill
- 浏览器 overlay 逻辑原样保留（降级路径）

## 独立演示（立即可见）

`python helper/main.py --demo --packs <项目>/packs`：
- 无 host 自跑：自动循环展示 7 种状态 + 全交互可用（点击/拖拽/右键/托盘）
- 完成后在用户桌面直接弹出透明窗口

## 打包交付

- zip 增加 `helper/`（Python 源码 + requirements.txt）
- `build.ps1` 增加 helper 目录；README 更新安装说明（需 Python3.8+ & PyQt5）
- v2.1 可选：PyInstaller 打独立 exe

## 验收标准

1. `python helper/main.py --demo` 弹出透明置顶窗口：呼吸/摇摆/走路/蹦跳动画正常
2. 单击气泡、拖拽、右键菜单（模式/穿透/置顶/隐藏）、托盘全部可用
3. host 激活自动 spawn helper，Agent 状态实时驱动窗口（状态卡/气泡/大笑 GIF）
4. 切表情包 → helper 同步换素材；helper 崩溃自动重启
5. helper 不可用时浏览器 overlay 正常降级
6. zip 打包包含 helper/，README 说明安装
