# -*- coding: utf-8 -*-
"""
奶娃桌宠表情包可视化编辑器（美化版）
====================================
拖拽或选择图片，分配到各个状态，一键生成表情包。
"""
import sys
import os
import json
import shutil
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QGridLayout, QLabel, QLineEdit, QPushButton, QFileDialog,
    QListWidget, QListWidgetItem, QGroupBox, QScrollArea,
    QAbstractItemView, QFrame, QCheckBox
)
from PyQt5.QtCore import Qt, QSize, QTimer
from PyQt5.QtGui import QPixmap, QIcon, QFont
from PyQt5.QtNetwork import QLocalSocket, QLocalServer

from state_table import (STATE_ERROR, STATE_IDLE, STATE_STREAMING, STATE_TASK_DONE,
                         STATE_THINKING, STATE_TOOL_CALL, STATE_USER_MSG)

# ============================================================
# 全局 QSS 样式表 —— 奶龙暖色调主题
# ============================================================
GLOBAL_QSS = """
QMainWindow {
    background: #f5f6f8;
}

/* 顶部标题横幅 */
#HeaderBanner {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 #FFB300, stop:0.5 #FFA000, stop:1 #FF8F00);
    border-radius: 0px;
}
#HeaderTitle {
    color: white;
    font-size: 20px;
    font-weight: bold;
    padding-left: 16px;
}
#HeaderSubtitle {
    color: rgba(255,255,255,0.85);
    font-size: 12px;
    padding-left: 16px;
    padding-bottom: 8px;
}

/* 分组卡片 */
QGroupBox {
    background: white;
    border: 1px solid #e8e8e8;
    border-radius: 10px;
    margin-top: 18px;
    padding-top: 6px;
    font-size: 13px;
    font-weight: 600;
    color: #444;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 14px;
    padding: 0 8px;
    background: white;
    border-radius: 4px;
}

/* 状态卡片 —— 带左侧色条 */
#StateCard {
    background: white;
    border: 1px solid #e8e8e8;
    border-radius: 10px;
    margin-top: 0px;
    padding-top: 0px;
    font-size: 13px;
    font-weight: 600;
}
#StateCard::title {
    subcontrol-origin: margin;
    left: 14px;
    top: 2px;
    padding: 0 6px;
    background: white;
}

/* 输入框 */
QLineEdit {
    border: 1.5px solid #e0e0e0;
    border-radius: 6px;
    padding: 7px 10px;
    background: #fafafa;
    font-size: 13px;
    color: #333;
    selection-background-color: #FFB300;
}
QLineEdit:focus {
    border-color: #FFA000;
    background: white;
}
QLineEdit:read-only {
    background: #f0f0f0;
    color: #666;
}

/* 普通按钮 */
QPushButton {
    background: #f5f5f5;
    border: 1px solid #ddd;
    border-radius: 6px;
    padding: 6px 14px;
    font-size: 12px;
    color: #555;
}
QPushButton:hover {
    background: #eeeeee;
    border-color: #ccc;
    color: #333;
}
QPushButton:pressed {
    background: #e0e0e0;
}

/* 主按钮 —— 橙色渐变 */
#PrimaryButton {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #FFB300, stop:1 #FF8F00);
    border: none;
    border-radius: 8px;
    color: white;
    font-size: 15px;
    font-weight: bold;
    padding: 10px 20px;
}
#PrimaryButton:hover {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #FFA000, stop:1 #FB8C00);
}
#PrimaryButton:pressed {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #FB8C00, stop:1 #F57C00);
}

/* 次要按钮 —— 蓝色 */
#SecondaryButton {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #64B5F6, stop:1 #42A5F5);
    border: none;
    border-radius: 8px;
    color: white;
    font-size: 14px;
    font-weight: bold;
    padding: 10px 18px;
}
#SecondaryButton:hover {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #42A5F5, stop:1 #2196F3);
}
#SecondaryButton:pressed {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #2196F3, stop:1 #1E88E5);
}

/* 图片列表区域 */
QListWidget {
    background: #fafbfc;
    border: 1.5px dashed #ddd;
    border-radius: 8px;
    padding: 6px;
    outline: none;
}
QListWidget::item {
    border-radius: 4px;
    padding: 2px;
}
QListWidget::item:selected {
    background: #FFE0B2;
    color: #333;
}

/* 标签 */
QLabel {
    color: #555;
    font-size: 13px;
}

/* 滚动条 */
QScrollBar:vertical {
    background: transparent;
    width: 8px;
    margin: 0;
}
QScrollBar::handle:vertical {
    background: #ccc;
    border-radius: 4px;
    min-height: 30px;
}
QScrollBar::handle:vertical:hover {
    background: #aaa;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0;
}

/* 状态栏 */
QStatusBar {
    background: white;
    border-top: 1px solid #eee;
    color: #888;
    font-size: 12px;
}

/* 分隔线 */
QFrame[frameShape="4"] {
    color: #e0e0e0;
    max-height: 1px;
}
"""

