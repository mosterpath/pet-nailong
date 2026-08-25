# 0004: 声明式状态机文法单源

- 状态：已采纳
- 日期：2026-08-25
- 相关文件：helper/state_table.py, helper/pet_window.py, helper/packs.py

## 背景

状态名散落在 pet_window.py、packs.py、server.py 等多处，以硬编码字符串形式存在（"idle"、"thinking"、"task_done" 等）。状态优先级通过 if/else 链隐式表达，新增状态时容易遗漏校验，导致：
- 微动作覆盖 AI 真实状态（需 _state_gen 补丁）
- 系统事件结束后误触发大笑
- 短暂表情回退逻辑复杂且易出错

参考鲸鱼娘的 STATE_TABLE 设计（行序即优先级，文法单源，verify-spec-states 门禁校验）。

## 决策

创建 `helper/state_table.py` 作为状态机文法单源：
- STATE_TABLE 声明所有状态，行序即优先级
- 每个状态声明 type（persistent/burst/transient）、duration_ms、bubble_style
- 派生常量（ALL_STATES、PRIORITY、DEFAULT_DURATION 等）从表自动生成
- pet_window.py、packs.py、门禁脚本均从此导入，禁止硬编码状态名
- 提供 resolve_state() 统一冲突解决逻辑

## 理由

- 优先级显式化：新增状态只需在表中插入一行，位置决定优先级
- 可校验：门禁脚本可检查代码中所有状态字符串是否在 ALL_STATES 中
- 可测试：resolve_state() 是纯函数，可单元测试
- 减少补丁：_state_gen 代计数器仍是必要的（瞬发态回退检测），但优先级冲突由表统一管理

## 后果

正面：状态管理可维护性大幅提升，新增状态有统一入口。
负面：需要逐步迁移现有代码中的硬编码状态名到 state_table 导入，迁移期间存在双轨。门禁脚本会标记未迁移的硬编码。
