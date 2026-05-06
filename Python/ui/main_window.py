"""
ui/main_window.py

Main application window with tab bar.
Mirrors the multi-tab GUI from working.ahk.
"""
from pathlib import Path

from PyQt6.QtCore import Qt, QCoreApplication, QTimer
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
from ui.tabs.jackets_tab import JacketsTab
from utils.paths import app_home, img_dir
from data.systems import SystemRegistry
from core.task_manager import TaskManager
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
        self._tasks = TaskManager()
        self._assignments = AssignmentRegistry()
        self._launch_params = LaunchParamsRegistry()
        self._pre_mini_size = None
        self._setup_window()

        # Initialize startup splash for every run to mask intensive UI construction
        from ui.widgets.startup_splash import StartupSplashScreen
        self._splash = StartupSplashScreen()
        self._splash.show()
        # Ensure splash is drawn before we hit the heavy construction bottleneck
        QCoreApplication.processEvents()
        
        self._tasks.task_finished.connect(self._on_startup_task_finished)

        self._build_ui()
        self._register_tabs()
        self.apply_settings()
        self._restore_geometry()

        # If no background detection is triggered within 500ms, assume UI-only init is done
        QTimer.singleShot(500, self._check_splash_status)

    def _check_splash_status(self):
        """Closes splash if no background detection tasks are currently active."""
        if self._splash and not self._tasks.is_running("system_detection"):
            self._on_startup_task_finished("startup_init")

    # ------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------

    def _check_first_run(self) -> bool:
        """Identify if settings are missing, indicating an initial run."""
        return not self._cfg.path.exists() or (
            not self._cfg.get("GLOBAL", "systems_directory")
            and not self._cfg.get("GLOBAL", "emulators_directory")
        )

    def _on_startup_task_finished(self, name: str):
        """Show main UI once the background system detection finishes."""
        if self._splash and name in ("system_detection", "startup_init"):
            self._splash.close()
            self._splash = None
            self.show()
            
            import logging
            logger = logging.getLogger(__name__)
            emu_idx = self._tab_map.get("EmulatorsTab")
            if emu_idx is not None:
                logger.info("Executing tab-command switch to 'Emulators'")
                self._tabs.setCurrentIndex(emu_idx)

    def show(self):
        """Override show to keep UI hidden during startup splash."""
        if self._splash:
            return
        super().show()

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
        self.resize(800, 600)

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
        self._main_tab = MainTab(self._systems, self._emus, self._assignments, self._launch_params, self._tasks, self)
        self._systems_tab = SystemsTab(self._systems, self._emus, self._assignments, self._launch_params, self._tasks, self)
        self._emulators_tab = EmulatorsTab(self._systems, self._emus, self._tasks, self)
        self._artwork_tab = ArtworkTab(self._systems, self._tasks, self)
        self._jackets_tab = JacketsTab(self._systems, self._tasks, self)

        self._tabs_list = [
            self._settings_tab, self._main_tab, self._systems_tab, self._jackets_tab,
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
            elif title == "Jackets": title = "Jackets"

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
        # Ensure global window settings (transparency, AOT) are reapplied
        self.apply_settings()
        # Trigger layout adjustment after content refresh
        if self.layout():
            self.layout().activate()
            QCoreApplication.processEvents()  # Allow UI to update

    def _restore_geometry(self):
        w = int(self._cfg.get("GUI", "width", fallback="800"))
        h = int(self._cfg.get("GUI", "height", fallback="600"))
        self.resize(w, h)

        # First-run: no settings saved yet → open on Settings tab
        if self._check_first_run():
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
        if enabled:
            self._pre_mini_size = self.size()

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
            self.setMinimumSize(800, 600)
            self._tabs.setMinimumSize(self._tabs.sizeHint())

        self.layout().activate()
        self.adjustSize()
        if enabled:
            self.resize(0, 0) # Force to smallest possible size based on constraints
        else:
            if self._pre_mini_size:
                self.resize(self._pre_mini_size)
            else:
                self.resize(800, 600)

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
