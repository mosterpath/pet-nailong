# -*- coding: utf-8 -*-
"""
奶娃桌宠 - 动画引擎
基于 QGraphicsItem 变换（scale/rotation/pos）模拟动画，
素材为静态 PNG + laugh.gif。窗口本身移动实现"走路"。

层级设计：
  scene → PetGroup（QGraphicsItemGroup，负责左右镜像 setScale(-1/1)）
            └→ PetSprite（QGraphicsObject，负责绘制 pixmap + 呼吸/蹦跳动画）
这样镜像翻转与呼吸缩放互不干扰（二者相乘）。
"""
import random

from PyQt5.QtCore import (QEasingCurve, QParallelAnimationGroup, QPoint,
                          QPropertyAnimation, QRectF, QSequentialAnimationGroup, Qt, QTimer)
from PyQt5.QtWidgets import QApplication, QGraphicsItemGroup, QGraphicsObject


class PetSprite(QGraphicsObject):
    """桌宠精灵图元：只负责绘制 pixmap，不处理鼠标事件（由容器 PetGroup 处理）。"""

    def __init__(self):
        super().__init__()
        self._pixmap = None
        self.setAcceptedMouseButtons(Qt.NoButton)

    def set_pixmap(self, pixmap):
        self._pixmap = pixmap
        self.prepareGeometryChange()
        self.update()

    def boundingRect(self):
        if self._pixmap is None:
            return self.childrenBoundingRect()
        return QRectF(self._pixmap.rect())

    def paint(self, painter, option, widget=None):
        if self._pixmap is not None:
            painter.drawPixmap(0, 0, self._pixmap)


class PetGroup(QGraphicsItemGroup):
    """容器层：负责左右镜像，并接收鼠标事件转发给窗口（QGraphicsScene
    会把事件交给组而非组内子项，所以事件处理必须放在这一层）。"""

    def __init__(self, window):
        super().__init__()
        self._window = window
        self.setAcceptedMouseButtons(Qt.LeftButton | Qt.RightButton)

    def paint(self, painter, option, widget=None):
        pass

    def boundingRect(self):
        return self.childrenBoundingRect()

    def face(self, left):
        """设置朝向：left=True 朝左（镜像）。"""
        self.setScale(-1.0 if left else 1.0)

    def mousePressEvent(self, event):
        event.accept()
        if event.button() == Qt.LeftButton:
            self._window._on_press(event)

    def mouseMoveEvent(self, event):
        event.accept()
        self._window._on_drag_move(event)

    def mouseReleaseEvent(self, event):
        event.accept()
        if event.button() == Qt.RightButton:
            self._window._on_right_click(event)
        else:
            self._window._on_drag_release(event)


def make_anim(target, prop, start, end, duration, easing=QEasingCurve.InOutCubic):
    anim = QPropertyAnimation(target, prop)
    anim.setStartValue(start)
    anim.setEndValue(end)
    anim.setDuration(duration)
    anim.setEasingCurve(easing)
    return anim


class IdleAnimator:
    """待机循环：呼吸（scale 微缩放）。"""

    def __init__(self, sprite):
        self.sprite = sprite
        self._anim = make_anim(sprite, b"scale", 1.0, 1.02, 1500, QEasingCurve.InOutSine)
        self._anim.setLoopCount(-1)
        self._anim.start()

    def stop(self):
        self._anim.stop()


class SwayAnimator:
    """摇摆：rotation 微转，单次播放。"""

    def __init__(self, sprite):
        self.sprite = sprite
        self._anim = QSequentialAnimationGroup(sprite)
        self._anim.addAnimation(make_anim(sprite, b"rotation", 0.0, 2.5, 250, QEasingCurve.OutQuad))
        self._anim.addAnimation(make_anim(sprite, b"rotation", 2.5, -2.5, 500, QEasingCurve.InOutSine))
        self._anim.addAnimation(make_anim(sprite, b"rotation", -2.5, 0.0, 250, QEasingCurve.InQuad))
        self._anim.start()


class BounceAnimator:
    """点击蹦跳：先「弹起」（scale+y 并行），再「落下」（scale+y 并行），串行播放。"""

    def __init__(self, sprite):
        self.sprite = sprite
        y0 = sprite.y()
        self._group = QSequentialAnimationGroup(sprite)
        up = QParallelAnimationGroup(sprite)
        up.addAnimation(make_anim(sprite, b"scale", 1.0, 1.18, 110, QEasingCurve.OutQuad))
        up.addAnimation(make_anim(sprite, b"y", y0, y0 - 14, 110, QEasingCurve.OutQuad))
        down = QParallelAnimationGroup(sprite)
        down.addAnimation(make_anim(sprite, b"scale", 1.18, 1.0, 180, QEasingCurve.InQuad))
        down.addAnimation(make_anim(sprite, b"y", y0 - 14, y0, 180, QEasingCurve.InQuad))
        self._group.addAnimation(up)
        self._group.addAnimation(down)
        self._group.start()


class ShakeAnimator:
    """出错抖动：窗口左右快速抖动。"""

    def __init__(self, widget):
        self.widget = widget
        self._anim = QSequentialAnimationGroup(widget)
        origin = widget.pos()
        deltas = [6, -6, 5, -5, 3, -3, 0]
        start = origin
        for d in deltas:
            end = origin + QPoint(d, 0)
            # 每段起点接上一段终点，避免跳回原点导致抖动不连贯
            self._anim.addAnimation(make_anim(widget, b"pos", start, end, 38, QEasingCurve.Linear))
            start = end
        self._anim.start()


class WalkController:
    """走路：整个窗口沿屏幕底部随机漫步，精灵按方向镜像。"""

    def __init__(self, window, group, margin=20):
        self.window = window
        self.group = group
        self.margin = margin
        self._anim = None
        self._timer = QTimer(window)
        self._timer.timeout.connect(self._start_walk)

    def start(self, interval_ms=5500):
        self._timer.start(interval_ms)

    def stop(self):
        self._timer.stop()
        if self._anim:
            self._anim.stop()

    def _start_walk(self):
        if self._anim and self._anim.state() == QPropertyAnimation.Running:
            return
        screen_obj = self.window.screen() or QApplication.primaryScreen()
        if screen_obj is None:
            return
        screen = screen_obj.availableGeometry()
        win_w = self.window.width()
        x0 = self.window.x()
        x1 = x0
        tries = 0
        while abs(x1 - x0) < 90 and tries < 6:
            lo = screen.left() + self.margin
            hi = max(lo + 1, screen.width() - win_w - self.margin)
            x1 = lo + random.randrange(hi - lo + 1)
            tries += 1
        self.group.face(x1 < x0)
        self._anim = make_anim(self.window, b"pos", self.window.pos(),
                               QPoint(x1, self.window.y()), 2400, QEasingCurve.InOutQuad)
        self._anim.finished.connect(self._on_arrived)
        self._anim.start()

    def _on_arrived(self):
        self._anim = None
        self.group.face(False)
