"""
ui/main_window.py

Main application window with tab bar.
Mirrors the multi-tab GUI from working.ahk.
"""
from pathlib import Path

from PyQt6.QtCore import Qt, QCoreApplication
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import (
    QSizePolicy, QMainWindow, QTabWidget, QStatusBar, QLabel, QWidget,
)

from core.config import global_config, setup_logging
from ui.tabs.systems_tab import SystemsTab
from ui.tabs.main_tab import MainTab
from ui.tabs.emulators_tab import EmulatorsTab
from ui.tabs.settings_tab import SettingsTab
from ui.tabs.artwork_tab import ArtworkTab
from utils.paths import app_home, img_dir
from data.systems import SystemRegistry
from data.emulators import EmuRegistry
from data.assignments import AssignmentRegistry
from data.launch_params import LaunchParamsRegistry


class MainWindow(QMainWindow):
    """Top-level window containing all feature tabs."""

    def __init__(self):
        super().__init__()
        self._cfg = global_config()
        setup_logging(self._cfg)
        # Create shared registries
        self._systems = SystemRegistry()
        self._emus = EmuRegistry()
        self._assignments = AssignmentRegistry()
        self._launch_params = LaunchParamsRegistry()
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
        self._main_tab = MainTab(self._systems, self._emus, self._assignments, self._launch_params, self)
        self._systems_tab = SystemsTab(self._systems, self._emus, self._assignments, self._launch_params, self)
        self._emulators_tab = EmulatorsTab(self._systems, self._emus, self)
        self._artwork_tab = ArtworkTab(self._systems, self)
        
        self._tabs_list = [
            self._settings_tab, self._main_tab, self._systems_tab,
            self._emulators_tab, self._artwork_tab
        ]

    def _register_tabs(self):
        """Add instantiated tabs to the QTabWidget and create a dynamic mapping."""
        self._tab_map = {}  # Maps tab class instance to tab index
        
        for tab in self._tabs_list:
            # Map class names to display labels
            title = tab.__class__.__name__.replace("Tab", "")
            if title == "Main": title = "MAIN"
            elif title == "Settings": title = "Settings"
            elif title == "Systems": title = "Systems"
            elif title == "Emulators": title = "Emulators"
            elif title == "Artwork": title = "Artwork"
            
            index = self._tabs.addTab(tab, title)
            self._tab_map[tab.__class__.__name__] = index
            
            # Also store instance references for backward compatibility
            if isinstance(tab, SettingsTab):
                self._settings_tab_index = index
            elif isinstance(tab, MainTab):
                self._main_tab_index = index

    def refresh_all_tabs(self):
        """Trigger a UI refresh on all tabs to reflect data changes."""
        for tab in self._tabs_list:
            if hasattr(tab, "refresh_ui"):
                tab.refresh_ui()
        # Trigger layout adjustment after content refresh
        if self.layout():
            self.layout().activate()
            QCoreApplication.processEvents()  # Allow UI to update

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
        self._cfg.reload() # Ensure latest settings are loaded
        aot = self._cfg.get("GLOBAL", "AlwaysOnTop", fallback="0") == "1"
        flags = self.windowFlags()
        if aot:
            flags |= Qt.WindowType.WindowStaysOnTopHint
        else:
            flags &= ~Qt.WindowType.WindowStaysOnTopHint
            
        if flags != self.windowFlags():
            self.setWindowFlags(flags)
            self.show()  # Required after changing window flags

        # Window Transparency (mirrors TRANSLID in AHK)
        alpha = int(self._cfg.get("GLOBAL", "Transparency", fallback="255"))
        self.setWindowOpacity(alpha / 255.0)
        self._full_opacity = alpha / 255.0 # Store full opacity for dynamic transparency

    def set_mini_mode(self, enabled: bool):
        """Toggle visibility of navigation tabs and allow window to shrink."""
        self._tabs.tabBar().setVisible(not enabled)
        
        # Relax constraints on other tabs to allow the window to shrink
        for i in range(self._tabs.count()):
            tab = self._tabs.widget(i)
            if tab != self._main_tab:
                if enabled:
                    tab.setMinimumSize(0, 0)
                    tab.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
                else:
                    tab.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)

        if enabled:
            self.setMinimumSize(0, 0)
            self._tabs.setMinimumSize(0, 0)
        else:
            self.setMinimumSize(1024, 768)
            self._tabs.setMinimumSize(self._tabs.sizeHint())

        self.layout().activate()
        self.adjustSize()
        if enabled:
            self.resize(0, 0) # Force to smallest possible size based on constraints
        else:
            self.resize(1024, 768)

    def focusInEvent(self, event):
        """Restore full opacity when window gains focus."""
        super().focusInEvent(event)
        # Ensure latest settings are loaded
        self._cfg.reload()
        if self._cfg.get("GLOBAL", "Dynamic_Transparency", fallback="0") == "1":
            self.setWindowOpacity(self._full_opacity)

    def focusOutEvent(self, event):
        """Reduce opacity when window loses focus, if dynamic transparency is enabled."""
        super().focusOutEvent(event)
        # Ensure latest settings are loaded
        self._cfg.reload()
        if self._cfg.get("GLOBAL", "Dynamic_Transparency", fallback="0") == "1":
            # Reduce to 50% of full opacity, or a minimum of 0.1
            reduced_opacity = max(0.1, self._full_opacity * 0.5)
            self.setWindowOpacity(reduced_opacity)



    # ------------------------------------------------------------------
    # Events
    # ------------------------------------------------------------------

    def closeEvent(self, event):
        # Persist window size
        self._cfg.set("GUI", "width", str(self.width()))
        self._cfg.set("GUI", "height", str(self.height()))
        self._cfg.save()
        super().closeEvent(event)