# ============================================================
# 状态配置：(id, 中文名, 主题色)
# ============================================================
STATES = [
    (STATE_IDLE,      "空闲",     "#9E9E9E"),
    (STATE_THINKING,  "思考中",   "#42A5F5"),
    (STATE_TOOL_CALL, "工具调用", "#AB47BC"),
    (STATE_STREAMING, "输出中",   "#26C6DA"),
    (STATE_TASK_DONE, "任务完成", "#FFA000"),
    (STATE_ERROR,     "出错了",   "#EF5350"),
    (STATE_USER_MSG,  "用户消息", "#FFCA28"),
]

STATE_COLORS = {sid: color for sid, _, color in STATES}

DEFAULT_BUBBLES = {
    STATE_IDLE: "摸鱼中…",
    STATE_THINKING: "嗯…",
    STATE_TOOL_CALL: "这是在干嘛？",
    STATE_STREAMING: "认真听",
    STATE_TASK_DONE: "哈哈哈哈",
    STATE_ERROR: "又崩了…",
    STATE_USER_MSG: "来活了！",
}

FOLDER_MAP = {
    "idle": STATE_IDLE, "normal": STATE_IDLE, "default": STATE_IDLE,
    "thinking": STATE_THINKING, "think": STATE_THINKING, "thought": STATE_THINKING,
    "tool_call": STATE_TOOL_CALL, "tool": STATE_TOOL_CALL, "shock": STATE_TOOL_CALL, "surprised": STATE_TOOL_CALL,
    "streaming": STATE_STREAMING, "serious": STATE_STREAMING, "output": STATE_STREAMING,
    "task_done": STATE_TASK_DONE, "laugh": STATE_TASK_DONE, "happy": STATE_TASK_DONE, "done": STATE_TASK_DONE,
    "error": STATE_ERROR, "err": STATE_ERROR, "fail": STATE_ERROR,
    "user_msg": STATE_USER_MSG, "user": STATE_USER_MSG, "message": STATE_USER_MSG,
}

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}


# ============================================================
# Toast 内联提示条（替代系统弹窗）
# ============================================================
class Toast(QFrame):
    """界面内提示条，从顶部滑入，定时自动消失。"""

    COLORS = {
        "info":    ("#E3F2FD", "#1565C0", "#1976D2"),
        "success": ("#E8F5E9", "#2E7D32", "#388E3C"),
        "warning": ("#FFF3E0", "#E65100", "#F57C00"),
        "error":   ("#FFEBEE", "#C62828", "#E53935"),
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Toast")
        self.setVisible(False)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide_toast)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 10, 12, 10)
        layout.setSpacing(10)

        self.icon_label = QLabel()
        self.icon_label.setFixedSize(20, 20)
        self.icon_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.icon_label)

        self.msg_label = QLabel()
        self.msg_label.setWordWrap(True)
        self.msg_label.setStyleSheet("font-size: 13px; font-weight: 500;")
        layout.addWidget(self.msg_label, stretch=1)

        close_btn = QPushButton("✕")
        close_btn.setFixedSize(24, 24)
        close_btn.setCursor(Qt.PointingHandCursor)
        close_btn.setStyleSheet("""
            QPushButton { border: none; border-radius: 12px; background: transparent;
                          font-size: 14px; color: #666; }
            QPushButton:hover { background: rgba(0,0,0,0.08); color: #333; }
        """)
        close_btn.clicked.connect(self.hide_toast)
        layout.addWidget(close_btn)

    def show_message(self, message, msg_type="info", duration=3500):
        bg, text_color, border_color = self.COLORS.get(msg_type, self.COLORS["info"])
        icons = {"info": "ℹ", "success": "✓", "warning": "⚠", "error": "✕"}
        self.icon_label.setText(icons.get(msg_type, "ℹ"))
        self.icon_label.setStyleSheet(f"color: {text_color}; font-size: 16px; font-weight: bold;")
        self.msg_label.setText(message)
        self.msg_label.setStyleSheet(f"font-size: 13px; font-weight: 500; color: {text_color};")
        self.setStyleSheet(f"""
            #Toast {{
                background: {bg};
                border: 1px solid {border_color};
                border-radius: 8px;
            }}
        """)
        self.setVisible(True)
        self.raise_()
        if duration > 0:
            self._timer.start(duration)
        else:
            self._timer.stop()

    def hide_toast(self):
        self.setVisible(False)


