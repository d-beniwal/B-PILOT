"""Left-edge minimize ribbon: collapsible panels tuck into a vertical tab
strip and restore on click.

Two call sites use this (see ``main_window.py`` / ``plan_runner.py``):
Plans list and Plan form both live inside a ``QSplitter``, so they minimize
via :class:`CollapsibleSplitterPanel`. The AutoPILOT chat panel is a
``QDockWidget``; :class:`CollapsibleDockPanel` repurposes its own title-bar
close (x) as the minimize trigger instead of adding a new button. Console
and the Plan queue are intentionally never minimizable (explicit choice).
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
        self.setToolTip(f"Restore {label}")
        fm = QtGui.QFontMetrics(self.font())
        self._text_len = fm.horizontalAdvance(label) + S.px(16)
        self.setFixedWidth(S.px(26))

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
        a hover/pressed fill on top, then the rotated label."""
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        rect = QtCore.QRectF(self.rect()).adjusted(1, 0.5, -1, -0.5)
        if self.isDown():
            painter.setBrush(QtGui.QColor(S.ACCENT))
        elif self.underMouse():
            painter.setBrush(QtGui.QColor(S.HOVER))
        else:
            painter.setBrush(QtGui.QColor(S.PANEL))
        painter.setPen(QtGui.QPen(QtGui.QColor(S.BORDER), 1))
        painter.drawRoundedRect(rect, S.px(3), S.px(3))
        painter.setPen(QtGui.QColor("white" if self.isDown() else S.TEXT))
        painter.translate(0, self.height())
        painter.rotate(-90)
        text_rect = QtCore.QRect(0, 0, self.height(), self.width())
        painter.drawText(text_rect, QtCore.Qt.AlignCenter, self.text())


class PanelRibbon(QtWidgets.QWidget):
    """Always-present vertical strip along the left edge holding tabs for
    minimized panels. Empty (just background) when nothing is minimized, so
    the rest of the layout doesn't shift when the first panel is tucked away.
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

    def add_tab(self, key: str, label: str, on_restore: Callable[[], None]) -> None:
        """Show a ribbon tab for `key`; no-op if one is already showing."""
        if key in self._tabs:
            return
        btn = VerticalTabButton(label, self)
        btn.clicked.connect(on_restore)
        self._layout.insertWidget(self._layout.count() - 1, btn)
        self._tabs[key] = btn

    def remove_tab(self, key: str) -> None:
        """Remove `key`'s ribbon tab, if present."""
        btn = self._tabs.pop(key, None)
        if btn is not None:
            self._layout.removeWidget(btn)
            btn.deleteLater()


class CollapsibleSplitterPanel:
    """Minimize/restore a `widget` living inside a `splitter`, tucking it
    into `ribbon` as a `label`-named tab while hidden.

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
    ) -> None:
        """Bind the panel to `splitter`/`ribbon`; nothing is hidden yet."""
        self._splitter = splitter
        self._widget = widget
        self._ribbon = ribbon
        self._key = key
        self._label = label
        self._saved_sizes: list[int] | None = None

    def minimize(self) -> None:
        """Hide the panel and add its ribbon tab."""
        if not self._widget.isVisible():
            return
        self._saved_sizes = self._splitter.sizes()
        self._widget.setVisible(False)
        self._ribbon.add_tab(self._key, self._label, self.restore)

    def restore(self) -> None:
        """Show the panel again, restoring its prior width, and drop the tab."""
        self._widget.setVisible(True)
        if self._saved_sizes is not None:
            self._splitter.setSizes(self._saved_sizes)
        self._ribbon.remove_tab(self._key)


class CollapsibleDockPanel:
    """Tuck a `QDockWidget` into the ribbon when its own title-bar close (x)
    is clicked; restore it from the ribbon tab.

    `visibilityChanged` also fires on construction and on float/dock
    transitions, and if the panel is re-shown through some other path (e.g.
    a menu checkbox) rather than the ribbon tab -- both are handled for
    free since `PanelRibbon.add_tab`/`remove_tab` are idempotent.
    """

    def __init__(
        self,
        dock: QtWidgets.QDockWidget,
        ribbon: PanelRibbon,
        key: str,
        label: str,
    ) -> None:
        """Wire `dock`'s visibility to ribbon tab add/remove."""
        self._dock = dock
        self._ribbon = ribbon
        self._key = key
        self._label = label
        dock.visibilityChanged.connect(self._on_visibility_changed)

    def _on_visibility_changed(self, visible: bool) -> None:
        if visible:
            self._ribbon.remove_tab(self._key)
        else:
            self._ribbon.add_tab(self._key, self._label, self.restore)

    def restore(self) -> None:
        """Re-show the dock; its own visibilityChanged drops the tab."""
        self._dock.setVisible(True)
        self._dock.raise_()

    def detach(self) -> None:
        """Disconnect before the dock is torn down (config disabled it),
        so its final hide-on-teardown doesn't spawn a tab for a widget
        that's about to be deleted."""
        try:
            self._dock.visibilityChanged.disconnect(self._on_visibility_changed)
        except TypeError:
            pass
        self._ribbon.remove_tab(self._key)
