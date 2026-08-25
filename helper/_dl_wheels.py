# -*- coding: utf-8 -*-
"""补下缺失依赖：递归解析 requires_dist 下载 wheel"""
import json
import os
import re
import sys
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")

OUT = os.path.join(os.environ["TEMP"], "pyinstaller-wheels")
os.makedirs(OUT, exist_ok=True)

done = set()


def pick_wheel(name, version=None):
    data = json.loads(urllib.request.urlopen("https://pypi.org/pypi/%s/json" % name, timeout=20).read())
    info = data["info"]
    ver = version or info["version"]
    best = None
    for u in data["urls"]:
        fn = u["filename"]
        if not fn.endswith(".whl"):
            continue
        if ver not in fn:
            continue
        if ("cp38" in fn or "py3-none" in fn or "py2.py3-none" in fn) and ("win_amd64" in fn or "any" in fn):
            best = u
            break
    if best is None:
        raise RuntimeError("no wheel for %s %s" % (name, ver))
    return best, info.get("requires_dist") or []


def parse_req(req):
    # 取包名（处理 extras / markers / 版本约束）
    m = re.match(r"^([A-Za-z0-9_.-]+)", req)
    return m.group(1).replace("_", "-").lower()


def resolve(name, version=None):
    key = (name, version)
    if key in done:
        return
    done.add(key)
    u, reqs = pick_wheel(name, version)
    fn = u["filename"]
    dst = os.path.join(OUT, fn)
    if not os.path.exists(dst):
        print("downloading", fn)
        urllib.request.urlretrieve(u["url"], dst)
    else:
        print("have", fn)
    for r in reqs or []:
        # 跳过环境标记不满足的（粗略处理：只取无 python_version 限制或兼容 3.8 的）
        if ";" in r:
            r, marker = r.split(";", 1)
            mver = re.search(r'python_version[<>=!]+ ?"?(\d+)\.(\d+)', marker)
            if mver:
                major, minor = int(mver.group(1)), int(mver.group(2))
                op = re.search(r'python_version\s*([<>=!]+)', marker)
                ok = False
                cur = (3, 8)
                if op:
                    ops = op.group(1)
                    if ops == "<":
                        ok = cur < (major, minor)
                    elif ops == "<=":
                        ok = cur <= (major, minor)
                    elif ops == ">":
                        ok = cur > (major, minor)
                    elif ops == ">=":
                        ok = cur >= (major, minor)
                    elif ops == "==":
                        ok = cur == (major, minor)
                if not ok:
                    continue
        dep = parse_req(r)
        try:
            resolve(dep)
        except Exception as e:
            print("skip dep", dep, e)


resolve("pyinstaller", "6.22.2")
print("RESOLVED", len(done), "packages")
