# -*- mode: python ; coding: utf-8 -*-
# 奶娃桌宠 - 一体化 exe 打包配置
# 用法: python -m PyInstaller helper/pet_all.spec
# 输出: dist/奶娃桌宠/奶娃桌宠.exe （onefile 单文件，无终端弹窗）
import os

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))

a = Analysis(
    [os.path.join(ROOT, "helper", "all_in_one.py")],
    pathex=[ROOT, os.path.join(ROOT, "helper"), os.path.join(ROOT, "bridge")],
    binaries=[],
    datas=[
        (os.path.join(ROOT, "packs"), "packs"),
        (os.path.join(ROOT, "icons"), "icons"),
        # bridge 模块：server.py / ai_monitor.py / auto_monitor.py / ai_apps.json / push.py
        (os.path.join(ROOT, "bridge", "server.py"), "bridge"),
        (os.path.join(ROOT, "bridge", "ai_monitor.py"), "bridge"),
        (os.path.join(ROOT, "bridge", "auto_monitor.py"), "bridge"),
        (os.path.join(ROOT, "bridge", "ai_apps.json"), "bridge"),
    ],
    hiddenimports=["psutil"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="pet-nailong-all",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,          # 无终端弹窗
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=os.path.join(ROOT, "icons", "pet-icon.ico"),
)
