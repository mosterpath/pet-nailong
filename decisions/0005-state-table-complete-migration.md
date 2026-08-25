# 0005: 状态表补全与硬编码迁移

- 状态：已采纳
- 日期：2026-08-25
- 相关文件：helper/state_table.py, helper/pet_window.py, helper/packs.py, bridge/server.py, scripts/gates/verify.py

## 背景

ADR 0004 建立 state_table.py 作为状态机文法单源，但迁移未闭环：

- STATE_TABLE 缺 `tool_call` / `user_msg` 两个状态，而 host/index.js 与代码已在用（旧门禁的状态名清单也漏了这两个）
- pet_window.py / packs.py / server.py 仍硬编码状态名字符串，门禁仅给出 3 个 WARN，未形成硬约束
- 门禁只检查 `apply_state` 字面量，漏掉 `set_state` / `force_set`，且状态名清单是手工维护的正则，新增状态容易漏

## 决策

1. STATE_TABLE 补两行：
   - `user_msg`：burst 1500ms，放在 `error` 之后（与 server/host 优先级 error > user_msg > task_done 一致）
   - `tool_call`：persistent，放在 `streaming` 与 `thinking` 之间（工作周期 idle → thinking → tool_call → streaming → task_done）
2. state_table 导出每个状态的命名常量（`STATE_xxx`）与 `SESSION_STATES`（桥接层 7 个逻辑状态白名单，与 host/index.js PET_STATE 对齐）
3. pet_window.py / packs.py / server.py 全部改为从 state_table 导入常量，消除状态名硬编码；server.py 增加 helper 目录 sys.path 引导以便独立运行
4. 门禁升级（scripts/gates/verify.py）：
   - 未导入 state_table：WARN 升 FAIL
   - 状态调用（apply_state / set_state / force_set）出现字符串字面量：FAIL
   - 状态定义型字典（STATE_LABELS 等）key 出现状态名字符串：WARN 提示
   - 改为 AST 静态分析，状态名清单从 ALL_STATES 自动生成，不再手工维护正则
5. state_table.validate() 校验常量与表一致性，门禁第 2 节首先执行

## 理由

- 常量导出让"禁止硬编码"可执行：状态调用处出现字符串字面量直接 FAIL，不再依赖人工 review
- AST 检查天然免疫注释/字符串误报（旧正则会把注释里的 `force_set("idle")` 当代码），且新增状态自动覆盖
- 两个新状态的位置与既有 host/index.js + server.py 的优先级/冷却语义一致，运行期行为零变化
- 只扫描"赋值给变量的字典"，避免把 HTTP 响应字段名（`{"error": ...}`）误当状态定义

## 后果

正面：文法单源闭环，门禁 0 警告；新增状态 = STATE_TABLE 加一行 + 导出常量，门禁自动覆盖。
负面：运行期行为不变——窗口 apply_state 仍直接应用、不按表内优先级裁决；表内行序目前是文档性 + 未来 resolve_state() 的依据。

待办（未纳入本次范围，后续如需彻底单源可一并迁移）：
- helper/main.py 的 DEMO_STATES、helper/tray.py 的 `image_for("idle")`
- bridge/ai_monitor.py、bridge/push.py、bridge/system_events.py 的状态字符串
- host/index.js 是 JS 侧平行实现，维持 PET_STATE 常量定义（跨语言无法复用 Python 单源）
