# -*- coding: utf-8 -*-
"""
奶娃桌宠 - 透明置顶桌面窗口
- 无边框透明置顶窗口，精灵居中渲染（packs/ 素材）
- 动画：呼吸/摇摆/蹦跳/走路（窗口移动+镜像）/大笑 GIF/出错抖动
- 交互：左键拖拽、单击蹦跳+梗气泡、右键菜单、托盘
- 三种模式：自由散步 / 跟随鼠标 / 原地待着
- 气泡（状态/梗/思维链心声）+ 状态卡
- 配置记忆：位置/大小/模式/穿透/置顶（helper/config.json）
"""
import ctypes
import json
import os
import random
import sys

from PyQt5.QtCore import QPoint, QPropertyAnimation, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QColor, QFont, QFontMetrics, QIcon, QMovie, QPainter, QPainterPath, QPen, QPixmap
from PyQt5.QtWidgets import (QApplication, QGraphicsOpacityEffect,
                             QGraphicsScene, QGraphicsView, QLabel, QMessageBox,
                             QPushButton, QVBoxLayout, QWidget, QSystemTrayIcon, QMenu, QAction)

from animations import (BounceAnimator, PetGroup, PetSprite,
                        ShakeAnimator, SwayAnimator, WalkController)

from state_table import (STATE_ERROR, STATE_IDLE, STATE_STREAMING, STATE_TASK_DONE,
                         STATE_THINKING, STATE_TOOL_CALL, STATE_USER_MSG)

SPRITE_W = 175          # 精灵显示宽度（默认档位）
SIZE_PRESETS = {"small": 150, "medium": 190, "large": 260}
# 窗口高 = 精灵宽 + 上下留白（顶部给气泡、底部给状态卡）
WINDOW_HEIGHT_PAD = 120

# 全局快捷键：Ctrl+Alt+L 手动触发大笑
WM_HOTKEY = 0x0312
MOD_CONTROL = 0x0002
MOD_ALT = 0x0001
MOD_NOREPEAT = 0x4000
VK_L = 0x4C
HOTKEY_ID = 9527
VK_M = 0x4D
HOTKEY_ID_MUTE = 9528

STATE_LABELS = {
    STATE_IDLE: "空闲", STATE_THINKING: "思考中", STATE_TOOL_CALL: "调工具",
    STATE_STREAMING: "回复中", STATE_TASK_DONE: "任务完成", STATE_ERROR: "出错",
    STATE_USER_MSG: "来消息",
}

FALLBACK_CLICK = ["嘿嘿", "看我干嘛"]
FALLBACK_THOUGHTS = [
    "（这题有点难…）", "（假装在思考）", "（人类又在催我了）",
    "（摸鱼被发现了）", "（先想个借口）",
]

APP_VERSION = "2.0.1"          # 与插件版 host 版本保持一致
AUTOSTART_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
AUTOSTART_VALUE = "pet-nailong"
LAUGH_VOLUME = 60              # 大笑音量（0-100），避免吓一跳


class PetMenu(QWidget):
    """自定义右键菜单。
    不用 QMenu：本进程常无前台激活权，QMenu 的 popup/exec 会抓鼠标或
    弹不出，导致桌宠失去鼠标响应。这里用普通置顶小窗口 + 按钮实现：
    普通窗口不需要激活权即可显示和接收点击，且不抓取鼠标，
    桌宠本体始终能收到事件。"""

    ITEM_QSS = (
        "QPushButton { background: transparent; border: none; color: #333;"
        " text-align: left; padding: 6px 14px; font-size: 13px; border-radius: 6px; }"
        "QPushButton:hover { background: #eef1ff; }"
        "QPushButton:checked { background: #667eea; color: #fff; }"
    )

    def __init__(self, window):
        super().__init__(window, Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint)
        self._window = window
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setStyleSheet(
            "QWidget { background: transparent; }"
            "QFrame#menuCard { background: rgba(255,255,255,245); border-radius: 10px;"
            " border: 1px solid rgba(0,0,0,0.08); }"
        )
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        from PyQt5.QtWidgets import QFrame
        card = QFrame(self)
        card.setObjectName("menuCard")
        outer.addWidget(card)
        self._layout = QVBoxLayout(card)
        self._layout.setContentsMargins(6, 8, 6, 8)
        self._layout.setSpacing(2)
        self._groups = {}  # group_id -> { key: button }
        self._group_buttons = {}  # group_id -> button list

    # ----------------------------------------------------------
    def _add_item(self, text, on_click, group=None, group_key=None, checked=False, checkable=False):
        btn = QPushButton(text, self)
        btn.setStyleSheet(self.ITEM_QSS)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setCheckable(group is not None or checkable)
        btn.setChecked(checked)
        btn.setMinimumWidth(150)
        btn.clicked.connect(lambda _=False, cb=on_click, g=group, k=group_key: self._activate(g, k, cb))
        self._layout.addWidget(btn)
        if group is not None:
            self._groups.setdefault(group, {})[group_key] = btn
            self._group_buttons.setdefault(group, []).append(btn)
        return btn

    def _add_separator(self):
        from PyQt5.QtWidgets import QFrame
        line = QFrame(self)
        line.setFrameShape(QFrame.HLine)
        line.setStyleSheet("color: #e5e5e5;")
        self._layout.addWidget(line)

    def _activate(self, group, key, on_click):
        # 单选组：先刷新勾选
        if group is not None and key is not None:
            for k, btn in self._groups.get(group, {}).items():
                btn.setChecked(k == key)
        if on_click:
            on_click()
        self.close()

    # ----------------------------------------------------------
    def show_menu(self, global_pos, items):
        """items: list of (kind, ...)
           kind='item': (text, on_click)
           kind='check': (text, checked, on_click)
           kind='radio': (text, group, key, checked, on_click)
           kind='sep'
        """
        while self._layout.count():
            item = self._layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._groups.clear()
        self._group_buttons.clear()
        for it in items:
            if it[0] == "sep":
                self._add_separator()
            elif it[0] == "item":
                self._add_item(it[1], it[2])
            elif it[0] == "check":
                self._add_item(it[1], it[3], checked=it[2], checkable=True)
            elif it[0] == "radio":
                self._add_item(it[1], it[4], group=it[2], group_key=it[3], checked=it[5] if len(it) > 5 else False)
        self.adjustSize()
        # 贴屏幕：防止超出边缘
        screen = self.screen().availableGeometry() if self.screen() else None
        x, y = global_pos.x(), global_pos.y()
        if screen is not None:
            x = min(x, screen.right() - self.width() - 8)
            y = min(y, screen.bottom() - self.height() - 8)
            x = max(x, screen.left() + 8)
            y = max(y, screen.top() + 8)
        self.move(x, y)
        self.show()
        self.raise_()

    def closeEvent(self, event):
        super().closeEvent(event)