# ============================================================
# 状态卡片
# ============================================================
class StateCard(QGroupBox):
    """带主题色的状态卡片。"""

    def __init__(self, state_id, state_name, color, parent=None):
        super().__init__(state_name, parent)
        self.setObjectName("StateCard")
        self.state_id = state_id
        self.color = color
        self.images = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 16, 12, 10)
        layout.setSpacing(8)

        # 色条 + 标题行
        header = QHBoxLayout()
        header.setSpacing(6)
        # 左侧色条
        color_bar = QFrame()
        color_bar.setFixedSize(4, 16)
        color_bar.setStyleSheet(f"background: {color}; border-radius: 2px;")
        header.addWidget(color_bar)
        # 状态名
        title_label = QLabel(state_name)
        title_label.setStyleSheet(f"color: {color}; font-weight: bold; font-size: 13px;")
        header.addWidget(title_label)
        # 计数
        self.count_label = QLabel("0 张")
        self.count_label.setStyleSheet("color: #aaa; font-size: 11px;")
        header.addWidget(self.count_label)
        header.addStretch()
        layout.addLayout(header)

        # 缩略图列表
        self.list_widget = QListWidget()
        self.list_widget.setViewMode(QListWidget.IconMode)
        self.list_widget.setIconSize(QSize(60, 60))
        self.list_widget.setResizeMode(QListWidget.Adjust)
        self.list_widget.setMovement(QListWidget.Static)
        self.list_widget.setSpacing(6)
        self.list_widget.setFixedHeight(96)
        self.list_widget.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.list_widget.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        layout.addWidget(self.list_widget)

        # 按钮行
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(6)
        add_btn = QPushButton("+ 添加")
        add_btn.setFixedHeight(28)
        add_btn.setCursor(Qt.PointingHandCursor)
        add_btn.clicked.connect(self.add_images)
        del_btn = QPushButton("删除")
        del_btn.setFixedHeight(28)
        del_btn.setCursor(Qt.PointingHandCursor)
        del_btn.clicked.connect(self.remove_selected)
        clear_btn = QPushButton("清空")
        clear_btn.setFixedHeight(28)
        clear_btn.setCursor(Qt.PointingHandCursor)
        clear_btn.clicked.connect(self.clear_all)
        btn_layout.addWidget(add_btn)
        btn_layout.addWidget(del_btn)
        btn_layout.addWidget(clear_btn)
        layout.addLayout(btn_layout)

    def _update_count(self):
        n = len(self.images)
        self.count_label.setText(f"{n} 张" if n > 0 else "空")
        self.count_label.setStyleSheet(
            f"color: {self.color if n > 0 else '#ccc'}; font-size: 11px;"
        )

    def add_images(self):
        files, _ = QFileDialog.getOpenFileNames(
            self, "选择图片", "",
            "图片文件 (*.png *.jpg *.jpeg *.gif *.webp *.bmp)"
        )
        for f in files:
            self._add_image(f)

    def _add_image(self, path):
        if path in self.images:
            return
        self.images.append(path)
        item = QListWidgetItem()
        pixmap = QPixmap(path)
        if not pixmap.isNull():
            item.setIcon(QIcon(pixmap))
        item.setToolTip(f"{os.path.basename(path)}\n{path}")
        item.setTextAlignment(Qt.AlignCenter)
        self.list_widget.addItem(item)
        self._update_count()

    def add_image_path(self, path):
        self._add_image(path)

    def remove_selected(self):
        for item in self.list_widget.selectedItems():
            path = item.toolTip().split("\n")[-1]
            if path in self.images:
                self.images.remove(path)
            self.list_widget.takeItem(self.list_widget.row(item))
        self._update_count()

    def clear_all(self):
        self.images.clear()
        self.list_widget.clear()
        self._update_count()

    def get_images(self):
        return list(self.images)


