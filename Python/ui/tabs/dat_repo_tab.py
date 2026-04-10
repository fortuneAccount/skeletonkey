"""
ui/tabs/dat_repo_tab.py

Placeholder for the DAT : Repo tab.
"""
from PyQt6.QtWidgets import QLabel, QVBoxLayout
from ui.tabs.base_tab import BaseTab

class DatRepoTab(BaseTab):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("DAT : Repo Tab - Placeholder"))