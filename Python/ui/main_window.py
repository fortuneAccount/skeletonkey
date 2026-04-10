"""
ui/main_window.py

Main application window with tab bar.
Mirrors the multi-tab GUI from working.ahk.
"""
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import (
    QMainWindow, QTabWidget, QStatusBar, QLabel, QWidget,
)

from core.config import global_config
from ui.tabs.systems_tab import SystemsTab
from ui.tabs.main_tab import MainTab
from ui.tabs.emulators_tab import EmulatorsTab
from ui.tabs.settings_tab import SettingsTab
from ui.tabs.playlists_tab import PlaylistsTab
from ui.tabs.frontends_tab import FrontendsTab
from ui.tabs.utilities_tab import UtilitiesTab
from ui.tabs.artwork_tab import ArtworkTab
from ui.tabs.dat_repo_tab import DatRepoTab
from ui.tabs.jackets_tab import JacketsTab
from utils.paths import app_home, img_dir


class MainWindow(QMainWindow):
    """Top-level window containing all feature tabs."""

    def __init__(self):
        super().__init__()
        self._cfg = global_config()
        self._setup_window()
        self._build_ui()
        self._register_tabs()
        self.apply_settings()
        self._restore_geometry()

    # ------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------

    def _setup_window(self):
        self.setWindowTitle("skeletonKey")
        icon_path = img_dir() / "skeletonkey.ico"
        if not icon_path.exists():
            # fall back to any .ico in img/
            icons = list(img_dir().glob("*.ico"))
            if icons:
                icon_path = icons[0]
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))
        self.resize(1024, 768)

    def _build_ui(self):
        # Initialize status bar first so tabs can use it during init (mirrors AHK SB_SetText)
        self._status = QStatusBar()
        self.setStatusBar(self._status)
        # Mirrors "Helpy Helperton" default text from working.ahk
        self._status_label = QLabel("Helpy Helperton")
        self._status.addWidget(self._status_label)

        self._tabs_list = []

        self._tabs = QTabWidget()
        self._tabs.setTabPosition(QTabWidget.TabPosition.North)
        self.setCentralWidget(self._tabs)
        
        self._settings_tab = SettingsTab(self)
        self._main_tab = MainTab(self)
        self._systems_tab = SystemsTab(self)
        self._playlists_tab = PlaylistsTab(self)
        self._dat_repo_tab = DatRepoTab(self)
        self._jackets_tab = JacketsTab(self)
        self._frontends_tab = FrontendsTab(self)
        self._artwork_tab = ArtworkTab(self)
        self._utilities_tab = UtilitiesTab(self)
        
        self._tabs_list = [
            self._settings_tab, self._main_tab, self._systems_tab,
            self._playlists_tab, self._frontends_tab, self._artwork_tab,
            self._dat_repo_tab, self._jackets_tab, self._utilities_tab
        ]

    def _register_tabs(self):
        """Add instantiated tabs to the QTabWidget and set index references."""
        for tab in self._tabs_list:
            # Map class names to AHK labels
            title = tab.__class__.__name__.replace("Tab", "")
            if title == "Main": title = "MAIN"
            elif title == "Systems": title = "Emu:Sys"
            elif title == "DatRepo": title = "DAT:Repo"
            elif title == "Utilities": title = "Util"
            
            self._tabs.addTab(tab, title)

        # Match the hardcoded indices used in _restore_geometry
        self._settings_tab_index = 0
        self._main_tab_index = 1

    def refresh_all_tabs(self):
        """Trigger a UI refresh on all tabs to reflect data changes."""
        for tab in self._tabs_list:
            if hasattr(tab, "refresh_ui"):
                tab.refresh_ui()

    def _restore_geometry(self):
        w = int(self._cfg.get("GUI", "width", fallback="1024"))
        h = int(self._cfg.get("GUI", "height", fallback="768"))
        self.resize(w, h)

        # First-run: no settings saved yet → open on Settings tab
        is_first_run = not self._cfg.path.exists() or (
            not self._cfg.get("GLOBAL", "systems_directory")
            and not self._cfg.get("GLOBAL", "emulators_directory")
        )
        if is_first_run:
            self._tabs.setCurrentIndex(self._settings_tab_index)
        else:
            self._tabs.setCurrentIndex(self._main_tab_index)

    # ------------------------------------------------------------------
    # Public helpers used by child tabs
    # ------------------------------------------------------------------

    def set_status(self, message: str):
        """Update the status bar text (thread-safe via Qt signal queue)."""
        self._status_label.setText(message)

    def apply_settings(self):
        """Apply global settings like transparency and stay-on-top."""
        # Always on top (mirrors ALWOTP in AHK)
        aot = self._cfg.get("GLOBAL", "AlwaysOnTop", fallback="0") == "1"
        if aot:
            self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)
        else:
            self.setWindowFlags(self.windowFlags() & ~Qt.WindowType.WindowStaysOnTopHint)

        # Window Transparency (mirrors TRANSLID in AHK)
        alpha = int(self._cfg.get("GLOBAL", "Transparency", fallback="255"))
        self.setWindowOpacity(alpha / 255.0)

    # ------------------------------------------------------------------
    # Events
    # ------------------------------------------------------------------

    def closeEvent(self, event):
        # Persist window size
        self._cfg.set("GUI", "width", str(self.width()))
        self._cfg.set("GUI", "height", str(self.height()))
        self._cfg.save()
        super().closeEvent(event)
