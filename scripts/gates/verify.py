# -*- coding: utf-8 -*-
"""
奶娃桌宠 - 门禁校验脚本

运行：python scripts/gates/verify.py
检查项：
1. Python 语法检查（所有 .py 文件）
2. 状态名一致性（文法单源：状态调用必须用 state_table 常量，未导入/硬编码即失败）
3. pack.json 素材完整性（核心状态必须有图片文件）
4. config.json 字段合法性
5. ai_apps.json 格式校验
6. 目标模块导入冒烟（拦截 import 期错误，如未定义的 os/sys）
"""
import ast
import json
import os
import subprocess
import sys

# 项目根目录（脚本在 scripts/gates/ 下，上两级是根）
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "helper"))

from state_table import ALL_STATES, ASSET_STATES, validate  # noqa: E402

PASS = 0
FAIL = 0
WARNINGS = 0


def ok(msg):
    global PASS
    PASS += 1
    print(f"  [PASS] {msg}")


def fail(msg):
    global FAIL
    FAIL += 1
    print(f"  [FAIL] {msg}")


def warn(msg):
    global WARNINGS
    WARNINGS += 1
    print(f"  [WARN] {msg}")


# ============================================================
# 1. Python 语法检查
# ============================================================
def check_syntax():
    print("\n=== 1. Python 语法检查 ===")
    py_files = []
    for dirpath, _, filenames in os.walk(ROOT):
        if any(skip in dirpath for skip in ["__pycache__", "_pyinstaller", "_backup", "dist", "build", ".git"]):
            continue
        for f in filenames:
            if f.endswith(".py"):
                py_files.append(os.path.join(dirpath, f))

    for f in py_files:
        try:
            with open(f, "r", encoding="utf-8") as fh:
                ast.parse(fh.read(), filename=f)
            ok(f"语法OK: {os.path.relpath(f, ROOT)}")
        except SyntaxError as e:
            fail(f"语法错误: {os.path.relpath(f, ROOT)} - {e}")


# ============================================================
# 2. 状态名一致性（文法单源）
# ============================================================
def check_state_names():
    print("\n=== 2. 状态名一致性（文法单源） ===")
    target_files = [
        os.path.join(ROOT, "helper", "pet_window.py"),
        os.path.join(ROOT, "helper", "packs.py"),
        os.path.join(ROOT, "helper", "main.py"),
        os.path.join(ROOT, "helper", "tray.py"),
        os.path.join(ROOT, "helper", "pack_editor.py"),
        os.path.join(ROOT, "bridge", "server.py"),
        os.path.join(ROOT, "bridge", "ai_monitor.py"),
        os.path.join(ROOT, "bridge", "push.py"),
        os.path.join(ROOT, "bridge", "system_events.py"),
        os.path.join(ROOT, "bridge", "auto_monitor.py"),
    ]
    state_calls = {"apply_state", "set_state", "force_set"}

    # 0) state_table 自检：状态名常量与状态表一致
    st_errors = validate()
    if st_errors:
        for e in st_errors:
            fail(f"state_table 自检: {e}")
    else:
        ok("state_table 状态名常量与状态表一致")

    import ast
    for f in target_files:
        if not os.path.exists(f):
            fail(f"文件不存在: {os.path.relpath(f, ROOT)}")
            continue
        name = os.path.basename(f)
        with open(f, "r", encoding="utf-8") as fh:
            try:
                tree = ast.parse(fh.read(), filename=f)
            except SyntaxError as e:
                fail(f"{name}: AST 解析失败 - {e}")
                continue

        # 1) 必须从 state_table 导入（文法单源）
        imported = any(
            (isinstance(n, ast.ImportFrom) and n.module == "state_table")
            or (isinstance(n, ast.Import) and any(a.name == "state_table" for a in n.names))
            for n in ast.walk(tree)
        )
        if imported:
            ok(f"{name}: 已导入 state_table")
        else:
            fail(f"{name}: 未导入 state_table（违反文法单源，状态名必须从 state_table 导入）")

        # 2) 状态调用（apply_state/set_state/force_set）禁止硬编码字符串字面量
        literals = []
        unknown = []
        for n in ast.walk(tree):
            if not (isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in state_calls):
                continue
            if n.args and isinstance(n.args[0], ast.Constant) and isinstance(n.args[0].value, str):
                literals.append(n.args[0].value)
                if n.args[0].value not in ALL_STATES:
                    unknown.append(n.args[0].value)
        if literals:
            fail(f"{name}: 状态调用硬编码字符串 {sorted(set(literals))}，应改用 state_table 常量")
        else:
            ok(f"{name}: 状态调用均使用 state_table 常量")
        for s in sorted(set(unknown)):
            fail(f"{name}: 状态调用使用了未知状态 '{s}'（不在状态表中）")

        # 3) 状态定义型字典的 key 出现状态名 → 提示。
        # 只扫描“赋值给变量的字典”（如 STATE_LABELS / STATE_COOLDOWN_MS），
        # 排除 BUBBLE_STYLES（key 是气泡样式名）与内联 dict（HTTP 响应 / 消息字段名，如 {"error": ...}）
        assigned_dicts = {
            id(n.value)
            for n in ast.walk(tree)
            if isinstance(n, ast.Assign) and isinstance(n.value, ast.Dict)
        }
        style_nodes = {
            id(n.value)
            for n in ast.walk(tree)
            if isinstance(n, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "BUBBLE_STYLES" for t in n.targets)
        }
        dict_literals = []
        for n in ast.walk(tree):
            if isinstance(n, ast.Dict) and id(n) in assigned_dicts and id(n) not in style_nodes:
                keys = [k for k in n.keys if isinstance(k, ast.Constant) and isinstance(k.value, str)]
                state_keys = [k.value for k in keys if k.value in ALL_STATES]
                # 比例启发式：仅当超过一半 key 是状态名才提示，
                # 避免误报 FOLDER_MAP/Toast.COLORS/pack.json 字段这类恰好同名的 key
                if keys and len(state_keys) > len(keys) / 2:
                    dict_literals.extend(state_keys)
        if dict_literals:
            warn(f"{name}: 状态定义字典 key 出现状态名字符串 {sorted(set(dict_literals))}，建议改用 state_table 常量")

        # 4) return 语句返回状态名字符串字面量 → 违反文法单源
        ret_literals = []
        for n in ast.walk(tree):
            if isinstance(n, ast.Return) and isinstance(n.value, ast.Constant) \
                    and isinstance(n.value.value, str) and n.value.value in ALL_STATES:
                ret_literals.append(n.value.value)
        if ret_literals:
            fail(f"{name}: return 硬编码状态字符串 {sorted(set(ret_literals))}，应改用 state_table 常量")