class SettingsDialog(QWidget):
    """奶娃桌宠设置面板（独立置顶窗口，非模态，不抓鼠标）。"""

    SETTINGS_QSS = """
        QWidget { background: #fafafa; font-family: "Microsoft YaHei"; font-size: 13px; color: #333; }
        QFrame#section { background: #fff; border: 1px solid #eee; border-radius: 10px; }
        QLabel#title { font-size: 15px; font-weight: 600; color: #222; }
        QLabel#hint { font-size: 11px; color: #999; }
        QCheckBox { spacing: 8px; padding: 4px 0; }
        QCheckBox::indicator { width: 16px; height: 16px; border-radius: 4px; border: 1px solid #ccc; }
        QCheckBox::indicator:checked { background: #667eea; border-color: #667eea; }
        QRadioButton { spacing: 8px; padding: 3px 0; }
        QRadioButton::indicator { width: 14px; height: 14px; border-radius: 7px; border: 1px solid #ccc; }
        QRadioButton::indicator:checked { background: #667eea; border-color: #667eea; }
        QPushButton#primary { background: #667eea; color: #fff; border: none; border-radius: 8px;
                              padding: 8px 20px; font-weight: 600; }
        QPushButton#primary:hover { background: #5568d3; }
        QPushButton#ghost { background: transparent; color: #666; border: 1px solid #ddd;
                            border-radius: 8px; padding: 8px 16px; }
        QPushButton#ghost:hover { background: #f0f0f0; }
    """

    def __init__(self, pet):
        super().__init__(None, Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint)
        self.pet = pet
        self.setAttribute(Qt.WA_TranslucentBackground, False)
        self.setStyleSheet(self.SETTINGS_QSS)
        self.setWindowTitle("奶娃桌宠设置")
        self.setMinimumWidth(380)
        self._build_ui()
        self._load_values()

    def _build_ui(self):
        from PyQt5.QtWidgets import QScrollArea, QButtonGroup, QHBoxLayout
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 16, 16, 16)
        outer.setSpacing(12)

        # 标题
        header = QHBoxLayout()
        title = QLabel("⚙ 奶娃桌宠设置")
        title.setStyleSheet("font-size:18px;font-weight:700;color:#222;")
        header.addWidget(title)
        header.addStretch()
        close_btn = QPushButton("✕")
        close_btn.setFixedSize(28, 28)
        close_btn.setStyleSheet("QPushButton{border:none;border-radius:14px;background:#eee;color:#666;font-size:14px;}QPushButton:hover{background:#ddd;}")
        close_btn.clicked.connect(self.close)
        header.addWidget(close_btn)
        outer.addLayout(header)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea{border:none;background:transparent;}")
        content = QWidget()
        cl = QVBoxLayout(content)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(12)

        # === 通用 ===
        cl.addWidget(self._section("通用", [
            ("check", "开机自动启动", "autostart"),
            ("check", "大笑静音（只笑不叫）", "muted"),
            ("check", "显示状态卡", "card"),
            ("check", "鼠标穿透（点不到桌宠）", "through"),
            ("check", "窗口置顶", "topmost"),
            ("check", "拖拽边缘吸附", "edge_snap"),
        ]))

        # === 外观 ===
        self.size_group = QButtonGroup(self)
        self.mode_group = QButtonGroup(self)
        cl.addWidget(self._section("外观与行为", [
            ("radio", "大小", "size", [("小", "small"), ("中", "medium"), ("大", "large")], self.size_group),
            ("radio", "移动模式", "mode", [("原地待着", "stay"), ("自由散步", "walk"), ("跟随鼠标", "follow")], self.mode_group),
        ]))

        # === 动态效果 ===
        self.micro_group = QButtonGroup(self)
        cl.addWidget(self._section("动态效果", [
            ("radio", "空闲活跃程度", "micro", [("高", "high"), ("中", "medium"), ("低", "low"), ("关闭", "off")], self.micro_group),
            ("check", "减少动态效果（省资源）", "reduce_motion"),
        ]))

        # === 气泡 ===
        self.bubble_scale_group = QButtonGroup(self)
        self.bubble_dur_group = QButtonGroup(self)
        self.bubble_disp_group = QButtonGroup(self)
        cl.addWidget(self._section("气泡", [
            ("radio", "气泡大小", "bscale", [("小", "0.85"), ("中", "1.0"), ("大", "1.15")], self.bubble_scale_group),
            ("radio", "显示时长", "bdur", [("短 3秒", "3"), ("中 6秒", "6"), ("长 10秒", "10")], self.bubble_dur_group),
            ("radio", "显示策略", "bdisp", [("全部显示", "all"), ("仅状态变化", "state_only"), ("关闭", "off")], self.bubble_disp_group),
        ]))

        # === 快捷键 ===
        cl.addWidget(self._section("快捷键", [
            ("label", "Ctrl + Alt + L", "手动大笑"),
            ("label", "Ctrl + Alt + M", "切换静音"),
            ("label", "左键托盘图标", "显示/隐藏桌宠"),
            ("label", "右键桌宠", "打开菜单"),
        ]))

        # === 关于 ===
        pack_name = self.pet.pack.name if self.pet.pack else "—"
        cl.addWidget(self._section("关于", [
            ("label", "版本", "v%s" % APP_VERSION),
            ("label", "当前表情包", pack_name),
            ("label", "AI 监控", "24 个工具 + 系统事件"),
        ]))

        cl.addStretch()
        scroll.setWidget(content)
        outer.addWidget(scroll, stretch=1)

        # 底部按钮
        btns = QHBoxLayout()
        btns.addStretch()
        reset_btn = QPushButton("恢复默认")
        reset_btn.setObjectName("ghost")
        reset_btn.clicked.connect(self._reset_defaults)
        btns.addWidget(reset_btn)
        ok_btn = QPushButton("完成")
        ok_btn.setObjectName("primary")
        ok_btn.clicked.connect(self.close)
        btns.addWidget(ok_btn)
        outer.addLayout(btns)

        self._widgets = {}

    def _section(self, title, items):
        from PyQt5.QtWidgets import QFrame, QHBoxLayout, QCheckBox, QRadioButton
        card = QFrame()
        card.setObjectName("section")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(16, 12, 16, 12)
        lay.setSpacing(6)
        t = QLabel(title)
        t.setObjectName("title")
        lay.addWidget(t)
        for item in items:
            if item[0] == "check":
                cb = QCheckBox(item[1])
                cb.stateChanged.connect(lambda _=None, key=item[2]: self._on_check(key, cb))
                lay.addWidget(cb)
                self._widgets[item[2]] = cb
            elif item[0] == "radio":
                row = QHBoxLayout()
                row.addWidget(QLabel(item[1] + "："))
                group = item[4]
                for text, key in item[3]:
                    rb = QRadioButton(text)
                    group.addButton(rb)
                    rb.clicked.connect(lambda _=None, k=key, field=item[2]: self._on_radio(field, k))
                    row.addWidget(rb)
                    self._widgets[item[2] + "_" + key] = rb
                row.addStretch()
                lay.addLayout(row)
            elif item[0] == "label":
                row = QHBoxLayout()
                k = QLabel(item[1])
                k.setStyleSheet("color:#555;font-weight:500;min-width:140px;")
                v = QLabel(item[2])
                v.setStyleSheet("color:#888;")
                row.addWidget(k)
                row.addWidget(v, stretch=1)
                lay.addLayout(row)
        return card

    def _load_values(self):
        w = self._widgets
        if "autostart" in w:
            w["autostart"].setChecked(self.pet._autostart_enabled())
        if "muted" in w:
            w["muted"].setChecked(self.pet._muted)
        if "card" in w:
            w["card"].setChecked(self.pet._card_visible)
        if "through" in w:
            w["through"].setChecked(self.pet.click_through)
        if "topmost" in w:
            w["topmost"].setChecked(self.pet.topmost)
        if "edge_snap" in w:
            w["edge_snap"].setChecked(self.pet._edge_snap)
        if "reduce_motion" in w:
            w["reduce_motion"].setChecked(self.pet._reduce_motion)
        for key, rb_key in [("small", "size_small"), ("medium", "size_medium"), ("large", "size_large")]:
            if rb_key in w:
                w[rb_key].setChecked(self.pet.size_key == key)
        for key, rb_key in [("stay", "mode_stay"), ("walk", "mode_walk"), ("follow", "mode_follow")]:
            if rb_key in w:
                w[rb_key].setChecked(self.pet.mode == key)
        for key, rb_key in [("high", "micro_high"), ("medium", "micro_medium"), ("low", "micro_low"), ("off", "micro_off")]:
            if rb_key in w:
                w[rb_key].setChecked(self.pet._micro_action_level == key)
        for key, rb_key in [("0.85", "bscale_0.85"), ("1.0", "bscale_1.0"), ("1.15", "bscale_1.15")]:
            if rb_key in w:
                w[rb_key].setChecked(abs(self.pet._bubble_scale - float(key)) < 0.01)
        for key, rb_key in [("3", "bdur_3"), ("6", "bdur_6"), ("10", "bdur_10")]:
            if rb_key in w:
                w[rb_key].setChecked(self.pet._bubble_duration == int(key))
        for key, rb_key in [("all", "bdisp_all"), ("state_only", "bdisp_state_only"), ("off", "bdisp_off")]:
            if rb_key in w:
                w[rb_key].setChecked(self.pet._bubble_display == key)

    def _on_check(self, key, cb):
        if key == "autostart":
            self.pet.toggle_autostart()
            cb.setChecked(self.pet._autostart_enabled())
        elif key == "muted":
            if cb.isChecked() != self.pet._muted:
                self.pet.toggle_mute()
        elif key == "card":
            if cb.isChecked() != self.pet._card_visible:
                self.pet.toggle_card()
        elif key == "through":
            if cb.isChecked() != self.pet.click_through:
                self.pet.toggle_click_through()
        elif key == "topmost":
            if cb.isChecked() != self.pet.topmost:
                self.pet.toggle_topmost()
        elif key == "edge_snap":
            if cb.isChecked() != self.pet._edge_snap:
                self.pet.toggle_edge_snap()
        elif key == "reduce_motion":
            if cb.isChecked() != self.pet._reduce_motion:
                self.pet.toggle_reduce_motion()

    def _on_radio(self, field, key):
        if field == "size":
            self.pet.set_size(key)
        elif field == "mode":
            self.pet.set_mode(key)
        elif field == "micro":
            self.pet.set_micro_action_level(key)
        elif field == "bscale":
            self.pet.set_bubble_scale(float(key))
        elif field == "bdur":
            self.pet.set_bubble_duration(int(key))
        elif field == "bdisp":
            self.pet.set_bubble_display(key)

    def _reset_defaults(self):
        self.pet.set_size("medium")
        self.pet.set_mode("stay")
        self.pet.set_micro_action_level("medium")
        self.pet.set_bubble_scale(1.0)
        self.pet.set_bubble_duration(6)
        self.pet.set_bubble_display("all")
        if self.pet._reduce_motion:
            self.pet.toggle_reduce_motion()
        if self.pet._muted:
            self.pet.toggle_mute()
        if not self.pet._card_visible:
            self.pet.toggle_card()
        if self.pet.click_through:
            self.pet.toggle_click_through()
        if not self.pet.topmost:
            self.pet.toggle_topmost()
        if not self.pet._edge_snap:
            self.pet.toggle_edge_snap()
        self._load_values()
        self.pet.show_bubble("已恢复默认设置", interaction=True)

    def show_at_cursor(self):
        from PyQt5.QtGui import QCursor
        pos = QCursor.pos()
        self.move(pos.x() + 10, pos.y() + 10)
        self.show()
        self.raise_()
        self.activateWindow()


