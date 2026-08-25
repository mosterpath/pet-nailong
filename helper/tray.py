# -*- coding: utf-8 -*-
"""
奶娃桌宠 - 托盘图标与菜单
"""
import os

from state_table import STATE_IDLE

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QIcon, QPixmap
from PyQt5.QtWidgets import QMenu, QSystemTrayIcon


class TrayIcon:
    def __init__(self, window):
        self.window = window
        self.tray = None
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        self.tray = QSystemTrayIcon(self._make_icon(), window)
        self.tray.setToolTip("奶娃桌宠")
        self._menu = QMenu()
        self._menu.aboutToShow.connect(self._build_menu)
        self.tray.setContextMenu(self._menu)
        self._build_menu()
        self.tray.activated.connect(self._on_activated)
        self.tray.show()
        # 切换表情包后刷新托盘图标
        try:
            window.event_out.connect(self._on_window_event)
        except Exception:  # noqa: BLE001
            pass

    def _make_icon(self):
        # 用当前包的空闲素材当托盘图标
        try:
            pack = self.window.pack
            rel = pack.image_for(STATE_IDLE) if pack else None
            if rel:
                abs_path = self.window.loader.resolve(pack, rel)
                if abs_path and os.path.isfile(abs_path):
                    pm = QPixmap(abs_path)
                    if not pm.isNull():
                        return QIcon(pm.scaled(64, 64, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        except Exception:  # noqa: BLE001
            pass
        return QIcon()

    def _build_menu(self):
        """清空并重建托盘菜单（每次显示前调用，保证文字/勾选实时）。"""
        self._menu.clear()
        self._menu.addAction("显示/隐藏", self.window.toggle_visible)
        mode_menu = self._menu.addMenu("移动模式")
        for key, label in (("walk", "自由散步"), ("follow", "跟随鼠标"), ("stay", "原地待着")):
            act = mode_menu.addAction(label)
            act.setCheckable(True)
            act.setChecked(self.window.mode == key)
            act.triggered.connect(lambda _=False, k=key: self.window.set_mode(k))
        self._menu.addSeparator()
        self._menu.addAction("鼠标穿透" if not self.window.click_through else "取消穿透", self.window.toggle_click_through)
        self._menu.addAction("取消置顶" if self.window.topmost else "置顶", self.window.toggle_topmost)
        self._menu.addAction("隐藏状态卡" if self.window._card_visible else "显示状态卡", self.window.toggle_card)
        self._menu.addSeparator()
        autostart_act = self._menu.addAction("开机启动", self.window.toggle_autostart)
        autostart_act.setCheckable(True)
        autostart_act.setChecked(self.window._autostart_enabled())
        self._menu.addAction("让奶龙笑一个", self.window.trigger_laugh)
        mute_act = self._menu.addAction("取消静音" if self.window._muted else "大笑静音", self.window.toggle_mute)
        mute_act.setCheckable(True)
        mute_act.setChecked(self.window._muted)
        self._menu.addAction("设置…", self.window._open_settings)
        self._menu.addAction("关于", self.window._show_about)
        self._menu.addSeparator()
        self._menu.addAction("退出", self.window.quit_pet)

    def _on_activated(self, reason):
        if reason == QSystemTrayIcon.Trigger:  # 左键单击
            self.window.toggle_visible()
        elif reason == QSystemTrayIcon.DoubleClick:  # 双击
            self.window.trigger_laugh()

    def _on_window_event(self, msg):
        """窗口事件：表情包切换后刷新托盘图标（右键菜单发 pack，桥接/协议发 pack_changed）。"""
        if isinstance(msg, dict) and msg.get("kind") == "event" and msg.get("name") in ("pack", "pack_changed"):
            self.refresh_icon()

    def refresh_icon(self):
        """用当前包的空闲素材重建托盘图标 + 悬浮提示。"""
        if self.tray is None:
            return
        try:
            self.tray.setIcon(self._make_icon())
            pack = self.window.pack
            self.tray.setToolTip("奶娃桌宠 - %s" % (pack.name if pack else "无表情包"))
        except Exception:  # noqa: BLE001
            pass

    def hide(self):
        if self.tray:
            self.tray.hide()
