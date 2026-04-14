"""
ui/tabs/settings_tab.py

Settings tab – global application configuration.

Systems and Emulators directory fields are combo-boxes that support
multiple paths.  The + button adds a new path (via folder browser),
the - button removes the currently selected entry.

Paths are stored in Settings.ini as pipe-delimited lists:
    systems_directory = /path/a|/path/b
    emulators_directory = /path/x|/path/y

The first entry in each combo is treated as the primary/active path.
"""
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout,
    QPushButton, QFileDialog, QGroupBox, QCheckBox,
    QComboBox, QSizePolicy, QLabel, QSlider, QLineEdit, QMessageBox
)
from PyQt6.QtWidgets import QPlainTextEdit, QProgressBar
import os

from core.config import global_config
from ui.tabs.base_tab import BaseTab
from utils.paths import app_home

def _home() -> Path:
    """Return the application root directory (parent of the Python folder)."""
    return Path(__file__).resolve().parent.parent.parent.parent

_SEP = "|"  # delimiter used in Settings.ini for multi-path values


def _load_paths(raw: str) -> list[str]:
    """Split a pipe-delimited path string, stripping blanks."""
    return [p.strip() for p in raw.split(_SEP) if p.strip()]


def _save_paths(paths: list[str]) -> str:
    return _SEP.join(paths)


