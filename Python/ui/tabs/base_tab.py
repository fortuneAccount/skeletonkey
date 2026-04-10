"""
ui/tabs/base_tab.py

Base class for all tab widgets.

Provides set_status() which walks up the widget hierarchy to find the
MainWindow regardless of how many intermediate containers Qt inserts
(QTabWidget wraps tabs in a QStackedWidget, so self.parent() is never
the MainWindow directly).
"""
from PyQt6.QtWidgets import QWidget


class BaseTab(QWidget):
    def set_status(self, message: str):
        """
        Display *message* in the MainWindow status bar.
        Safe to call even before the window is fully shown.
        """
        widget = self.parent()
        while widget is not None:
            if hasattr(widget, "set_status") and widget is not self:
                widget.set_status(message)
                return
            widget = widget.parent() if hasattr(widget, "parent") else None
