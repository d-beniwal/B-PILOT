"""Left-edge ribbon: every collapsible/toggleable panel gets a permanent
vertical tab that stays in the strip whether the panel is shown or hidden,
highlighted while its panel is active (visible) and toggling it on click.

Call sites (see ``main_window.py`` / ``plan_runner.py``): Plans list and Plan
form both live inside a ``QSplitter``, so they toggle via
:class:`CollapsibleSplitterPanel`. The AutoPILOT chat panel is a
``QDockWidget``; :class:`CollapsibleDockPanel` repurposes its own title-bar
close (x) as an additional hide trigger, on top of the ribbon tab itself.
Console and the Plan queue are intentionally never minimizable (explicit
choice).
"""
from __future__ import annotations

from typing import Callable

from PyQt5 import QtCore
from PyQt5 import QtGui
from PyQt5 import QtWidgets

from . import style as S


class VerticalTabButton(QtWidgets.QAbstractButton):
    """A ribbon tab: `label` rotated -90° so it reads bottom-to-top."""

    def __init__(self, label: str, parent: QtWidgets.QWidget | None = None) -> None:
        """Store `label` and size the button to fit it once rotated."""
        super().__init__(parent)
        self.setText(label)
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self._active = False
        self._update_tooltip()
        fm = QtGui.QFontMetrics(self.font())
        self._text_len = fm.horizontalAdvance(label) + S.px(16)
        self.setFixedWidth(S.px(26))

    def set_active(self, active: bool) -> None:
        """Mark whether this tab's panel is currently shown; repaint + retip."""
        if active == self._active:
            return
        self._active = active
        self._update_tooltip()
        self.update()

    def _update_tooltip(self) -> None:
        self.setToolTip(f"Hide {self.text()}" if self._active else f"Show {self.text()}")

    def sizeHint(self) -> QtCore.QSize:  # noqa: N802
        """Fixed ribbon width; length follows the rotated label."""
        return QtCore.QSize(S.px(26), self._text_len)

    def enterEvent(self, event) -> None:  # noqa: N802
        """Repaint for hover -- custom-painted buttons don't do this for free."""
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        """Repaint to clear the hover highlight."""
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802
        """Paint a bordered chip (always, so tabs stay separable at rest),
        a hover/pressed fill on top, then the rotated label.

        An active tab (its panel currently shown) gets a translucent accent
        fill, a thicker accent border, and bold accent text -- distinct from
        the transient hover/pressed states, which still take priority since
        they reflect the click about to happen.
        """
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        rect = QtCore.QRectF(self.rect()).adjusted(1, 0.5, -1, -0.5)
        if self.isDown():
            painter.setBrush(QtGui.QColor(S.ACCENT))
        elif self.underMouse():
            painter.setBrush(QtGui.QColor(S.HOVER))
        elif self._active:
            active_fill = QtGui.QColor(S.ACCENT)
            active_fill.setAlpha(60)
            painter.setBrush(active_fill)
        else:
            painter.setBrush(QtGui.QColor(S.PANEL))
        border_color = S.ACCENT if self._active and not self.isDown() else S.BORDER
        painter.setPen(QtGui.QPen(QtGui.QColor(border_color), S.px(2) if self._active else 1))
        painter.drawRoundedRect(rect, S.px(3), S.px(3))
        if self.isDown():
            text_color = "white"
        elif self._active:
            text_color = S.ACCENT
        else:
            text_color = S.TEXT
        painter.setPen(QtGui.QColor(text_color))
        font = painter.font()
        font.setBold(self._active)
        painter.setFont(font)
        painter.translate(0, self.height())
        painter.rotate(-90)
        text_rect = QtCore.QRect(0, 0, self.height(), self.width())
        painter.drawText(text_rect, QtCore.Qt.AlignCenter, self.text())


