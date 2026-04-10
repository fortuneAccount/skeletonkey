"""
ui/tabs/systems_tab.py

Systems tab – browse systems, set ROM directories, assign emulators,
and launch ROMs.
"""
from pathlib import Path

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QListWidget, QListWidgetItem, QLabel, QLineEdit,
    QPushButton, QFileDialog, QComboBox, QGroupBox,
    QFormLayout, QMessageBox, QPlainTextEdit,
)

from core.config import global_config
from core.launcher import Launcher, LaunchConfig, suspend_frontends, resume_frontends
from data.systems import SystemRegistry
from data.assignments import AssignmentRegistry
from data.launch_params import LaunchParamsRegistry
from ui.tabs.settings_tab import _PathCombo, _load_paths, _save_paths
from data.emulators import EmuRegistry
from ui.tabs.base_tab import BaseTab
from utils.paths import app_home


class _LaunchThread(QThread):
    """Run a ROM launch in a background thread so the UI stays responsive."""
    finished = pyqtSignal(int)

    def __init__(self, launcher: Launcher):
        super().__init__()
        self._launcher = launcher

    def run(self):
        suspend_frontends()
        code = self._launcher.launch()
        resume_frontends()
        self.finished.emit(code)


class SystemsTab(BaseTab):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._cfg = global_config()
        self._systems = SystemRegistry()
        self._assignments = AssignmentRegistry()
        self._launch_params = LaunchParamsRegistry()
        self._emus = EmuRegistry()
        self._launch_thread: _LaunchThread | None = None
        self._build_ui()
        self._populate_systems()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self):
        root = QHBoxLayout(self)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        root.addWidget(splitter)

        # Left panel: system list
        left = QWidget()
        left_layout = QVBoxLayout(left)

        # Category selector (mirrors SaList in AHK)
        self._category_ddl = QComboBox()
        self._category_ddl.addItems(["Systems", "Emulators", "Frontends", "Utilities"])
        self._category_ddl.currentTextChanged.connect(self._on_category_changed)
        left_layout.addWidget(self._category_ddl)

        self._item_list = QListWidget()
        self._item_list.currentTextChanged.connect(self._on_item_selected)
        left_layout.addWidget(self._item_list)
        splitter.addWidget(left)

        # Right panel: system details + ROM browser
        right = QWidget()
        right_layout = QVBoxLayout(right)

        self._info_group = QGroupBox("Configuration")
        self._form = QFormLayout(self._info_group)

        self._name_label = QLabel("")
        self._form.addRow("Name:", self._name_label)

        self._rom_path_combo = _PathCombo("ROMs")
        self._form.addRow("ROM Path:", self._rom_path_combo)

        self._exe_path_edit = QLineEdit()
        self._exe_row_widget = QWidget()
        exe_layout = QHBoxLayout(self._exe_row_widget)
        exe_layout.setContentsMargins(0, 0, 0, 0)
        exe_layout.addWidget(self._exe_path_edit)
        exe_browse = QPushButton("Browse…")
        exe_browse.clicked.connect(self._browse_exe)
        exe_layout.addWidget(exe_browse)
        self._form.addRow("Executable:", self._exe_row_widget)

        self._config_path_combo = _PathCombo("Configs")
        self._form.addRow("Config Paths:", self._config_path_combo)

        self._ext_edit = QLineEdit()
        self._ext_edit.setPlaceholderText("e.g. zip, sfc, smc")
        self._form.addRow("Extensions:", self._ext_edit)

        self._req_files_edit = QPlainTextEdit()
        self._req_files_edit.setMaximumHeight(60)
        self._form.addRow("Required Files:", self._req_files_edit)

        self._emu_combo = QComboBox()
        self._emu_combo.setEditable(True)
        self._form.addRow("Emulator:", self._emu_combo)

        save_btn = QPushButton("Save Assignment")
        save_btn.clicked.connect(self._save_assignment)

        # Detect buttons row (mirrors SYSDETECT / EMUDETECT in AHK)
        detect_row = QHBoxLayout()
        detect_sys_btn = QPushButton("Detect Systems")
        detect_sys_btn.clicked.connect(self._on_detect_systems_clicked)
        detect_emu_btn = QPushButton("Detect Emulators")
        detect_emu_btn.clicked.connect(self._on_detect_emus_clicked)
        detect_row.addWidget(detect_sys_btn)
        detect_row.addWidget(detect_emu_btn)

        self._form.addRow("", save_btn)
        self._form.addRow("", detect_row)

        right_layout.addWidget(self._info_group)

        # ROM list
        self._rom_label = QLabel("ROMs")
        right_layout.addWidget(self._rom_label)
        self._rom_list = QListWidget()
        self._rom_list.itemDoubleClicked.connect(self._launch_rom)
        right_layout.addWidget(self._rom_list)

        self._launch_btn = QPushButton("Launch Selected ROM")
        self._launch_btn.clicked.connect(self._launch_selected)
        right_layout.addWidget(self._launch_btn)

        splitter.addWidget(right)
        splitter.setSizes([250, 750])
        self._update_ui_visibility()

    # ------------------------------------------------------------------
    # Data population
    # ------------------------------------------------------------------

    def refresh_ui(self):
        """Sync UI lists with data registries."""
        self._populate_list()

    def _populate_systems(self):
        self._populate_list()
        self._emu_combo.clear()
        self._emu_combo.addItem("")
        for entry in self._emus.emulators():
            self._emu_combo.addItem(entry.name)

    def _populate_list(self):
        category = self._category_ddl.currentText()
        self._item_list.clear()
        if category == "Systems":
            items = self._systems.all_systems()
        elif category == "Emulators":
            items = [e.name for e in self._emus.emulators()]
        elif category == "Frontends":
            items = [e.name for e in self._emus.frontends()]
        elif category == "Utilities":
            items = [e.name for e in self._emus.utilities()]
        else:
            items = []
        for name in items:
            self._item_list.addItem(name)

    def _update_ui_visibility(self):
        category = self._category_ddl.currentText()
        is_system = category == "Systems"
        
        self._rom_path_combo.setVisible(is_system)
        self._emu_combo.setVisible(is_system)
        self._exe_row_widget.setVisible(not is_system)
        self._config_path_combo.setVisible(not is_system)
        self._ext_edit.setVisible(not is_system)
        self._req_files_edit.setVisible(not is_system)
        self._rom_label.setVisible(is_system)
        self._rom_list.setVisible(is_system)
        self._launch_btn.setVisible(is_system)
        
        for i in range(self._form.rowCount()):
            label_item = self._form.itemAt(i, QFormLayout.ItemRole.LabelRole)
            if label_item:
                label = label_item.widget()
                if label.text() == "ROM Path:" or label.text() == "Emulator:":
                    label.setVisible(is_system)
                elif label.text() == "Executable:" or label.text() == "Config Paths:":
                    label.setVisible(not is_system)
                elif label.text() in ("Extensions:", "Required Files:"):
                    label.setVisible(not is_system)

    def _on_category_changed(self, category: str):
        self._populate_list()
        self._update_ui_visibility()
        self._name_label.setText("")

    def _on_item_selected(self, name: str):
        if not name:
            return
        self._name_label.setText(name)
        category = self._category_ddl.currentText()
        
        # Reset fields
        self._ext_edit.clear()
        self._req_files_edit.setPlainText("")

        if category == "Systems":
            rom_path_str = self._systems.get_path(name)
            paths = _load_paths(rom_path_str)
            self._rom_path_combo.set_paths(paths)
            
            assigned = self._assignments.get_assignment(name)
            idx = self._emu_combo.findText(assigned)
            
            # Color the assigned emulator selection if valid
            if idx >= 0: 
                self._emu_combo.setCurrentIndex(idx)
            else:
                self._emu_combo.setCurrentText(assigned)
            self._populate_roms(self._rom_path_combo.current_path())
        else:
            entry = self._emus.get(name)
            if entry:
                from core.config import Config
                apps = Config(Config.APPS_FILE)
                sec = "EMULATORS" if category == "Emulators" else "FRONTENDS" if category == "Frontends" else "UTILITIES"
                path = apps.get(sec, name, fallback="")
                if not path and category == "Utilities":
                    path = apps.get("KEYMAPPERS", name, fallback="")
                
                self._exe_path_edit.setText(path)
                self._config_path_combo.set_paths(entry.configs)
                self._ext_edit.setText(", ".join(entry.extensions))
                self._req_files_edit.setPlainText("\n".join(entry.required_files))

    def _populate_roms(self, rom_dir: str):
        self._rom_list.clear()
        if not rom_dir:
            return
        p = Path(rom_dir)
        if not p.exists():
            return
        for f in sorted(p.iterdir()):
            if f.is_file():
                self._rom_list.addItem(f.name)

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _browse_exe(self):
        current = self._exe_path_edit.text() or str(Path.home())
        chosen, _ = QFileDialog.getOpenFileName(
            self, "Select Executable", current, "Executables (*.exe);;All Files (*)"
        )
        if chosen:
            self._exe_path_edit.setText(chosen)

    def _on_detect_systems_clicked(self):
        """Trigger fuzzy system detection from the primary systems directory."""
        root = self._cfg.get("GLOBAL", "systems_directory", fallback="").split('|')[0]
        if not root or not Path(root).exists():
            QMessageBox.warning(self, "Path Error", "Primary Systems Directory not found. Set it in Settings.")
            return
        
        self._systems.detect_systems(Path(root))
        
        main_win = self.window()
        if hasattr(main_win, "refresh_all_tabs"):
            main_win.refresh_all_tabs()

        self.set_status("System detection complete.")

    def _on_detect_emus_clicked(self):
        """Scan emulator directories for known executables."""
        root = self._cfg.get("GLOBAL", "emulators_directory", fallback="").split('|')[0]
        if not root or not Path(root).exists():
            QMessageBox.warning(self, "Path Error", "Primary Emulators Directory not found.")
            return

        self.set_status("Scanning for emulators...")
        # Re-use the existing EmuRegistry logic to match found files
        found_count = 0
        for p in Path(root).rglob("*.exe"):
            # Check if this filename is a known exe in our registry
            for entry in self._emus.by_category("emulator"):
                if entry.exe.lower() == p.name.lower():
                    from core.config import Config
                    apps = Config(Config.APPS_FILE)
                    apps.set("EMULATORS", entry.name, f'"{p}"')
                    apps.save()
                    found_count += 1
                    break

        main_win = self.window()
        if hasattr(main_win, "refresh_all_tabs"):
            main_win.refresh_all_tabs()
        self.set_status(f"Detection complete. Found {found_count} known emulators.")

    def _edit_system_path(self):
        system = self._item_list.currentItem()
        if not system:
            return

    def _save_assignment(self): # This method is called when the "Save Assignment" button is clicked.
        item = self._item_list.currentItem()
        if not item:
            return
        name = item.text()
        category = self._category_ddl.currentText()
        
        if category == "Systems":
            emu = self._emu_combo.currentText().strip()
            self._assignments.set_override(name, emu)
            self._assignments.save()
            self._systems.set_path(name, _save_paths(self._rom_path_combo.paths()))
            self._systems.save()
            self.set_status(f"Saved System: {name} → {emu}")
        else:
            from core.config import Config
            apps = Config(Config.APPS_FILE)
            sec = "EMULATORS" if category == "Emulators" else "FRONTENDS" if category == "Frontends" else "UTILITIES"
            path = self._exe_path_edit.text().strip()
            apps.set(sec, name, f'"{path}"')
            apps.save()
            cfg_presets = Config(Config.EMUCFG_FILE)
            
            # Save expanded metadata
            exts = [x.strip() for x in self._ext_edit.text().split(",") if x.strip()]
            reqs = [x.strip() for x in self._req_files_edit.toPlainText().splitlines() if x.strip()]
            cfg_presets.set(name, "extensions", "|".join(exts))
            cfg_presets.set(name, "required_files", "|".join(reqs))
            
            cfg_presets.set(name, "configs", _save_paths(self._config_path_combo.paths()))
            cfg_presets.save()
            self.set_status(f"Saved {category[:-1]}: {name}")
        
        main_win = self.window()
        if hasattr(main_win, "refresh_all_tabs"):
            main_win.refresh_all_tabs()

    def _launch_selected(self):
        item = self._rom_list.currentItem()
        if item:
            self._launch_rom(item)

    def _launch_rom(self, item: QListWidgetItem):
        system = self._item_list.currentItem()
        if not system:
            return
        system_name = system.text()
        rom_dir = self._systems.get_path(system_name)
        rom_path = str(Path(rom_dir) / item.text())

        emu_name = self._assignments.get_assignment(system_name)
        emu_entry = self._emus.get(emu_name)
        if not emu_entry or not emu_entry.exe:
            QMessageBox.warning(self, "No Emulator",
                                f"No emulator configured for {system_name}.")
            return

        emu_dir = app_home() / "Emulators" / emu_entry.name
        emu_exe = str(emu_dir / emu_entry.exe)

        lp = self._launch_params.get(system_name)
        cfg = LaunchConfig(
            emulator_path=emu_exe,
            rom_path=rom_path,
            include_extension=lp.extract,
            include_path=lp.runrom,
            working_dir=str(emu_dir),
        )
        launcher = Launcher(cfg)
        self._launch_thread = _LaunchThread(launcher)
        self._launch_thread.finished.connect(self._on_launch_finished)
        self._launch_thread.start()
        self.set_status(f"Launching {item.text()}…")

    def _on_launch_finished(self, exit_code: int):
        self.set_status(f"Emulator exited (code {exit_code})")