# 不同状态的气泡配色（背景, 文字, 边框）
BUBBLE_STYLES = {
    "normal":      ((255, 255, 255, 240), (42, 42, 42), (0, 0, 0, 15)),
    "thought":     ((60, 60, 75, 220), (232, 232, 232), (0, 0, 0, 0)),
    "error":       ((255, 235, 235, 245), (180, 40, 40), (255, 100, 100, 60)),
    "task_done":   ((255, 248, 220, 245), (180, 120, 0), (255, 200, 50, 80)),
    "thinking":    ((230, 240, 255, 240), (40, 80, 160), (100, 150, 255, 50)),
    "system":      ((235, 250, 235, 240), (40, 120, 60), (100, 200, 100, 50)),
}


class BubbleWidget(QWidget):
    """带尾巴的对话气泡：圆角矩形 + 底部尖角指向宠物，支持打字机效果。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._text = ""
        self._displayed = ""
        self._style_key = "normal"
        self._tail_width = 14
        self._tail_height = 10
        self._padding_x = 14
        self._padding_y = 8
        self._radius = 14
        self._base_font_size = 10
        self._font = QFont("Microsoft YaHei", self._base_font_size)
        self._scale = 1.0
        self._max_width = 240  # 由外部设置为宠物窗口宽度-8
        self._typewriter_timer = QTimer(self)
        self._typewriter_timer.timeout.connect(self._type_next)
        self._typewriter_full = ""
        self._final_size = None  # 打字机开始时锁定最终尺寸，避免跳动
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.hide()

    def set_scale(self, scale):
        """缩放气泡：同时缩放字体和内边距，文字同步放大/缩小。"""
        self._scale = max(0.8, min(1.2, float(scale)))
        new_size = max(8, int(self._base_font_size * self._scale))
        self._font = QFont("Microsoft YaHei", new_size)
        self._padding_x = int(14 * self._scale)
        self._padding_y = int(8 * self._scale)
        self._tail_width = int(14 * self._scale)
        self._tail_height = int(10 * self._scale)
        if self._text:
            self._final_size = None
            self._update_geometry()
            self.update()

    def set_text(self, text, style_key="normal", typewriter=True):
        self._style_key = style_key if style_key in BUBBLE_STYLES else "normal"
        self._typewriter_timer.stop()
        self._text = text
        if typewriter and len(text) > 3:
            self._typewriter_full = text
            self._displayed = ""
            # 先用完整文本计算最终尺寸并锁定，打字过程中尺寸不变
            self._displayed = text
            self._update_geometry()
            self._final_size = (self.width(), self.height())
            self._displayed = ""
            self._typewriter_timer.start(25)  # 每25ms一个字
        else:
            self._displayed = text
            self._final_size = None
            self._update_geometry()
        self.update()

    def _type_next(self):
        if len(self._displayed) < len(self._typewriter_full):
            self._displayed = self._typewriter_full[:len(self._displayed) + 1]
            self.update()  # 只重绘，不改尺寸
        else:
            self._typewriter_timer.stop()

    def stop_typewriter(self):
        """外部调用：停止打字机（气泡隐藏时）。"""
        self._typewriter_timer.stop()

    def _update_geometry(self):
        fm = QFontMetrics(self._font)
        text = self._displayed or " "
        max_w = max(60, int(self._max_width * self._scale))
        # 用 boundingRect + TextWordWrap 计算实际尺寸，与 paintEvent 绘制方式完全一致
        # 避免手动换行和 Qt 换行算法不一致导致文字被截断
        text_rect = fm.boundingRect(0, 0, max_w, 10000,
                                    Qt.TextWordWrap | Qt.AlignTop | Qt.AlignHCenter, text)
        text_w = text_rect.width()
        text_h = text_rect.height()
        w = text_w + self._padding_x * 2
        h = text_h + self._padding_y * 2 + self._tail_height + 2  # +2px 缓冲防圆角裁剪
        # 打字机模式下锁定最终尺寸
        if self._final_size:
            w, h = self._final_size
        self.setFixedSize(max(w, 40), max(h, 30))

    def paintEvent(self, event):
        if not self._displayed:
            return
        bg, fg, border = BUBBLE_STYLES[self._style_key]
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        body_h = h - self._tail_height
        path = QPainterPath()
        path.addRoundedRect(0, 0, w, body_h, self._radius, self._radius)
        cx = w / 2
        tail = QPainterPath()
        tail.moveTo(cx - self._tail_width / 2, body_h - 1)
        tail.lineTo(cx, h - 1)
        tail.lineTo(cx + self._tail_width / 2, body_h - 1)
        tail.closeSubpath()
        path.addPath(tail)
        p.fillPath(path, QColor(*bg))
        if border[3] > 0:
            p.setPen(QPen(QColor(*border), 1))
            p.drawPath(path)
        p.setPen(QColor(*fg))
        p.setFont(self._font)
        text_rect = self.rect().adjusted(self._padding_x, self._padding_y,
                                         -self._padding_x, -self._padding_y - self._tail_height)
        p.drawText(text_rect, Qt.AlignCenter | Qt.TextWordWrap, self._displayed)


class PetWindow(QWidget):
    """透明置顶桌宠窗口。"""

    # 发往 host 的事件（demo 模式无人接收，忽略）
    event_out = pyqtSignal(dict)

    def __init__(self, loader, initial_pack="nailong"):
        super().__init__(None)
        self.loader = loader
        self.pack_id = initial_pack if loader.get(initial_pack) else self._first_pack()
        self.pack = loader.get(self.pack_id)

        self.mode = "stay"          # stay / walk / follow（默认原地待着，散步需右键开启）
        self.size_key = "medium"
        self.click_through = False
        self.topmost = True
        self._card_visible = True   # 状态卡可见性（默认显示，配置可覆盖）
        self._muted = False          # 大笑静音（默认不静音，静音时只播GIF不发声）
        self._bubble_scale = 1.0     # 气泡缩放 0.8-1.2
        self._bubble_duration = 6    # 气泡显示秒数
        self._bubble_display = "all" # all / state_only / off
        self._edge_snap = True       # 拖拽到边缘自动吸附
        self._saved_pos = None      # 记忆位置（配置统一由本类读写）
        self._load_config()

        self._state = STATE_IDLE
        self._tool_count = 0
        self._last_tool = ""
        self._bubbles = {}
        self._click_lines = list(FALLBACK_CLICK)
        self._thoughts = list(FALLBACK_THOUGHTS)
        self._dragging = False
        self._drag_offset = QPoint(0, 0)
        self._clicked_moved = False
        self._bubble_timer = QTimer(self)
        self._bubble_timer.setSingleShot(True)
        self._bubble_timer.timeout.connect(self._hide_bubble)
        self._thought_timer = QTimer(self)
        self._thought_timer.setSingleShot(True)
        self._thought_timer.timeout.connect(self._hide_bubble)
        self._menu = None
        self._follow_timer = QTimer(self)
        self._follow_timer.timeout.connect(self._follow_step)
        self._sway_timer = QTimer(self)
        self._sway_timer.timeout.connect(self._random_sway)
        self._micro_action_level = "medium"  # high/medium/low/off
        self._reduce_motion = False
        self._state_gen = 0  # 每次 apply_state 递增，用于微动作回退检测
        self._movie = None
        self.sprite_pixmap = None
        self._sprite_w = SIZE_PRESETS.get(self.size_key, 175)
        self._audio_player = None
        self._init_audio()

        self._build_ui()
        self._apply_window_flags()
        self._apply_size()
        self._apply_saved_position()
        self.walk = WalkController(self, self.group)
        self._apply_mode()          # 开机应用已保存的移动模式（walk/follow/stay）
        self.apply_pack()
        self.apply_state(STATE_IDLE)
        self._register_hotkey()
        self._apply_micro_action()

    def _register_hotkey(self):
        """注册全局快捷键：Ctrl+Alt+L 大笑，Ctrl+Alt+M 静音。"""
        try:
            user32 = ctypes.windll.user32
            ok1 = user32.RegisterHotKey(
                int(self.winId()), HOTKEY_ID,
                MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, VK_L)
            ok2 = user32.RegisterHotKey(
                int(self.winId()), HOTKEY_ID_MUTE,
                MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, VK_M)
            self._hotkey_registered = bool(ok1) or bool(ok2)
        except Exception:
            self._hotkey_registered = False

    def _unregister_hotkey(self):
        if getattr(self, "_hotkey_registered", False):
            try:
                user32 = ctypes.windll.user32
                user32.UnregisterHotKey(int(self.winId()), HOTKEY_ID)
                user32.UnregisterHotKey(int(self.winId()), HOTKEY_ID_MUTE)
            except Exception:
                pass
            self._hotkey_registered = False

    def nativeEvent(self, eventType, message):
        """处理 Windows 原生消息，捕获 WM_HOTKEY。"""
        try:
            msg = ctypes.wintypes.MSG.from_address(int(message))
            if msg.message == WM_HOTKEY:
                if msg.wParam == HOTKEY_ID:
                    self.trigger_laugh()
                    return True, 0
                elif msg.wParam == HOTKEY_ID_MUTE:
                    self.toggle_mute()
                    return True, 0
        except Exception:
            pass
        return super().nativeEvent(eventType, message)

    # ----------------------------------------------------------
    # 系统托盘
    # ----------------------------------------------------------
    def _icon_path(self):
        """解析托盘图标路径：源码模式用项目 icons/，打包模式用 _MEIPASS。"""
        candidates = []
        if hasattr(sys, "_MEIPASS"):
            candidates.append(os.path.join(sys._MEIPASS, "icons", "pet-icon.ico"))
        # 源码模式：helper/ 的上一级是项目根
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        candidates.append(os.path.join(project_root, "icons", "pet-icon.ico"))
        candidates.append(os.path.join(project_root, "icons", "pet-icon.png"))
        for p in candidates:
            if os.path.isfile(p):
                return p
        return None

    def _init_tray(self):
        """创建系统托盘图标：左键显隐桌宠，右键菜单。"""
        if not QSystemTrayIcon.isSystemTrayAvailable():
            self._tray = None
            return
        icon_path = self._icon_path()
        icon = QIcon(icon_path) if icon_path else self.windowIcon()
        self._tray = QSystemTrayIcon(icon, self)
        self._tray.setToolTip("奶娃桌宠")
        # 左键点击：显示/隐藏
        self._tray.activated.connect(self._on_tray_activated)
        # 右键菜单
        menu = QMenu()
        menu.setStyleSheet(
            "QMenu { background: #fff; border: 1px solid #ddd; border-radius: 8px; padding: 4px; }"
            "QMenu::item { padding: 6px 20px; border-radius: 4px; font-size: 13px; }"
            "QMenu::item:selected { background: #eef1ff; }"
            "QMenu::separator { height: 1px; background: #eee; margin: 4px 8px; }"
        )
        act_show = QAction("显示/隐藏桌宠", self)
        act_show.triggered.connect(self.toggle_visible)
        menu.addAction(act_show)
        menu.addSeparator()
        act_laugh = QAction("让奶龙笑一个", self)
        act_laugh.triggered.connect(self.trigger_laugh)
        menu.addAction(act_laugh)
        act_mute = QAction("大笑静音", self, checkable=True)
        act_mute.setChecked(self._muted)
        act_mute.triggered.connect(self.toggle_mute)
        menu.addAction(act_mute)
        act_autostart = QAction("开机启动", self, checkable=True)
        act_autostart.setChecked(self._autostart_enabled())
        act_autostart.triggered.connect(self.toggle_autostart)
        menu.addAction(act_autostart)
        menu.addSeparator()
        act_settings = QAction("设置…", self)
        act_settings.triggered.connect(self._open_settings)
        menu.addAction(act_settings)
        act_about = QAction("关于", self)
        act_about.triggered.connect(self._show_about)
        menu.addAction(act_about)
        menu.addSeparator()
        act_quit = QAction("退出", self)
        act_quit.triggered.connect(self.quit_pet)
        menu.addAction(act_quit)
        self._tray.setContextMenu(menu)
        self._tray.show()
        self._tray_menu = menu
        self._tray_act_mute = act_mute
        self._tray_act_autostart = act_autostart

    def _on_tray_activated(self, reason):
        """托盘左键：显示/隐藏；双击：大笑。"""
        if reason == QSystemTrayIcon.Trigger:  # 左键单击
            self.toggle_visible()
        elif reason == QSystemTrayIcon.DoubleClick:
            self.trigger_laugh()

    def _refresh_tray_checks(self):
        """同步托盘菜单的勾选状态（静音/开机启动被其他方式改变时调用）。"""
        if not getattr(self, "_tray", None):
            return
        try:
            self._tray_act_mute.setChecked(self._muted)
            self._tray_act_autostart.setChecked(self._autostart_enabled())
        except Exception:
            pass

    def _open_settings(self):
        """打开设置面板。"""
        if not hasattr(self, "_settings_dlg") or self._settings_dlg is None:
            self._settings_dlg = SettingsDialog(self)
        self._settings_dlg._load_values()
        self._settings_dlg.show_at_cursor()

    # ----------------------------------------------------------
    # 初始化
    # ----------------------------------------------------------
    def _first_pack(self):
        packs = self.loader.list()
        return packs[0]["id"] if packs else "nailong"

    def _build_ui(self):
        self.setWindowTitle("奶娃桌宠")
        self.setAttribute(Qt.WA_TranslucentBackground, True)

        self.scene = QGraphicsScene(self)
        self.scene.setBackgroundBrush(Qt.transparent)
        self.view = QGraphicsView(self.scene, self)
        self.view.setFrameShape(QGraphicsView.NoFrame)
        self.view.setStyleSheet("background: transparent; border: none;")
        self.view.setAttribute(Qt.WA_TranslucentBackground, True)
        self.view.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.view.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        # 手动坐标对齐：场景坐标 = 窗口坐标（气泡/卡片/精灵统一坐标系，杜绝居中偏移）
        self.view.setAlignment(Qt.AlignLeft | Qt.AlignTop)

        self.group = PetGroup(self)
        self.scene.addItem(self.group)
        self.sprite = PetSprite()
        self.group.addToGroup(self.sprite)
        self.sprite.setPos(0, 0)

        # 气泡（带尾巴的对话气泡，支持打字机效果）
        self.bubble = BubbleWidget(self)
        self.bubble.hide()
        # 淡入淡出
        self._bubble_opacity = QGraphicsOpacityEffect(self.bubble)
        self._bubble_opacity.setOpacity(0.0)
        self.bubble.setGraphicsEffect(self._bubble_opacity)
        self._bubble_fade = QPropertyAnimation(self._bubble_opacity, b"opacity", self.bubble)
        self._bubble_fade.setDuration(180)

        # 状态卡
        self.card = QLabel(self)
        self.card.setStyleSheet(
            "background: rgba(20,20,30,190); color: #eee; border-radius: 8px;"
            "padding: 5px 9px; font-size: 11px;")
        self.card.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.card.hide()

    def _apply_window_flags(self):
        flags = Qt.FramelessWindowHint | Qt.Tool
        if self.topmost:
            flags |= Qt.WindowStaysOnTopHint
        self.setWindowFlags(flags)

    def _apply_size(self):
        w = SIZE_PRESETS.get(self.size_key, 190)
        self._sprite_w = w
        # 窗口高 = 精灵(≈方形) + 上下留白：气泡(顶) + 状态卡(底) 永不遮挡表情包
        self.setFixedSize(w, w + WINDOW_HEIGHT_PAD)
        self.scene.setSceneRect(0, 0, w, w + WINDOW_HEIGHT_PAD)
        self._layout_overlays()
        self.apply_pack()

    def _layout_overlays(self):
        self.view.setGeometry(0, 0, self.width(), self.height())
        # 精灵在窗口内居中（场景坐标 = 窗口坐标；上下留白给气泡和状态卡）
        pm = getattr(self, "sprite_pixmap", None)
        if pm is not None:
            x = (self.width() - pm.width()) / 2
            y = (self.height() - pm.height()) / 2
            self.sprite.setPos(max(0, x), max(0, y))

    def _init_audio(self):
        """初始化音频播放器（QtMultimedia）。环境不支持则静默降级。"""
        try:
            from PyQt5.QtMultimedia import QMediaPlayer
            self._audio_player = QMediaPlayer(self)
            self._audio_player.setVolume(LAUGH_VOLUME)   # 大笑音量默认 60%
        except Exception:  # noqa: BLE001
            self._audio_player = None

    def _play_laugh_audio(self):
        """任务完成：播放表情包内的 laugh.mp3（静音时跳过）。"""
        if self._muted:
            return
        if self._audio_player is None or self.pack is None:
            return
        mp3_rel = (self.pack.laugh or {}).get("mp3")
        if not mp3_rel:
            return
        abs_path = self.loader.resolve(self.pack, mp3_rel)
        if not abs_path or not os.path.isfile(abs_path):
            return
        try:
            from PyQt5.QtCore import QUrl
            from PyQt5.QtMultimedia import QMediaContent
            self._audio_player.stop()
            self._audio_player.setMedia(QMediaContent(QUrl.fromLocalFile(abs_path)))
            self._audio_player.play()
        except Exception as e:  # noqa: BLE001
            print("[pet-helper] 音频播放失败:", e, flush=True)

    # ----------------------------------------------------------
    # 表情包 / 状态
    # ----------------------------------------------------------
    def apply_pack(self):
        self.pack = self.loader.get(self.pack_id) or self.pack
        self._click_lines = (self.pack.click_bubbles or FALLBACK_CLICK) if self.pack else FALLBACK_CLICK
        self._thoughts = (self.pack.thinking_lines or FALLBACK_THOUGHTS) if self.pack else FALLBACK_THOUGHTS
        self.apply_state(self._state)
        # 通知托盘刷新图标（所有切换路径都经过这里，只发一次）
        self.event_out.emit({"kind": "event", "name": "pack", "packId": self.pack_id})

    def apply_state(self, state):
        self._state_gen += 1  # 状态代计数器：微动作回退时检测是否被真实状态覆盖
        prev_state = getattr(self, "_last_applied_state", None)
        self._last_applied_state = state
        self._state = state
        if state == STATE_TASK_DONE and prev_state == STATE_TASK_DONE:
            # 重复收到 task_done（如冷却期被其他请求重发）：不打断正在播放的大笑 GIF/音频，
            # 只刷新状态卡，避免反复大笑
            self._update_card()
            return
        if self.pack is None:
            return
        # 非大笑状态先停音频（大笑状态会播放新音频）
        if state != STATE_TASK_DONE and self._audio_player:
            try:
                self._audio_player.stop()
            except Exception:
                pass
        rel = self.pack.image_for(state)
        abs_path = self.loader.resolve(self.pack, rel) if rel else None
        # 大笑 → 优先 laugh.gif（专门配置），回退 states 里的图 + 笑声音频
        if state == STATE_TASK_DONE:
            gif_rel = self.pack.laugh_gif()
            gif_abs = self.loader.resolve(self.pack, gif_rel) if gif_rel else None
            if gif_abs and os.path.isfile(gif_abs):
                self._show_gif(gif_abs)
            elif abs_path and os.path.isfile(abs_path):
                if abs_path.lower().endswith(".gif"):
                    self._show_gif(abs_path)
                else:
                    self._show_image(abs_path)
            self._play_laugh_audio()
        else:
            # 非大笑：显示 states 里的图（.gif 播动图，.png/.jpg 显示静态）
            if abs_path and os.path.isfile(abs_path):
                if abs_path.lower().endswith(".gif"):
                    self._show_gif(abs_path)
                else:
                    self._show_image(abs_path)
            elif self._movie:
                self._stop_movie()
        # 出错抖动
        if state == STATE_ERROR:
            ShakeAnimator(self)
        elif state == STATE_TASK_DONE:
            BounceAnimator(self.sprite)
            self._hide_bubble()  # 大笑时隐藏气泡，GIF 是焦点
        # 气泡（大笑时不显示）
        if state != STATE_TASK_DONE:
            style_key = self._STATE_BUBBLE_STYLE.get(state, "normal")
            self.show_bubble(self.pack.bubble_for(state), style_key)
        # 思维链心声（思考时小概率）
        if state == STATE_THINKING and random.random() < 0.35:
            thought = self._thoughts[random.randrange(len(self._thoughts))]
            self._show_thought(thought)
        # 状态卡
        self._update_card()

    def _show_image(self, abs_path):
        self._stop_movie()
        pm = QPixmap(abs_path)
        if pm.isNull():
            return
        pm = pm.scaledToWidth(getattr(self, "_sprite_w", SPRITE_W), Qt.SmoothTransformation)
        self.sprite_pixmap = pm
        self.sprite.set_pixmap(pm)
        self._layout_overlays()

    def _show_gif(self, abs_path):
        if self._movie:
            self._stop_movie()
        movie = QMovie(abs_path)
        movie.setCacheMode(QMovie.CacheAll)
        # 校验 GIF 有效性：损坏/不支持的文件 frameCount 为 0
        if movie.frameCount() == 0:
            print("[pet-helper] GIF 加载失败，回退静态图:", abs_path, flush=True)
            return
        movie.frameChanged.connect(self._on_movie_frame)
        movie.setScaledSize(self.sprite_pixmap.size() if self.sprite_pixmap else None)
        self._movie = movie
        movie.start()

    def _on_movie_frame(self):
        if self._movie is None:
            return
        frame = self._movie.currentPixmap()
        if not frame.isNull():
            w = getattr(self, "_sprite_w", SPRITE_W)
            self.sprite_pixmap = frame.scaledToWidth(w, Qt.SmoothTransformation)
            self.sprite.set_pixmap(self.sprite_pixmap)

    def _stop_movie(self):
        if self._movie:
            self._movie.stop()
            try:
                self._movie.frameChanged.disconnect(self._on_movie_frame)
            except (TypeError, RuntimeError):
                pass  # 信号未连接或对象已失效
            self._movie = None

    def _fallback_pixmap(self):
        return None

    def _update_card(self):
        label = STATE_LABELS.get(self._state, self._state)
        parts = ["🤖 %s" % label]
        if self._last_tool:
            parts.append("🛠 %s" % self._last_tool)
        if self._tool_count:
            parts.append("🔧 x%d" % self._tool_count)
        self.card.setText("  ".join(parts))
        self.card.adjustSize()
        # 状态卡放窗口底部（气泡在顶部、精灵居中，互不遮挡）
        self.card.move(4, self.height() - self.card.height() - 4)
        if self._card_visible:
            self.card.show()
        else:
            self.card.hide()

    # ----------------------------------------------------------
    # 气泡（带尾巴 + 打字机 + 状态配色，精灵上方居中）
    # ----------------------------------------------------------
    # 状态 → 气泡配色 key
    _STATE_BUBBLE_STYLE = {
        STATE_IDLE: "normal", STATE_THINKING: "thinking", STATE_TOOL_CALL: "thinking",
        STATE_STREAMING: "thinking", STATE_ERROR: "error", STATE_USER_MSG: "normal",
    }

    def _layout_bubble(self, text, style_key="normal", typewriter=True):
        """统一气泡布局：设置文字/配色/缩放/位置，淡入显示。"""
        if not text:
            return
        if getattr(self, "_bubble_display", "all") == "off":
            return
        # 气泡最大文本宽度 = 宠物窗口宽度 - 左右边距(8) - 气泡左右padding
        # 确保气泡总宽（文本+padding）不超出宠物窗口
        avail = self.width() - 8 - self.bubble._padding_x * 2
        self.bubble._max_width = max(60, avail)
        self.bubble.set_scale(getattr(self, "_bubble_scale", 1.0))
        self.bubble.set_text(text, style_key, typewriter=typewriter)
        bw = self.bubble.width()
        # 水平居中，确保不溢出窗口左右边界
        x = max(4, min((self.width() - bw) // 2, self.width() - bw - 4))
        self.bubble.move(x, 4)
        self._bubble_fade.stop()
        try:
            self._bubble_fade.finished.disconnect()
        except (TypeError, RuntimeError):
            pass
        self._bubble_fade.setStartValue(self._bubble_opacity.opacity())
        self._bubble_fade.setEndValue(1.0)
        self.bubble.show()
        self._bubble_fade.start()

    def _fade_out_bubble(self):
        self._bubble_fade.stop()
        self._bubble_fade.setStartValue(self._bubble_opacity.opacity())
        self._bubble_fade.setEndValue(0.0)
        try:
            self._bubble_fade.finished.disconnect()
        except (TypeError, RuntimeError):
            pass
        self._bubble_fade.finished.connect(self.bubble.hide)
        self._bubble_fade.start()

    def show_bubble(self, text, style_key="normal", interaction=False):
        if not text:
            return
        # 显示策略：state_only 时只显示状态变化气泡，不显示点击/设置类气泡
        if self._bubble_display == "state_only" and interaction:
            return
        if self._bubble_display == "off":
            return
        self._thought_timer.stop()
        self._layout_bubble(text, style_key)
        duration = int(getattr(self, "_bubble_duration", 6) * 1000)
        self._bubble_timer.start(duration)

    def _show_thought(self, text):
        self._bubble_timer.stop()
        self._layout_bubble(text, "thought", typewriter=False)
        self._thought_timer.start(8000)

    def _hide_bubble(self):
        self.bubble.stop_typewriter()
        self._fade_out_bubble()

    def _apply_micro_action(self):
        """根据活跃程度和减少动态设置，启动/停止空闲微动作定时器。"""
        self._sway_timer.stop()
        if self._reduce_motion or self._micro_action_level == "off":
            return
        interval = {"high": 8000, "medium": 15000, "low": 25000}.get(self._micro_action_level, 15000)
        self._sway_timer.start(interval)

    def set_micro_action_level(self, level):
        self._micro_action_level = level
        self._save_config()
        self._apply_micro_action()

    def toggle_reduce_motion(self):
        self._reduce_motion = not self._reduce_motion
        self._save_config()
        self._apply_micro_action()
        self.show_bubble("已减少动态效果" if self._reduce_motion else "已恢复动态效果", interaction=True)

    def set_bubble_scale(self, scale):
        self._bubble_scale = max(0.8, min(1.2, float(scale)))
        self._save_config()

    def set_bubble_duration(self, seconds):
        self._bubble_duration = max(2, min(15, int(seconds)))
        self._save_config()

    def set_bubble_display(self, mode):
        self._bubble_display = mode if mode in ("all", "state_only", "off") else "all"
        self._save_config()
        if mode == "off":
            self._hide_bubble()

    def toggle_edge_snap(self):
        self._edge_snap = not self._edge_snap
        self._save_config()
        self.show_bubble("边缘吸附已开启" if self._edge_snap else "边缘吸附已关闭", interaction=True)

    def _random_sway(self):
        """空闲微动作：摇摆/蹦跳/短暂表情变化，随机选一个。"""
        if self._state != STATE_IDLE:
            return
        if self._dragging or not self.isVisible():
            return
        roll = random.random()
        if roll < 0.5:
            SwayAnimator(self.sprite)
        elif roll < 0.75:
            BounceAnimator(self.sprite)
        else:
            # 25% 概率短暂表情变化，用状态代计数器防止覆盖真实 AI 状态
            expr = random.choice([STATE_THINKING, STATE_TOOL_CALL])
            gen = self._state_gen
            self.apply_state(expr)
            QTimer.singleShot(1200, lambda: (
                self.apply_state(STATE_IDLE) if self._state_gen == gen + 1 else None
            ))

    # ----------------------------------------------------------
    # 鼠标：拖拽 / 单击 / 右键
    # ----------------------------------------------------------
    def _on_press(self, event):
        """左键按下：先关菜单，暂停走路/跟随（避免拖拽时定时器抢位置），记录拖拽起点。"""
        self._close_menu()
        if self.click_through:
            return
        self._dragging = True
        self._clicked_moved = False
        self._press_pos = event.screenPos()
        self._drag_offset = event.screenPos() - self.frameGeometry().topLeft()
        # 拖拽期间暂停移动定时器，松手后按 mode 恢复
        self._stop_movers()

    def _on_drag_move(self, event):
        """拖拽中：移动窗口。"""
        if not self._dragging:
            return
        if (event.screenPos() - self._press_pos).manhattanLength() > 4:
            self._clicked_moved = True
        self.move(event.screenPos() - self._drag_offset)

    def _on_drag_release(self, event):
        """松手：拖过就说话+存位置，没拖就是单击；最后按 mode 恢复走路/跟随。"""
        if not self._dragging:
            return
        self._dragging = False
        if self._clicked_moved:
            self.show_bubble(self._click_lines[random.randrange(len(self._click_lines))], interaction=True)
            self.event_out.emit({"kind": "event", "name": "drag"})
            self._snap_to_edge()
            self._save_config()
        else:
            self._on_click(event)
        # 拖拽结束，恢复移动模式
        if self.mode == "walk":
            self.walk.start()
        elif self.mode == "follow":
            self._follow_timer.start(40)

    def _snap_to_edge(self):
        """拖拽释放时：如果窗口靠近屏幕边缘，平滑动画吸附到边缘。仅 stay 模式生效。"""
        if not getattr(self, "_edge_snap", True):
            return
        if self.mode != "stay":
            return  # walk/follow 模式下宠物会自己移动，不吸附
        screen = QApplication.primaryScreen().availableGeometry()
        x, y = self.x(), self.y()
        w, h = self.width(), self.height()
        threshold = 60  # 距边缘 60px 内触发吸附
        target_x, target_y = x, y
        snapped = False
        if x < threshold:
            target_x = screen.left()
            snapped = True
        elif x + w > screen.right() - threshold:
            target_x = screen.right() - w
            snapped = True
        if y < threshold:
            target_y = screen.top()
            snapped = True
        elif y + h > screen.bottom() - threshold:
            target_y = screen.bottom() - h
            snapped = True
        if not snapped:
            return
        # 平滑动画滑到吸附位置
        anim = QPropertyAnimation(self, b"pos", self)
        anim.setDuration(200)
        anim.setStartValue(self.pos())
        anim.setEndValue(QPoint(target_x, target_y))
        anim.finished.connect(lambda: self._save_config())
        anim.start()
        # 保存动画引用防止被 GC
        self._snap_anim = anim

    def _on_click(self, event):
        # 随机动画：70% 蹦跳，30% 摇摆
        if random.random() < 0.7:
            BounceAnimator(self.sprite)
        else:
            SwayAnimator(self.sprite)
        # 20% 概率短暂变脸（震惊/思考），用状态代计数器防止覆盖真实 AI 状态
        if random.random() < 0.2 and self._state == STATE_IDLE:
            expr = random.choice([STATE_TOOL_CALL, STATE_THINKING])
            gen = self._state_gen
            self.apply_state(expr)
            QTimer.singleShot(1500, lambda: (
                self.apply_state(STATE_IDLE) if self._state_gen == gen + 1 else None
            ))
        self.show_bubble(self._click_lines[random.randrange(len(self._click_lines))], interaction=True)
        self.event_out.emit({"kind": "event", "name": "click"})

    def _on_right_click(self, event):
        if self._menu_visible():
            self._close_menu()
            return
        self._show_menu(event.screenPos())

    # ----------------------------------------------------------
    # 自定义右键菜单（普通窗口，不抓鼠标，不依赖激活权）
    # ----------------------------------------------------------
    def _menu_visible(self):
        return self._menu is not None and self._menu.isVisible()

    def _close_menu(self):
        if self._menu is not None:
            try:
                self._menu.close()
            except Exception:  # noqa: BLE001
                pass
            self._menu = None

    def _show_menu(self, global_pos):
        if self._menu is None:
            self._menu = PetMenu(self)
        items = []
        # 切换表情包（单选）
        for p in self.loader.list():
            pid = p.get("id")
            items.append(("radio", "%s %s" % (p.get("emoji", "🐾"), p.get("name", pid)),
                          "pack", pid, lambda pid=pid: self.switch_pack(pid),
                          pid == self.pack_id))
        items.append(("sep",))
        # 移动模式（单选）
        for key, label in (("walk", "自由散步"), ("follow", "跟随鼠标"), ("stay", "原地待着")):
            items.append(("radio", label, "mode", key, lambda k=key: self.set_mode(k), self.mode == key))
        # 大小（单选）
        for key, label in (("small", "小"), ("medium", "中"), ("large", "大")):
            items.append(("radio", "大小：" + label, "size", key, lambda k=key: self.set_size(k), self.size_key == key))
        items.append(("sep",))
        items.append(("item", "鼠标穿透" if not self.click_through else "取消穿透", self.toggle_click_through))
        items.append(("item", "取消置顶" if self.topmost else "置顶", self.toggle_topmost))
        items.append(("item", "隐藏状态卡" if self._card_visible else "显示状态卡", self.toggle_card))
        items.append(("sep",))
        items.append(("item", "让奶龙笑一个", self.trigger_laugh))
        items.append(("check", "大笑静音", self._muted, self.toggle_mute))
        items.append(("check", "开机启动", self._autostart_enabled(), self.toggle_autostart))
        items.append(("item", "设置…", self._open_settings))
        items.append(("item", "关于", self._show_about))
        items.append(("sep",))
        items.append(("item", "隐藏", self.hide_pet))
        items.append(("item", "退出", self.quit_pet))
        self._menu.show_menu(global_pos, items)

    def switch_pack(self, pack_id):
        """切换表情包（右键菜单/协议消息）。"""
        if not pack_id or pack_id == self.pack_id or not self.loader.get(pack_id):
            return
        self.pack_id = pack_id
        self.pack = self.loader.get(pack_id)
        self.apply_pack()
        self._save_config()

    # ----------------------------------------------------------
    # 开机启动 / 关于
    # ----------------------------------------------------------
    def _autostart_command(self):
        """开机启动命令行：exe 模式用自身；源码模式用一体化入口 all_in_one.py + pythonw。"""
        if hasattr(sys, "_MEIPASS"):
            return '"%s"' % sys.executable
        py = sys.executable
        if py.lower().endswith("python.exe"):
            candidate = py[:-len("python.exe")] + "pythonw.exe"
            if os.path.isfile(candidate):
                py = candidate
        entry = os.path.join(os.path.dirname(os.path.abspath(__file__)), "all_in_one.py")
        return '"%s" "%s"' % (py, entry)

    def _autostart_enabled(self):
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, AUTOSTART_RUN_KEY, 0, winreg.KEY_QUERY_VALUE) as k:
                winreg.QueryValueEx(k, AUTOSTART_VALUE)
                return True
        except Exception:  # noqa: BLE001
            return False

    def toggle_autostart(self):
        """开机启动开关（写注册表 Run 键）。"""
        try:
            import winreg
            enabled = self._autostart_enabled()
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, AUTOSTART_RUN_KEY, 0,
                                winreg.KEY_SET_VALUE | winreg.KEY_QUERY_VALUE) as k:
                if enabled:
                    winreg.DeleteValue(k, AUTOSTART_VALUE)
                    self.show_bubble("已取消开机启动", interaction=True)
                else:
                    winreg.SetValueEx(k, AUTOSTART_VALUE, 0, winreg.REG_SZ, self._autostart_command())
                    self.show_bubble("已开启开机启动", interaction=True)
            self.event_out.emit({"kind": "event", "name": "autostart", "on": not enabled})
            self._refresh_tray_checks()
        except Exception:  # noqa: BLE001
            self.show_bubble("开机启动设置失败", interaction=True)

    def _show_about(self):
        pack_name = self.pack.name if self.pack else "—"
        QMessageBox.about(
            self, "关于奶娃桌宠",
            "奶娃桌宠 v%s\n\n表情包：%s\n\n让 AI 打工，桌宠陪笑 😄" % (APP_VERSION, pack_name))

    def trigger_laugh(self):
        """手动触发大笑（托盘菜单/快捷键），绕过桥接状态机。"""
        if self._state == STATE_TASK_DONE:
            return  # 已经在笑，不重复
        gen = self._state_gen
        self.apply_state(STATE_TASK_DONE)
        # 大笑时长跟随 pack.json laugh.duration_ms（默认8秒），结束后回到 idle
        duration = 8000
        try:
            d = int((self.pack.laugh or {}).get("duration_ms", 0) or 0)
            if d > 0:
                duration = d
        except Exception:
            pass
        # 用状态代计数器：桥接中途触发新大笑时不被本定时器提前中断
        QTimer.singleShot(duration, lambda: (
            self.apply_state(STATE_IDLE) if self._state_gen == gen + 1 else None
        ))

    def toggle_mute(self):
        """切换大笑静音：静音时只播GIF不发声。"""
        self._muted = not self._muted
        self._save_config()
        if self._muted:
            # 切换到静音时，如果正在播放音频，立即停止
            if self._audio_player:
                try:
                    self._audio_player.stop()
                except Exception:
                    pass
            self.show_bubble("已静音（只笑不叫）", interaction=True)
        else:
            self.show_bubble("取消静音", interaction=True)
        self._refresh_tray_checks()

    # ----------------------------------------------------------
    # 菜单动作
    # ----------------------------------------------------------
    def set_mode(self, mode):
        self.mode = mode
        self._stop_movers()
        self._apply_mode()
        self._save_config()
        self.event_out.emit({"kind": "event", "name": "mode", "mode": mode})

    def _apply_mode(self):
        """按当前 mode 启动对应移动逻辑（开机/切换菜单统一入口）。"""
        if self.mode == "walk":
            self.walk.start()
        elif self.mode == "follow":
            self._follow_timer.start(40)

    def set_size(self, key):
        self.size_key = key
        self._apply_size()
        self._save_config()

    def toggle_click_through(self):
        self.click_through = not self.click_through
        self.setAttribute(Qt.WA_TransparentForMouseEvents, self.click_through)
        self._save_config()
        if self.click_through:
            # 穿透后桌宠本体点不到，气泡提示用户从托盘取消
            self.show_bubble("穿透已开启，右键托盘取消", interaction=True)
        self.event_out.emit({"kind": "event", "name": "click_through", "on": self.click_through})

    def toggle_topmost(self):
        self.topmost = not self.topmost
        was_visible = self.isVisible()
        self._apply_window_flags()
        if was_visible:
            self.show()
        self._save_config()

    def toggle_card(self):
        self._card_visible = not self._card_visible
        if self._card_visible:
            self.card.show()
        else:
            self.card.hide()
        self._save_config()

    def toggle_visible(self):
        """托盘用：显示/隐藏。"""
        if self.isVisible():
            self.hide()
            self.event_out.emit({"kind": "event", "name": "hidden"})
        else:
            self.show()

    def hide_pet(self):
        self.hide()
        self.event_out.emit({"kind": "event", "name": "hidden"})

    def quit_pet(self):
        self._mark_user_exit()
        self.event_out.emit({"kind": "event", "name": "exited"})
        self.close()

    def _mark_user_exit(self):
        """标记用户主动退出，防止崩溃自重启机制误重启。"""
        try:
            d = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "pet-nailong")
            os.makedirs(d, exist_ok=True)
            with open(os.path.join(d, ".user_exit"), "w", encoding="utf-8") as f:
                f.write("1")
        except Exception:
            pass

    # ----------------------------------------------------------
    # 移动逻辑
    # ----------------------------------------------------------
    def _stop_movers(self):
        if hasattr(self, "walk"):
            self.walk.stop()
        self._follow_timer.stop()

    def _follow_step(self):
        from PyQt5.QtGui import QCursor
        cursor = QCursor.pos()
        screen_obj = self.screen() or QApplication.primaryScreen()
        if screen_obj is None:
            return
        screen = screen_obj.availableGeometry()
        target_x = cursor.x() - self.width() / 2
        target_y = max(screen.top() + 10, min(cursor.y() + 40, screen.bottom() - self.height() - 10))
        target_x = max(screen.left(), min(target_x, screen.right() - self.width()))
        cx, cy = self.x(), self.y()
        nx = cx + (target_x - cx) * 0.12
        ny = cy + (target_y - cy) * 0.12
        if abs(target_x - cx) > 4:
            self.group.face(target_x < cx)
        else:
            self.group.face(False)
        self.move(int(nx), int(ny))

    # ----------------------------------------------------------
    # host 消息入口
    # ----------------------------------------------------------
    def on_host_message(self, msg):
        kind = msg.get("kind")
        if kind == "state":
            state = msg.get("state", STATE_IDLE)
            # 注意：state 消息里的 packId 是桥接侧的默认包，不强制切换——
            # 用户右键切换的包才是准的，避免每次状态更新都被切回默认包。
            # 显式切包走 kind=="pack" 消息。
            if "toolCount" in msg:
                self._tool_count = msg.get("toolCount", 0)
            if "lastTool" in msg:
                self._last_tool = msg.get("lastTool") or ""
            self.apply_state(state)
        elif kind == "pack":
            pack_id = msg.get("packId")
            if pack_id and self.loader.get(pack_id):
                self.pack_id = pack_id
                self.pack = self.loader.get(pack_id)
                self.apply_pack()
                self._save_config()
        elif kind == "ping":
            self.event_out.emit({"kind": "pong"})
        elif kind == "shutdown":
            self.close()

    def start_auto_walk(self):
        """demo 模式启动自动散步。"""
        self.walk.start()

    # ----------------------------------------------------------
    # 配置记忆
    # ----------------------------------------------------------
    def _config_path(self):
        """配置路径：exe 打包后写 %APPDATA%/pet-nailong/config.json（持久），源码模式写 helper/config.json。"""
        if hasattr(sys, "_MEIPASS"):
            base = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "pet-nailong")
            try:
                os.makedirs(base, exist_ok=True)
            except Exception:  # noqa: BLE001
                pass
            return os.path.join(base, "config.json")
        return os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")

    def _load_config(self):
        try:
            with open(self._config_path(), "r", encoding="utf-8") as f:
                cfg = json.load(f)
            self.mode = cfg.get("mode", "stay")   # 默认原地待着（与 README 一致）
            self.size_key = cfg.get("size", "medium")
            self.click_through = cfg.get("clickThrough", False)
            self.topmost = cfg.get("topmost", True)
            self._card_visible = cfg.get("cardVisible", True)
            self._muted = cfg.get("muted", False)
            self._bubble_scale = float(cfg.get("bubbleScale", 1.0))
            self._bubble_duration = int(cfg.get("bubbleDuration", 6))
            self._bubble_display = cfg.get("bubbleDisplay", "all")
            self._edge_snap = cfg.get("edgeSnap", True)
            self._micro_action_level = cfg.get("microAction", "medium")
            self._reduce_motion = cfg.get("reduceMotion", False)
            self._saved_pos = cfg.get("pos")
            saved_pack = cfg.get("packId")
            if saved_pack and self.loader.get(saved_pack):
                self.pack_id = saved_pack
                self.pack = self.loader.get(saved_pack)
        except Exception:  # noqa: BLE001
            pass

    def _apply_saved_position(self):
        """应用记忆位置（贴屏钳制，避免换显示器后跑到屏幕外）；无记忆则放屏幕右下角。"""
        screen = QApplication.primaryScreen().availableGeometry()
        pos = getattr(self, "_saved_pos", None)
        if isinstance(pos, dict) and "x" in pos and "y" in pos:
            try:
                x = max(screen.left(), min(int(pos["x"]), screen.right() - self.width()))
                y = max(screen.top(), min(int(pos["y"]), screen.bottom() - self.height()))
                self.move(x, y)
                return
            except Exception:  # noqa: BLE001
                pass
        self.move(screen.right() - self.width() - 24, screen.bottom() - self.height() - 24)

    def _save_config(self):
        try:
            cfg = {
                "mode": self.mode,
                "size": self.size_key,
                "clickThrough": self.click_through,
                "topmost": self.topmost,
                "cardVisible": self._card_visible,
                "muted": self._muted,
                "bubbleScale": self._bubble_scale,
                "bubbleDuration": self._bubble_duration,
                "bubbleDisplay": self._bubble_display,
                "edgeSnap": self._edge_snap,
                "microAction": self._micro_action_level,
                "reduceMotion": self._reduce_motion,
                "packId": self.pack_id,
                "pos": {"x": self.x(), "y": self.y()},
            }
            with open(self._config_path(), "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
        except Exception:  # noqa: BLE001
            pass

    def closeEvent(self, event):
        self._mark_user_exit()
        self._unregister_hotkey()
        self._stop_movers()
        if self._movie:
            self._stop_movie()
        if self._audio_player:
            try:
                self._audio_player.stop()
            except Exception:
                pass
        if getattr(self, "_tray", None):
            try:
                self._tray.hide()
            except Exception:
                pass
        self._save_config()
        super().closeEvent(event)
        # 托盘图标会让应用在窗口关闭后仍存活，显式退出
        from PyQt5.QtWidgets import QApplication
        app = QApplication.instance()
        if app is not None:
            app.quit()