# ============================================================
# 主窗口
# ============================================================
class PackEditor(QMainWindow):
    def __init__(self, output_dir=None, on_pack_created=None):
        super().__init__()
        self._on_pack_created = on_pack_created  # 集成模式：生成后回调桌宠重载
        self.setWindowTitle("奶娃桌宠表情包编辑器")
        self.setMinimumSize(1000, 760)

        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # === 顶部横幅 ===
        banner = QFrame()
        banner.setObjectName("HeaderBanner")
        banner.setFixedHeight(64)
        banner_layout = QVBoxLayout(banner)
        banner_layout.setContentsMargins(20, 10, 20, 8)
        banner_layout.setSpacing(0)
        title = QLabel("🐉 奶娃桌宠表情包编辑器")
        title.setObjectName("HeaderTitle")
        subtitle = QLabel("为每个状态分配图片，一键生成 pack.json 并导入桌宠")
        subtitle.setObjectName("HeaderSubtitle")
        banner_layout.addWidget(title)
        banner_layout.addWidget(subtitle)
        main_layout.addWidget(banner)

        # === Toast 提示条（界面内提示，不弹系统窗） ===
        self.toast = Toast()
        self.toast.setVisible(False)
        toast_wrap = QWidget()
        toast_layout = QHBoxLayout(toast_wrap)
        toast_layout.setContentsMargins(16, 8, 16, 0)
        toast_layout.addWidget(self.toast)
        main_layout.addWidget(toast_wrap)

        # === 内容区（带边距） ===
        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(16, 14, 16, 14)
        content_layout.setSpacing(12)

        # --- 基本信息卡片 ---
        info_group = QGroupBox("基本信息")
        info_layout = QGridLayout(info_group)
        info_layout.setContentsMargins(16, 20, 16, 14)
        info_layout.setHorizontalSpacing(12)
        info_layout.setVerticalSpacing(10)

        info_layout.addWidget(QLabel("表情包名称"), 0, 0)
        self.name_edit = QLineEdit("我的表情包")
        self.name_edit.setPlaceholderText("给表情包起个名字")
        info_layout.addWidget(self.name_edit, 0, 1)

        info_layout.addWidget(QLabel("表情包 ID"), 0, 2)
        self.id_edit = QLineEdit("mypack")
        self.id_edit.setPlaceholderText("英文/数字，用于文件夹名")
        info_layout.addWidget(self.id_edit, 0, 3)

        info_layout.addWidget(QLabel("输出目录"), 1, 0)
        default_output = output_dir or os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "packs")
        self.output_edit = QLineEdit(os.path.abspath(default_output))
        self.output_edit.setPlaceholderText("桌宠项目的 packs 目录")
        info_layout.addWidget(self.output_edit, 1, 1, 1, 2)
        browse_btn = QPushButton("浏览…")
        browse_btn.setFixedHeight(32)
        browse_btn.setCursor(Qt.PointingHandCursor)
        browse_btn.clicked.connect(self.browse_output)
        info_layout.addWidget(browse_btn, 1, 3)

        # 覆盖已存在
        self.overwrite_check = QCheckBox("生成时自动覆盖已存在的同名表情包")
        self.overwrite_check.setChecked(True)
        self.overwrite_check.setStyleSheet("color: #888; font-size: 12px;")
        info_layout.addWidget(self.overwrite_check, 2, 0, 1, 4)

        content_layout.addWidget(info_group)

        # --- 状态卡片网格 ---
        states_group = QGroupBox("状态图片分配")
        states_layout = QVBoxLayout(states_group)
        states_layout.setContentsMargins(16, 20, 16, 14)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll_widget = QWidget()
        grid = QGridLayout(scroll_widget)
        grid.setSpacing(12)
        grid.setContentsMargins(0, 0, 0, 0)

        self.state_cards = {}
        for i, (state_id, state_name, color) in enumerate(STATES):
            card = StateCard(state_id, state_name, color)
            self.state_cards[state_id] = card
            row = i // 2
            col = i % 2
            grid.addWidget(card, row, col)

        # 最后一行如果只有一个卡片，让它占满
        if len(STATES) % 2 == 1:
            grid.setColumnStretch(0, 1)
            grid.setColumnStretch(1, 1)

        scroll.setWidget(scroll_widget)
        states_layout.addWidget(scroll)
        content_layout.addWidget(states_group, stretch=1)

        # --- 大笑配置卡片 ---
        laugh_group = QGroupBox("大笑配置（可选）")
        laugh_layout = QGridLayout(laugh_group)
        laugh_layout.setContentsMargins(16, 20, 16, 14)
        laugh_layout.setHorizontalSpacing(12)
        laugh_layout.setVerticalSpacing(10)

        laugh_layout.addWidget(QLabel("laugh.gif"), 0, 0)
        self.laugh_gif_edit = QLineEdit()
        self.laugh_gif_edit.setReadOnly(True)
        self.laugh_gif_edit.setPlaceholderText("大笑动图，不填则用 task_done 第一张图")
        laugh_layout.addWidget(self.laugh_gif_edit, 0, 1)
        gif_btn = QPushButton("选择…")
        gif_btn.setFixedHeight(32)
        gif_btn.setCursor(Qt.PointingHandCursor)
        gif_btn.clicked.connect(lambda: self._select_file("选择 laugh.gif", "GIF文件 (*.gif)", self.laugh_gif_edit))
        laugh_layout.addWidget(gif_btn, 0, 2)

        laugh_layout.addWidget(QLabel("laugh.mp3"), 1, 0)
        self.laugh_mp3_edit = QLineEdit()
        self.laugh_mp3_edit.setReadOnly(True)
        self.laugh_mp3_edit.setPlaceholderText("大笑音效，不填则静音")
        laugh_layout.addWidget(self.laugh_mp3_edit, 1, 1)
        mp3_btn = QPushButton("选择…")
        mp3_btn.setFixedHeight(32)
        mp3_btn.setCursor(Qt.PointingHandCursor)
        mp3_btn.clicked.connect(lambda: self._select_file("选择 laugh.mp3", "音频文件 (*.mp3 *.wav *.ogg)", self.laugh_mp3_edit))
        laugh_layout.addWidget(mp3_btn, 1, 2)

        content_layout.addWidget(laugh_group)

        # --- 底部操作按钮 ---
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(12)

        batch_btn = QPushButton("📁  从文件夹批量导入")
        batch_btn.setObjectName("SecondaryButton")
        batch_btn.setMinimumHeight(44)
        batch_btn.setCursor(Qt.PointingHandCursor)
        batch_btn.setToolTip("选择文件夹，按子文件夹名(idle/thinking/laugh等)自动分配图片")
        batch_btn.clicked.connect(self.batch_import)
        btn_layout.addWidget(batch_btn)

        generate_btn = QPushButton("✨  生成并导入表情包")
        generate_btn.setObjectName("PrimaryButton")
        generate_btn.setMinimumHeight(44)
        generate_btn.setCursor(Qt.PointingHandCursor)
        generate_btn.clicked.connect(self.generate_pack)
        btn_layout.addWidget(generate_btn, stretch=2)

        content_layout.addLayout(btn_layout)

        main_layout.addWidget(content)

        # 状态栏
        self.statusBar().showMessage("就绪。为每个状态添加图片，或点击「从文件夹批量导入」。")

    # ---------- 辅助方法 ----------

    def browse_output(self):
        folder = QFileDialog.getExistingDirectory(self, "选择输出目录", self.output_edit.text())
        if folder:
            self.output_edit.setText(folder)

    def _select_file(self, title, file_filter, edit_widget):
        file, _ = QFileDialog.getOpenFileName(self, title, "", file_filter)
        if file:
            edit_widget.setText(file)

    # ---------- 批量导入 ----------

    def batch_import(self):
        folder = QFileDialog.getExistingDirectory(self, "选择图片文件夹（按子文件夹名自动分配状态）")
        if not folder:
            return

        added = 0
        for name in os.listdir(folder):
            full = os.path.join(folder, name)
            if not os.path.isdir(full):
                continue
            state_id = FOLDER_MAP.get(name.lower())
            if state_id is None:
                continue
            card = self.state_cards.get(state_id)
            if card is None:
                continue
            for img in sorted(os.listdir(full)):
                if os.path.splitext(img)[1].lower() in IMAGE_EXTS:
                    card.add_image_path(os.path.join(full, img))
                    added += 1

        for name in os.listdir(folder):
            lower = name.lower()
            full = os.path.join(folder, name)
            if not os.path.isfile(full):
                continue
            if lower == "laugh.gif":
                self.laugh_gif_edit.setText(full)
            elif lower in ("laugh.mp3", "laugh.wav", "laugh.ogg"):
                self.laugh_mp3_edit.setText(full)

        self.statusBar().showMessage(f"批量导入完成：共添加 {added} 张图片。可继续手动调整后生成。")
        if added > 0:
            self.toast.show_message(f"批量导入成功，共添加 {added} 张图片。可继续调整后点击「生成并导入」。", "success")
        else:
            self.toast.show_message("未找到可导入的图片。请确保文件夹内有 idle/thinking/laugh 等子文件夹，或根目录有图片。", "warning")

    # ---------- 生成表情包 ----------

    def generate_pack(self):
        name = self.name_edit.text().strip()
        pack_id = self.id_edit.text().strip()
        output_dir = self.output_edit.text().strip()

        if not name or not pack_id or not output_dir:
            self.toast.show_message("请填写名称、ID和输出目录。", "warning")
            return

        total_images = sum(len(card.get_images()) for card in self.state_cards.values())
        if total_images == 0:
            self.toast.show_message("请至少为一个状态添加图片。", "warning")
            return

        pack_dir = os.path.join(output_dir, pack_id)

        # 已存在且未勾选自动覆盖 → 提示并中止
        if os.path.exists(pack_dir) and not self.overwrite_check.isChecked():
            self.toast.show_message(
                f"同名表情包已存在：{pack_dir}。请勾选「自动覆盖」或换个 ID。",
                "warning", duration=5000
            )
            return

        try:
            if os.path.exists(pack_dir):
                shutil.rmtree(pack_dir)
            os.makedirs(pack_dir, exist_ok=True)

            states = {}
            for state_id, card in self.state_cards.items():
                images = card.get_images()
                if not images:
                    continue
                rel_paths = []
                for i, img_path in enumerate(images):
                    ext = os.path.splitext(img_path)[1].lower()
                    rel_name = f"{state_id}_{i}{ext}"
                    rel_path = f"{state_id}/{rel_name}"
                    dst = os.path.join(pack_dir, rel_path)
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    shutil.copy2(img_path, dst)
                    rel_paths.append(rel_path)
                states[state_id] = rel_paths

            if STATE_IDLE not in states:
                for key, imgs in states.items():
                    if imgs:
                        states[STATE_IDLE] = [imgs[0]]
                        break

            laugh = {}
            laugh_gif = self.laugh_gif_edit.text().strip()
            laugh_mp3 = self.laugh_mp3_edit.text().strip()
            if laugh_gif and os.path.isfile(laugh_gif):
                shutil.copy2(laugh_gif, os.path.join(pack_dir, "laugh.gif"))
                laugh["gif"] = "laugh.gif"
            if laugh_mp3 and os.path.isfile(laugh_mp3):
                ext = os.path.splitext(laugh_mp3)[1].lower()
                shutil.copy2(laugh_mp3, os.path.join(pack_dir, f"laugh{ext}"))
                laugh["mp3"] = f"laugh{ext}"
            if laugh:
                laugh["duration_ms"] = 8000

            pack = {
                "id": pack_id,
                "name": name,
                "emoji": "🐉",
                "version": "1.0.0",
                "author": "pack-editor",
                "states": states,
                "bubbles": DEFAULT_BUBBLES,
                "clickBubbles": ["哈哈哈哈", "看我干嘛", "又在摸鱼？", "嘿嘿", "你好呀"],
                "thinkingLines": ["（这题有点难…）", "（假装在思考）", "（先想个借口）", "（摸鱼被发现了）"],
            }
            if laugh:
                pack["laugh"] = laugh

            pack_json_path = os.path.join(pack_dir, "pack.json")
            with open(pack_json_path, "w", encoding="utf-8") as f:
                json.dump(pack, f, ensure_ascii=False, indent=2)

            self.statusBar().showMessage(f"成功！表情包已生成到 {pack_dir}")
            self.toast.show_message(
                f"表情包「{name}」已生成！{len(states)} 个状态，共 {sum(len(v) for v in states.values())} 张图。",
                "success", duration=5000
            )
            # 集成模式：通知桌宠重载表情包
            if self._on_pack_created:
                try:
                    self._on_pack_created(pack_id)
                except Exception:
                    pass

        except Exception as e:
            self.toast.show_message(f"生成失败：{str(e)}", "error", duration=6000)