# ============================================================
# 3. pack.json 素材完整性
# ============================================================
def check_packs():
    print("\n=== 3. 表情包素材完整性 ===")
    packs_dir = os.path.join(ROOT, "packs")
    if not os.path.exists(packs_dir):
        warn("packs/ 目录不存在")
        return

    for pack_name in os.listdir(packs_dir):
        pack_path = os.path.join(packs_dir, pack_name)
        manifest = os.path.join(pack_path, "pack.json")
        if not os.path.exists(manifest):
            warn(f"表情包 {pack_name}: 无 pack.json")
            continue
        try:
            with open(manifest, "r", encoding="utf-8") as f:
                data = json.load(f)
        except json.JSONDecodeError as e:
            fail(f"表情包 {pack_name}: pack.json 解析失败 - {e}")
            continue

        states = data.get("states", {})
        # 核心状态必须存在
        for s in ASSET_STATES:
            if s in states and states[s]:
                # 检查文件是否存在
                for img in states[s]:
                    img_path = os.path.join(pack_path, img)
                    if os.path.exists(img_path):
                        ok(f"{pack_name}/{s}: {img} 存在")
                    else:
                        fail(f"{pack_name}/{s}: {img} 文件不存在!")
            else:
                fail(f"{pack_name}: 缺少核心状态 '{s}'")

        # laugh 配置
        laugh = data.get("laugh", {})
        if laugh:
            gif = laugh.get("gif")
            if gif:
                gif_path = os.path.join(pack_path, gif)
                if os.path.exists(gif_path):
                    ok(f"{pack_name}/laugh.gif: 存在")
                else:
                    fail(f"{pack_name}/laugh.gif: {gif} 文件不存在!")
            else:
                warn(f"{pack_name}: laugh 配置无 gif")
        else:
            warn(f"{pack_name}: 无 laugh 配置（大笑将无动图）")

        ok(f"表情包 {pack_name}: 校验完成")


