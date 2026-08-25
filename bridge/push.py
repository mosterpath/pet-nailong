# -*- coding: utf-8 -*-
"""
奶娃桌宠 - 状态推送命令行工具
通过本地 HTTP API 向桥接服务推送状态，驱动桌宠表情变化。

用法示例：
  python bridge/push.py thinking                  # 推送"思考中"
  python bridge/push.py tool_call --name calculator  # 推送"调工具"+工具名
  python bridge/push.py streaming                 # 推送"回复中"
  python bridge/push.py idle                      # 推送"空闲"（本轮用过工具会触发大笑）
  python bridge/push.py task_done                 # 强制任务完成（大笑）
  python bridge/push.py error                     # 出错抖动
  python bridge/push.py pack nailong              # 切换表情包
  python bridge/push.py reset                     # 重置状态
  python bridge/push.py status                    # 查询当前状态
  python bridge/push.py packs                     # 列出可用表情包
  python bridge/push.py health                    # 健康检查
"""
import argparse
import json
import sys
import urllib.error
import urllib.request

DEFAULT_URL = "http://127.0.0.1:18923"


def _post(url, path, data=None):
    full = url.rstrip("/") + path
    body = json.dumps(data or {}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        full, data=body, method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=3) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as e:
        return {"error": str(e), "hint": "桥接服务未启动？请先运行 start-bridge.bat"}


def _get(url, path):
    full = url.rstrip("/") + path
    try:
        with urllib.request.urlopen(full, timeout=3) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as e:
        return {"error": str(e), "hint": "桥接服务未启动？请先运行 start-bridge.bat"}


def main():
    parser = argparse.ArgumentParser(
        description="奶娃桌宠状态推送工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--url", default=DEFAULT_URL, help="桥接服务地址（默认 http://127.0.0.1:18923）")

    sub = parser.add_subparsers(dest="cmd", metavar="命令")

    # 快捷状态命令
    state_commands = ["idle", "thinking", "tool_call", "streaming", "task_done", "error", "user_msg", "running"]
    for s in state_commands:
        p = sub.add_parser(s, help=f"推送状态: {s}")
        if s == "tool_call":
            p.add_argument("--name", default="", help="工具名称（显示在状态卡上）")

    # 完整状态更新
    p_state = sub.add_parser("state", help="完整 JSON 状态更新")
    p_state.add_argument("--json", required=True, help='JSON 字符串，如 \'{"state":"thinking","toolCount":3}\'')

    # 工具调用
    p_tool = sub.add_parser("tool", help="工具调用开始/结束")
    p_tool.add_argument("action", choices=["start", "end"], help="start=开始调用, end=调用结束")
    p_tool.add_argument("--name", default="", help="工具名称")

    # 切换表情包
    p_pack = sub.add_parser("pack", help="切换表情包")
    p_pack.add_argument("pack_id", help="表情包 id（如 nailong）")

    # 其他
    sub.add_parser("reset", help="重置状态为 idle")
    sub.add_parser("status", help="查询当前状态")
    sub.add_parser("packs", help="列出可用表情包")
    sub.add_parser("health", help="健康检查（含 helper 进程状态）")

    args = parser.parse_args()
    if not args.cmd:
        parser.print_help()
        sys.exit(1)
    # Windows GBK 控制台无法打印 emoji（🐲/🦖），统一 UTF-8 + 容错，避免 push.py packs 崩溃
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    url = args.url

    if args.cmd in state_commands:
        if args.cmd == "tool_call" and args.name:
            # 带工具名 → 走工具调用开始接口（增加计数 + 标记本轮用过工具）
            result = _post(url, "/api/tool/start", {"name": args.name})
        else:
            result = _post(url, f"/api/state/{args.cmd}")
    elif args.cmd == "state":
        try:
            data = json.loads(args.json)
        except json.JSONDecodeError as e:
            print(f"JSON 解析失败: {e}")
            sys.exit(1)
        result = _post(url, "/api/state", data)
    elif args.cmd == "tool":
        result = _post(url, f"/api/tool/{args.action}", {"name": args.name})
    elif args.cmd == "pack":
        result = _post(url, "/api/pack", {"packId": args.pack_id})
    elif args.cmd == "reset":
        result = _post(url, "/api/reset")
    elif args.cmd == "status":
        result = _get(url, "/api/state")
    elif args.cmd == "packs":
        result = _get(url, "/api/packs")
    elif args.cmd == "health":
        result = _get(url, "/api/health")
    else:
        parser.print_help()
        sys.exit(1)

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