class _PathCombo(QWidget):
    """
    A combo-box pre-loaded with directory paths plus + / - action buttons.

    Layout:  [combo▼]  [+]  [-]  [Browse…]
    """

    def __init__(self, label: str, parent=None):
        super().__init__(parent)
        self._label = label
        self._build()

    def _build(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._combo = QComboBox()
        self._combo.setEditable(True)
        self._combo.setSizePolicy(QSizePolicy.Policy.Expanding,
                                  QSizePolicy.Policy.Fixed)
        self._combo.setMinimumWidth(300)
        layout.addWidget(self._combo)

        add_btn = QPushButton("+")
        add_btn.setFixedWidth(28)
        add_btn.setToolTip(f"Add a {self._label} directory")
        add_btn.clicked.connect(self._add)
        layout.addWidget(add_btn)

        remove_btn = QPushButton("−")
        remove_btn.setFixedWidth(28)
        remove_btn.setToolTip(f"Remove selected {self._label} directory")
        remove_btn.clicked.connect(self._remove)
        layout.addWidget(remove_btn)

        browse_btn = QPushButton("Browse…")
        browse_btn.clicked.connect(self._browse)
        layout.addWidget(browse_btn)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_paths(self, paths: list[str]):
        self._combo.clear()
        for p in paths:
            self._combo.addItem(p)

    def paths(self) -> list[str]:
        """Return all entries in the combo as a list (preserving order)."""
        result = []
        for i in range(self._combo.count()):
            val = self._combo.itemText(i).strip()
            if val:
                result.append(val)
        # Also include whatever is currently typed in the editable field
        current = self._combo.currentText().strip()
        if current and current not in result:
            result.insert(0, current)
        return result

    def current_path(self) -> str:
        return self._combo.currentText().strip()

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------

    def _browse(self):
        start = self.current_path() or str(Path.home())
        chosen = QFileDialog.getExistingDirectory(
            self, f"Select {self._label} Directory", start)
        if chosen:
            self._add_path(chosen)

    def _add(self):
        """Trigger browse to select a directory to append."""
        self._browse()

    def _remove(self):
        idx = self._combo.currentIndex()
        if idx >= 0:
            self._combo.removeItem(idx)

    def _add_path(self, path: str):
        # Avoid duplicates
        for i in range(self._combo.count()):
            if self._combo.itemText(i) == path:
                self._combo.setCurrentIndex(i)
                return
        self._combo.insertItem(0, path)
        self._combo.setCurrentIndex(0)


class SettingsTab(BaseTab):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._cfg = global_config()
        self._build_ui()
        self._load_values()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self):
        root = QVBoxLayout(self)

        # Header Row: Reset functionality (mirrors SKRESDDL/SKRESET)
        header_row = QHBoxLayout()
        self._reset_ddl = QComboBox()
        self._reset_ddl.addItems(["All", "Session", "Jacket-Presets", "Retroarch", "Associations", "Core-Cfgs", "Playlist-DB"])
        self._reset_ddl.setFixedWidth(160)
        header_row.addWidget(self._reset_ddl)

        reset_btn = QPushButton("RESET")
        reset_btn.setFixedWidth(60)
        reset_btn.clicked.connect(self._on_reset_clicked)
        header_row.addWidget(reset_btn)
        header_row.addStretch()
        root.addLayout(header_row)

        dirs = QGroupBox("Directories")
        form = QFormLayout(dirs)
        form.setFieldGrowthPolicy(
            QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)

        self._systems_combo = _PathCombo("Systems / ROMs")
        form.addRow("Systems / ROMs:", self._systems_combo)

        self._emus_combo = _PathCombo("Emulators")
        form.addRow("Emulators:", self._emus_combo)

        self._exclude_systems_combo = _PathCombo("Exclude Systems")
        form.addRow("Exclude Systems:", self._exclude_systems_combo)

        self._exclude_emus_combo = _PathCombo("Exclude Emulators")
        form.addRow("Exclude Emulators:", self._exclude_emus_combo)

        self._cache_dir = QLineEdit()
        cache_row = QHBoxLayout()
        cache_row.addWidget(self._cache_dir)
        cache_browse = QPushButton("Browse…")
        cache_browse.clicked.connect(self._browse_cache)
        cache_row.addWidget(cache_browse)
        form.addRow("Cache / Temp:", cache_row)

        root.addWidget(dirs)

        # Activity Log Viewer
        self._log_group = QGroupBox("Activity Log")
        log_layout = QVBoxLayout(self._log_group)
        self._log_text = QPlainTextEdit()
        self._log_text.setReadOnly(True)
        self._log_text.setMaximumHeight(150)
        
        self._progress_bar = QProgressBar()
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setValue(0)
        self._progress_bar.setTextVisible(True)
        
        self._export_btn = QPushButton("Export Log")
        self._export_btn.setFixedWidth(100)
        self._export_btn.clicked.connect(self._export_log)

        log_layout.addWidget(self._progress_bar)
        log_layout.addWidget(self._log_text)
        log_layout.addWidget(self._export_btn, alignment=Qt.AlignmentFlag.AlignRight)
        root.addWidget(self._log_group)

        # Middle Row: Global Options and Appearance
        mid_row = QHBoxLayout()

        opts = QGroupBox("App Options")
        opts_layout = QVBoxLayout(opts)
        self._portable_chk = QCheckBox("Portable mode")
        self._portable_chk.toggled.connect(self._on_portable_toggled)
        self._always_on_top_chk = QCheckBox("Always On Top")
        self._logging_chk = QCheckBox("Enable Logging")
        self._auto_pgs_chk = QCheckBox("Auto-Load Per-Game Settings")

        opts_layout.addWidget(self._portable_chk)
        opts_layout.addWidget(self._always_on_top_chk)
        opts_layout.addWidget(self._logging_chk)
        opts_layout.addWidget(self._auto_pgs_chk)
        mid_row.addWidget(opts)

        appr = QGroupBox("Appearance")
        appr_layout = QVBoxLayout(appr)
        self._trans_slider = QSlider(Qt.Orientation.Horizontal)
        self._trans_slider.setRange(10, 255)
        appr_layout.addWidget(QLabel("Window Transparency:"))
        appr_layout.addWidget(self._trans_slider)
        self._dyn_trans_chk = QCheckBox("Dynamic Transparency")
        appr_layout.addWidget(self._dyn_trans_chk)
        mid_row.addWidget(appr)

        root.addLayout(mid_row)

        # Footer Row: Links and Save
        footer = QHBoxLayout()
        save_btn = QPushButton("Save Settings")
        save_btn.clicked.connect(self._save)
        footer.addWidget(save_btn)

        links = QVBoxLayout()
        help_lbl = QLabel('<a href="site/index.html">Help</a>')
        help_lbl.setOpenExternalLinks(True)
        donate_lbl = QLabel('<a href="https://www.paypal.me/romjacket/8.88">Donate</a>')
        donate_lbl.setOpenExternalLinks(True)
        links.addWidget(help_lbl)
        links.addWidget(donate_lbl)
        footer.addLayout(links)

        root.addLayout(footer)
        root.addStretch()

    # ------------------------------------------------------------------
    # Data
    # ------------------------------------------------------------------

    def append_log(self, message: str):
        """Thread-safe-ish append to the log viewer."""
        from datetime import datetime
        timestamp = datetime.now().strftime("%H:%M:%S")
        self._log_text.appendPlainText(f"[{timestamp}] {message}")

    def set_progress(self, value: int):
        """Update the activity progress bar."""
        self._progress_bar.setValue(value)

    def refresh_ui(self):
        """Public refresh method to reload values from config."""
        self._load_values()

    def _export_log(self):
        """Save the log content to a text file."""
        path, _ = QFileDialog.getSaveFileName(self, "Export Log", str(Path.home() / "skeletonkey_activity.log"), "Log Files (*.log);;Text Files (*.txt)")
        if path:
            Path(path).write_text(self._log_text.toPlainText(), encoding="utf-8")
            self.set_status(f"Log exported to {Path(path).name}")

    def _load_values(self):
        sys_raw = self._cfg.get("GLOBAL", "systems_directory", fallback="")
        self._systems_combo.set_paths(_load_paths(sys_raw))

        emu_raw = self._cfg.get("GLOBAL", "emulators_directory", fallback="")
        self._emus_combo.set_paths(_load_paths(emu_raw))

        sys_ex_raw = self._cfg.get("GLOBAL", "exclude_systems", fallback="")
        self._exclude_systems_combo.set_paths(_load_paths(sys_ex_raw))

        emu_ex_raw = self._cfg.get("GLOBAL", "exclude_emus", fallback="")
        self._exclude_emus_combo.set_paths(_load_paths(emu_ex_raw))

        self._cache_dir.setText(
            self._cfg.get("OPTIONS", "temp_location", fallback=""))

        # Load booleans
        self._portable_chk.setChecked((self._cfg.home / "portable.txt").exists())
        self._always_on_top_chk.setChecked(self._cfg.get("GLOBAL", "AlwaysOnTop", fallback="0") == "1")
        self._logging_chk.setChecked(self._cfg.get("GLOBAL", "Logging", fallback="1") == "1")
        self._auto_pgs_chk.setChecked(self._cfg.get("GLOBAL", "AutoLoad_PerGameSettings", fallback="1") == "1")
        self._dyn_trans_chk.setChecked(self._cfg.get("GLOBAL", "Dynamic_Transparency", fallback="0") == "1")
        self._trans_slider.setValue(int(self._cfg.get("GLOBAL", "Transparency", fallback="255")))

    def _on_reset_clicked(self):
        target = self._reset_ddl.currentText()
        ans = QMessageBox.question(self, "Confirm Reset", f"Are you sure you want to reset {target} settings?")
        if ans == QMessageBox.StandardButton.Yes:
            self.set_status(f"Resetting {target}...")
            home = app_home()

            if target == "All":
                # Mirrors RESET_ALL_QUIT: clear primary configs
                for ext in ["*.ini", "*.cfg"]:
                    for f in home.glob(ext):
                        f.unlink(missing_ok=True)
                self.set_status("All settings cleared. Restart recommended.")

            elif target == "Session":
                # Mirrors resetSYS: delete system index files
                (home / "sysabr.ini").unlink(missing_ok=True)
                (home / "sysint.ini").unlink(missing_ok=True)
                (home / "hacksyst.ini").unlink(missing_ok=True)
                self.set_status("Session cache cleared.")

            elif target == "Jacket-Presets":
                # Mirrors CLEAN_ROMJACKETS: remove jacket configs
                import shutil
                cfg_dir = home / "rj" / "sysCfgs"
                if cfg_dir.exists():
                    shutil.rmtree(cfg_dir)
                    cfg_dir.mkdir(parents=True)
                self.set_status("Jacket presets cleared.")

            elif target == "Associations":
                # Mirrors AsocInit: restore from source template if available
                import shutil
                src = home / "src" / "Assignments.set"
                if src.exists():
                    shutil.copy(src, home / "Assignments.ini")
                self.set_status("Associations restored to default.")

            elif target == "Playlist-DB":
                # Mirrors PlaylistInit
                (home / "hashdb.ini").unlink(missing_ok=True)
                self.set_status("Playlist database cleared.")

    def _on_portable_toggled(self, checked: bool):
        """Create or remove portable.txt with project path and state in root and AppData."""
        project_root = _home()
        appdata_dir = Path(os.environ.get("APPDATA", "")) / "skeletonkey"
        
        content = f"{project_root}\n{str(checked).lower()}"
        targets = [project_root / "portable.txt", appdata_dir / "portable.txt"]

        if checked:
            for p in targets:
                try:
                    p.parent.mkdir(parents=True, exist_ok=True)
                    p.write_text(content, encoding="utf-8")
                except Exception: pass
        else:
            for p in targets:
                if p.exists(): p.unlink()

    def _browse_cache(self):
        current = self._cache_dir.text() or str(Path.home())
        chosen = QFileDialog.getExistingDirectory(
            self, "Select Cache Directory", current)
        if chosen:
            self._cache_dir.setText(chosen)

    def _save(self):
        self._cfg.set("GLOBAL", "systems_directory",
                      _save_paths(self._systems_combo.paths()))
        self._cfg.set("GLOBAL", "emulators_directory",
                      _save_paths(self._emus_combo.paths()))
        self._cfg.set("GLOBAL", "exclude_systems", _save_paths(self._exclude_systems_combo.paths()))
        self._cfg.set("GLOBAL", "exclude_emus", _save_paths(self._exclude_emus_combo.paths()))
        self._cfg.set("OPTIONS", "temp_location", self._cache_dir.text())
        self._cfg.set("GLOBAL", "AlwaysOnTop", "1" if self._always_on_top_chk.isChecked() else "0")
        self._cfg.set("GLOBAL", "Logging", "1" if self._logging_chk.isChecked() else "0")
        self._cfg.set("GLOBAL", "AutoLoad_PerGameSettings", "1" if self._auto_pgs_chk.isChecked() else "0")
        self._cfg.set("GLOBAL", "Dynamic_Transparency", "1" if self._dyn_trans_chk.isChecked() else "0")
        self._cfg.set("GLOBAL", "Transparency", str(self._trans_slider.value()))
        self._cfg.save()
        self.set_status("Settings saved.")
        main_win = self.window()
        if hasattr(main_win, "refresh_all_tabs"):
            main_win.refresh_all_tabs()

    # ------------------------------------------------------------------
    # Public helpers (called by other tabs)
    # ------------------------------------------------------------------

    def primary_systems_dir(self) -> str:
        """Return the first (active) systems directory."""
        paths = _load_paths(
            self._cfg.get("GLOBAL", "systems_directory", fallback=""))
        return paths[0] if paths else ""

    def primary_emus_dir(self) -> str:
        """Return the first (active) emulators directory."""
        paths = _load_paths(
            self._cfg.get("GLOBAL", "emulators_directory", fallback=""))
        return paths[0] if paths else ""

    def all_systems_dirs(self) -> list[str]:
        return _load_paths(
            self._cfg.get("GLOBAL", "systems_directory", fallback=""))

    def all_emus_dirs(self) -> list[str]:
        return _load_paths(
            self._cfg.get("GLOBAL", "emulators_directory", fallback=""))