class PanelRibbon(QtWidgets.QWidget):
    """Always-present vertical strip along the left edge holding one
    permanent tab per registered panel -- tabs never disappear, they just
    toggle between the active (panel shown) and inactive (panel hidden)
    look (see :meth:`VerticalTabButton.set_active`).
    """

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        """Build the empty ribbon strip."""
        super().__init__(parent)
        self.setFixedWidth(S.px(26))
        self._layout = QtWidgets.QVBoxLayout(self)
        self._layout.setContentsMargins(0, S.px(4), 0, 0)
        self._layout.setSpacing(S.px(2))
        self._layout.addStretch(1)
        self._tabs: dict[str, VerticalTabButton] = {}

    def register_tab(
        self, key: str, label: str, on_toggle: Callable[[], None]
    ) -> VerticalTabButton:
        """Create `key`'s permanent tab (or rewire an existing one).

        Idempotent: calling this again for the same `key` just reconnects
        `on_toggle` on the existing button rather than duplicating it, so
        call sites don't need to guard against re-registration themselves.
        """
        btn = self._tabs.get(key)
        if btn is None:
            btn = VerticalTabButton(label, self)
            self._layout.insertWidget(self._layout.count() - 1, btn)
            self._tabs[key] = btn
        else:
            try:
                btn.clicked.disconnect()
            except TypeError:
                pass
        btn.clicked.connect(on_toggle)
        return btn

    def set_active(self, key: str, active: bool) -> None:
        """Update `key`'s tab to reflect whether its panel is shown."""
        btn = self._tabs.get(key)
        if btn is not None:
            btn.set_active(active)


class CollapsibleSplitterPanel:
    """Minimize/restore a `widget` living inside a `splitter`, keeping a
    permanent `label`-named tab on `ribbon` that's active while shown.

    Hiding a `QSplitter` child collapses its allotted space (and its
    adjacent handle) automatically, so minimizing is just `setVisible`
    plus remembering the sizes to restore.
    """

    def __init__(
        self,
        splitter: QtWidgets.QSplitter,
        widget: QtWidgets.QWidget,
        ribbon: PanelRibbon,
        key: str,
        label: str,
        on_change: Callable[[], None] | None = None,
    ) -> None:
        """Bind the panel to `splitter`/`ribbon` and register its tab as
        active (the panel starts visible). `on_change`, if given, is called
        after every minimize/restore -- used by callers that need to react
        to this panel's visibility (e.g. resizing an unrelated splitter)."""
        self._splitter = splitter
        self._widget = widget
        self._ribbon = ribbon
        self._key = key
        self._label = label
        self._on_change = on_change
        self._saved_sizes: list[int] | None = None
        self._minimized = False
        ribbon.register_tab(key, label, self.toggle)
        ribbon.set_active(key, True)

    @property
    def is_minimized(self) -> bool:
        return self._minimized

    def toggle(self) -> None:
        """Flip between minimized and restored -- the ribbon tab's click."""
        self.restore() if self._minimized else self.minimize()

    def minimize(self) -> None:
        """Hide the panel and mark its ribbon tab inactive."""
        if not self._widget.isVisible():
            return
        self._saved_sizes = self._splitter.sizes()
        self._widget.setVisible(False)
        self._minimized = True
        self._ribbon.set_active(self._key, False)
        if self._on_change is not None:
            self._on_change()

    def restore(self) -> None:
        """Show the panel again, restoring its prior width, and mark active."""
        self._widget.setVisible(True)
        if self._saved_sizes is not None:
            self._splitter.setSizes(self._saved_sizes)
        self._minimized = False
        self._ribbon.set_active(self._key, True)
        if self._on_change is not None:
            self._on_change()


class CollapsibleDockPanel:
    """Keep a permanent ribbon tab for a `QDockWidget`, active while shown.

    The dock's own title-bar close (x) also hides it, same as clicking the
    ribbon tab -- both go through `visibilityChanged`, which also fires on
    construction and on float/dock transitions, and if the panel is shown
    through some other path (e.g. a menu checkbox), so the tab stays in
    sync regardless of how visibility changed.
    """

    def __init__(
        self,
        dock: QtWidgets.QDockWidget,
        ribbon: PanelRibbon,
        key: str,
        label: str,
    ) -> None:
        """Register `key`'s tab and wire it + the dock's own visibility together."""
        self._dock = dock
        self._ribbon = ribbon
        self._key = key
        self._label = label
        ribbon.register_tab(key, label, self.toggle)
        ribbon.set_active(key, dock.isVisible())
        dock.visibilityChanged.connect(self._on_visibility_changed)

    def _on_visibility_changed(self, visible: bool) -> None:
        self._ribbon.set_active(self._key, visible)

    def toggle(self) -> None:
        """Flip the dock's visibility -- the ribbon tab's click."""
        if self._dock.isVisible():
            self._dock.setVisible(False)
        else:
            self.restore()

    def restore(self) -> None:
        """Re-show the dock; its own visibilityChanged marks the tab active."""
        self._dock.setVisible(True)
        self._dock.raise_()
