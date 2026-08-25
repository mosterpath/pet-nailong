# -*- coding: utf-8 -*-
"""
奶娃桌宠 - 表情包加载器
与插件版 packs/<id>/pack.json 完全一致的 schema：
- states:     {状态: [相对路径...]} 多张随机，路径可以是 .png/.jpg 静态图或 .gif 动图
- bubbles:    {状态: 文案}
- clickBubbles: [随机梗]
- laugh:      {gif, mp3, duration_ms} 大笑专用动图+音频（优先级高于 states.task_done）
- thinkingLines: [思维链心声]（可选）
"""
import json
import os
import random

from state_table import STATE_IDLE


class Pack:
    """一个表情包。image_path() 返回相对 packs 根目录的路径。"""

    def __init__(self, pack_id, dir_name, manifest, base_dir=""):
        self.id = pack_id
        self.dir = dir_name
        self.base_dir = base_dir
        self.name = manifest.get("name", pack_id)
        self.emoji = manifest.get("emoji", "🐾")
        self.version = manifest.get("version", "")
        self.laugh = manifest.get("laugh") or {}
        self.states = manifest.get("states") or {}
        self.bubbles = manifest.get("bubbles") or {}
        self.click_bubbles = manifest.get("clickBubbles") or []
        self.thinking_lines = manifest.get("thinkingLines") or []
        self._fallback_idle = None
        idle = self.states.get(STATE_IDLE) or []
        if idle:
            self._fallback_idle = idle[0]

    def image_for(self, state, index=None):
        """返回该状态的图片相对路径（多张随机）。缺省回退 idle。"""
        lst = self.states.get(state) or []
        if not lst:
            return self._fallback_idle
        if index is None:
            index = random.randrange(len(lst))
        return lst[index % len(lst)]

    def laugh_gif(self):
        return self.laugh.get("gif")

    def bubble_for(self, state):
        return self.bubbles.get(state, "")

    def random_click(self):
        if not self.click_bubbles:
            return "嘿嘿"
        return random.choice(self.click_bubbles)

    def random_thought(self):
        if not self.thinking_lines:
            return None
        return random.choice(self.thinking_lines)


class PackLoader:
    """扫描 packs 目录（可多个，按优先级合并，前面的优先、同名覆盖），加载所有 pack.json。"""

    def __init__(self, packs_dir):
        self.packs_dir = packs_dir
        self.packs_dirs = [packs_dir] if isinstance(packs_dir, str) else list(packs_dir or [])
        self.packs = {}
        self.scan()

    def scan(self):
        self.packs = {}
        for base in self.packs_dirs:
            if not os.path.isdir(base):
                continue
            for entry in sorted(os.listdir(base)):
                manifest_path = os.path.join(base, entry, "pack.json")
                if not os.path.isfile(manifest_path):
                    continue
                try:
                    with open(manifest_path, "r", encoding="utf-8") as f:
                        manifest = json.load(f)
                    if not manifest.get("id"):
                        continue
                    pid = manifest["id"]
                    if pid in self.packs:
                        continue  # 同名包已被更高优先级目录加载
                    self.packs[pid] = Pack(pid, entry, manifest, base_dir=base)
                except Exception as e:  # noqa: BLE001
                    print("[pet-helper] pack 加载失败 %s: %s" % (entry, e), flush=True)

    def list(self):
        return [{"id": p.id, "name": p.name, "emoji": p.emoji, "version": p.version}
                for p in self.packs.values()]

    def get(self, pack_id, default=None):
        return self.packs.get(pack_id, default)

    def resolve(self, pack, rel_path):
        """把包内相对路径解析为磁盘绝对路径。防路径穿越：normpath 后校验落在包目录内。"""
        if not rel_path:
            return None
        base = getattr(pack, "base_dir", None) or (self.packs_dirs[0] if self.packs_dirs else "")
        pack_root = os.path.normpath(os.path.join(base, pack.dir))
        candidate = os.path.normpath(os.path.join(pack_root, rel_path.lstrip("/\\")))
        if candidate != pack_root and not candidate.startswith(pack_root + os.sep):
            return None  # 越出包目录，拒绝
        return candidate