def main():
    # ---- 错误日志：pythonw 无控制台，异常写文件便于排查 ----
    import traceback
    log_path = os.path.join(os.path.expanduser("~"), "pack_editor_error.log")
    def _excepthook(tp, val, tb):
        try:
            with open(log_path, "a", encoding="utf-8") as f:
                import datetime
                f.write("==== %s ====\n" % datetime.datetime.now())
                traceback.print_exception(tp, val, tb, file=f)
        except Exception:
            pass
    sys.excepthook = _excepthook

    app = QApplication(sys.argv)
    WINDOW_TITLE = "奶娃桌宠表情包编辑器"
    SERVER_NAME = "nailong_pack_editor_v1"

    # ---- 单实例检测：优先用 FindWindow 找真实窗口 ----
    try:
        import ctypes
        user32 = ctypes.windll.user32
        hwnd = user32.FindWindowW(None, WINDOW_TITLE)
        if hwnd:
            # 窗口已存在：恢复 + 置顶 + 激活，然后退出
            user32.ShowWindow(hwnd, 9)  # SW_RESTORE
            user32.SetForegroundWindow(hwnd)
            return
    except Exception:
        pass

    # 窗口没找到，但可能有残留进程在监听 QLocalServer。
    # 尝试连接，如果连接成功说明有残留但窗口不可见 → 清理服务名后强制启动新实例。
    probe = QLocalSocket()
    probe.connectToServer(SERVER_NAME)
    if probe.waitForConnected(300):
        probe.disconnectFromServer()
        probe.close()
        # 残留进程：清理服务名，继续启动新实例（新实例会接管）
        QLocalServer.removeServer(SERVER_NAME)

    # 清理上次崩溃残留的服务名，然后监听
    QLocalServer.removeServer(SERVER_NAME)
    server = QLocalServer()
    server.listen(SERVER_NAME)

    app.setStyle("Fusion")
    app.setStyleSheet(GLOBAL_QSS)
    font = QFont("Microsoft YaHei", 9)
    app.setFont(font)
    window = PackEditor()
    window.show()

    # 收到后续实例的连接请求时，把窗口调到最前
    def _bring_to_front():
        client = server.nextPendingConnection()
        if client is not None:
            client.close()
        window.showNormal()
        window.raise_()
        window.activateWindow()

    server.newConnection.connect(_bring_to_front)

    sys.exit(app.exec_())


def open_editor(output_dir=None, on_pack_created=None):
    """集成模式：在已有 QApplication 中打开表情包编辑器窗口（桌宠内部调用）。
    不创建新 QApplication，不做单实例检测（桌宠自己管理窗口引用）。
    返回创建的 PackEditor 窗口实例，调用方需保持引用防止被 GC。
    """
    editor = PackEditor(output_dir=output_dir, on_pack_created=on_pack_created)
    # 集成模式：样式只作用于编辑器窗口，不污染桌宠全局样式
    editor.setStyleSheet(GLOBAL_QSS)
    editor.setFont(QFont("Microsoft YaHei", 9))
    editor.show()
    editor.raise_()
    editor.activateWindow()
    return editor


if __name__ == "__main__":
    main()