# ============================================================
# 4. config.json 字段合法性
# ============================================================
def check_config():
    print("\n=== 4. config.json 字段合法性 ===")
    config_path = os.path.join(ROOT, "helper", "config.json")
    if not os.path.exists(config_path):
        warn("helper/config.json 不存在")
        return
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except json.JSONDecodeError as e:
        fail(f"config.json 解析失败: {e}")
        return

    # 检查已知字段
    known_fields = {
        "volume", "muted", "autoStart", "currentPack", "edgeSnap",
        "bubbleScale", "bubbleDuration", "bubbleDisplay",
        "microAction", "reduceMotion", "petX", "petY", "mode",
        "clickThrough", "alwaysOnTop", "showBubble",
        "size", "topmost", "pos", "version",
        "cardVisible", "packId",
    }
    for key in cfg:
        if key in known_fields:
            ok(f"config 字段 '{key}': 已知")
        else:
            warn(f"config 字段 '{key}': 未在已知字段列表中")

    # 检查值范围
    if "bubbleScale" in cfg:
        v = cfg["bubbleScale"]
        if 0.5 <= v <= 2.0:
            ok(f"bubbleScale={v} 在范围内")
        else:
            fail(f"bubbleScale={v} 超出范围 [0.5, 2.0]")

    if "bubbleDuration" in cfg:
        v = cfg["bubbleDuration"]
        if 1 <= v <= 30:
            ok(f"bubbleDuration={v} 在范围内")
        else:
            fail(f"bubbleDuration={v} 超出范围 [1, 30]")

    if "microAction" in cfg:
        v = cfg["microAction"]
        if v in ["high", "medium", "low", "off"]:
            ok(f"microAction={v} 合法")
        else:
            fail(f"microAction={v} 不合法，应为 high/medium/low/off")


# ============================================================
# 5. ai_apps.json 格式校验
# ============================================================
def check_ai_apps():
    print("\n=== 5. ai_apps.json 格式校验 ===")
    path = os.path.join(ROOT, "bridge", "ai_apps.json")
    if not os.path.exists(path):
        warn("bridge/ai_apps.json 不存在")
        return
    try:
        with open(path, "r", encoding="utf-8") as f:
            apps = json.load(f)
    except json.JSONDecodeError as e:
        fail(f"ai_apps.json 解析失败: {e}")
        return

    if isinstance(apps, dict):
        apps = apps.get("apps", [])
    if not isinstance(apps, list):
        fail("ai_apps.json 根节点应为数组或 {apps: [...]} 对象")
        return

    required = {"id", "name"}
    for i, app in enumerate(apps):
        missing = required - set(app.keys())
        if missing:
            fail(f"ai_apps[{i}]: 缺少字段 {missing}")
        else:
            ok(f"ai_apps[{i}] {app.get('id', '?')}: 字段完整")
        # process 类型需要 processes 字段；codex 类型不需要
        if app.get("type") == "process" or "type" not in app:
            if "processes" not in app:
                fail(f"ai_apps[{i}] {app.get('id', '?')}: process 类型缺少 processes 字段")
            else:
                ok(f"  processes={app['processes']}")
        if "cpu_threshold" in app:
            v = app["cpu_threshold"]
            if 1 <= v <= 50:
                ok(f"  cpu_threshold={v} 合法")
            else:
                fail(f"  cpu_threshold={v} 超出范围 [1, 50]")


# ============================================================
# 6. 目标模块导入冒烟
# ============================================================
def check_imports():
    print("\n=== 6. 目标模块导入冒烟 ===")
    helper_dir = os.path.join(ROOT, "helper")
    bridge_dir = os.path.join(ROOT, "bridge")
    modules = [
        "pet_window", "packs", "main", "tray", "pack_editor",
        "server", "ai_monitor", "push", "system_events", "auto_monitor",
    ]
    code = (
        "import sys;"
        "sys.path.insert(0, " + repr(helper_dir) + ");"
        "sys.path.insert(0, " + repr(bridge_dir) + ");"
        "import " + ", ".join(modules)
    )
    try:
        sub = subprocess.run([sys.executable, "-c", code],
                             capture_output=True, text=True, timeout=60)
        if sub.returncode == 0:
            ok("9 个目标模块全部可导入")
        else:
            err = (sub.stderr or sub.stdout).strip().splitlines()
            fail("目标模块导入失败: " + (err[-1] if err else "未知错误"))
    except Exception as e:
        fail(f"导入冒烟执行异常: {e}")


# ============================================================
# 主入口
# ============================================================
def main():
    print(f"奶娃桌宠门禁校验 - 项目根目录: {ROOT}")
    print(f"状态表定义了 {len(ALL_STATES)} 个状态: {', '.join(ALL_STATES)}")

    check_syntax()
    check_state_names()
    check_packs()
    check_config()
    check_ai_apps()
    check_imports()

    print(f"\n{'='*50}")
    print(f"结果: {PASS} 通过, {FAIL} 失败, {WARNINGS} 警告")
    if FAIL > 0:
        print("门禁未通过，请修复 FAIL 项后重新打包。")
        sys.exit(1)
    else:
        print("门禁通过，可以打包。")
        sys.exit(0)


if __name__ == "__main__":
    main()
