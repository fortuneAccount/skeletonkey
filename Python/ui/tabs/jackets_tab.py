"""
ui/tabs/jackets_tab.py

Placeholder for the Jackets tab.
"""
from PyQt6.QtWidgets import QLabel, QVBoxLayout
from ui.tabs.base_tab import BaseTab

class JacketsTab(BaseTab):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Jackets Tab - Placeholder"))