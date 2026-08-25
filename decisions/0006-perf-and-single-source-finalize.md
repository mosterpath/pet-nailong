# 0006: 性能优化与文法单源收尾

- 状态：已采纳
- 日期：2026-08-25
- 相关文件：bridge/ai_monitor.py, bridge/server.py, bridge/auto_monitor.py, bridge/push.py, bridge/system_events.py, helper/main.py, helper/tray.py, helper/pet_window.py, helper/all_in_one.py, scripts/gates/verify.py

## 背景

ADR 0005 留下待办：main.py / tray.py / ai_monitor.py / push.py / system_events.py 仍硬编码状态名。
另外发现两个问题：

- 性能：ai_monitor 每个进程源每周期各自调用 `psutil.process_iter` + `EnumWindows` 枚举全部进程/窗口；
  23 个源时每周期重复 23 次全量系统枚举，监控自身 CPU 开销大（实测 20 周期 0.795s）
- 重复：server.py 的 `PackRegistry` 与 helper/packs.py 的 `PackLoader` 是同一套 pack 扫描逻辑的两份实现

## 决策

1. 性能：新增 `ProcessSnapshot`，`run_forever` 每周期只做一次 `process_iter` + `EnumWindows`，
   所有进程源共享；`ProcessSource.update()` 增加可选 `snapshot` 参数，独立运行时自动回退单源枚举。
   实测进程枚举加速约 **22x**（23 源 20 周期 0.795s → 0.036s）
2. 文法单源收尾：main.py / tray.py / ai_monitor.py / push.py / system_events.py / auto_monitor.py
   全部改为从 state_table 导入常量（各 bridge 文件自带 helper 目录 sys.path 引导，支持独立运行）；
   `"running"` 保留字面量（Codex 事件伪状态，不在状态表内）
3. 去重：server.py 删除 `PackRegistry`，改用 `helper/packs.PackLoader`（Pack 对象属性直接映射，行为不变）
4. 门禁：目标文件扩到 9 个（含 auto_monitor）；新增 return 状态字符串字面量检查；
   新增目标模块导入冒烟检查（子进程导入 9 个目标模块，拦截 import 期错误如未定义 os/sys）；
   语法检查改用 `ast.parse`（不写 `__pycache__` 字节码缓存）
5. 清理 pyflakes 死代码：未使用导入（protocol、QGraphicsDropShadowEffect、IdleAnimator、os、OPTIONAL_STATES）
   与死变量（ai_monitor `title_active`）

## 理由

- 进程枚举是监控热路径（每 0.5s 一次），共享快照把 O(源数) 次系统调用降为 1 次
- AST return 检查补上 `return "idle"` 这类硬编码漏洞（之前只能查调用参数和字典 key）
- `ast.parse` 比 `py_compile` 快且不写字节码缓存，门禁更干净
- PackLoader 已是 helper 侧成熟实现（含路径穿越防护），server 复用即可，无需第二份

## 后果

正面：监控自身 CPU 显著下降；文法单源闭环（门禁覆盖全部活跃 Python 源文件）；包扫描逻辑只剩一份。
负面：`ProcessSource.update` 增加可选参数，独立调用时自动回退，行为不变；
`host/index.js` 仍为 JS 平行实现（跨语言无法复用 Python 单源），维持 PET_STATE 常量。
