# -*- coding: utf-8 -*-
"""
奶娃桌宠 - JSON-lines 协议（stdio，UTF-8）
host(JS) <-> helper(Python) 通信。
参考 dsh-dafeiyu 的协议思路，简化实现。
"""
import json
import sys

PROTOCOL_VERSION = 1


def encode(msg):
    """编码消息为 UTF-8 字节行。"""
    return (json.dumps(msg, ensure_ascii=False) + "\n").encode("utf-8")


def write_message(msg):
    """向 host 发送一条消息（写 stdout 并立即 flush）。"""
    sys.stdout.buffer.write(encode(msg))
    sys.stdout.buffer.flush()


def read_message(stream):
    """从流中读一行并解析为 dict；EOF 返回 None；解析失败返回 None。"""
    line = stream.readline()
    if not line:
        return None
    line = line.strip()
    if not line:
        return read_message(stream)
    try:
        msg = json.loads(line)
        return msg if isinstance(msg, dict) else None
    except Exception:  # noqa: BLE001
        return None


def make(kind, **payload):
    msg = {"protocolVersion": PROTOCOL_VERSION, "kind": kind}
    msg.update(payload)
    return msg
