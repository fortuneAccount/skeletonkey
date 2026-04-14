"""
ui/tabs/systems_tab.py

Systems tab – browse systems, set ROM directories, assign emulators,
and launch ROMs.
"""
from pathlib import Path

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QBrush
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
        
        # Delay first-run logic slightly to ensure main window references are settled
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(100, self._handle_first_run)

    def _handle_first_run(self):
        """Execute full environment discovery on first launch."""
        if self._cfg.get("GLOBAL", "first_run", fallback="1") != "1":
            return

        main_win = self.window()
        if hasattr(main_win, "_tab_widget") and hasattr(main_win, "_settings_tab"):
            main_win._tab_widget.setCurrentWidget(main_win._settings_tab)

        self.log("First run detected. Initiating comprehensive environment discovery...")
        self.set_progress(5)
        
        # 1. Multi-stage System detection
        self._on_detect_systems_clicked()
        self.set_progress(40)

        # 2. Sequential Category detection
        cats = ["Emulators", "Frontends", "Utilities"]
        for i, cat in enumerate(cats):
            self.log(f"Scanning for {cat}...")
            self._category_ddl.setCurrentText(cat)
            self._on_detect_emus_clicked()
            self.set_progress(40 + (i + 1) * 20)

        self._cfg.set("GLOBAL", "first_run", "0")
        self._cfg.save()
        self.log("Initial setup and discovery complete.")
        self.set_progress(100)

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

        # Top Detection Group
        self._detect_grp = QGroupBox("System Management")
        dg_layout = QHBoxLayout(self._detect_grp)
        self._detect_sys_btn = QPushButton("Detect Systems")
        self._detect_sys_btn.clicked.connect(self._on_detect_clicked)
        
        self._search_combo = QComboBox()
        self._search_combo.setEditable(True)
        self._search_combo.setMinimumWidth(150)
        self._search_combo.setPlaceholderText("Search...")
        self._search_combo.editTextChanged.connect(self._on_search_text_changed)
        self._search_combo.currentIndexChanged.connect(self._on_search_index_changed)

        self._clear_search_btn = QPushButton("x")
        self._clear_search_btn.setFixedWidth(24)
        self._clear_search_btn.clicked.connect(self._clear_search)

        self._filter_detected_btn = QPushButton("Y")
        self._filter_detected_btn.setFixedWidth(24)
        self._filter_detected_btn.setCheckable(True)
        self._filter_detected_btn.clicked.connect(self._on_filter_detected_toggled)
        
        dg_layout.addWidget(self._detect_sys_btn)
        dg_layout.addWidget(self._search_combo)
        dg_layout.addWidget(self._clear_search_btn)
        dg_layout.addWidget(self._filter_detected_btn)
        left_layout.addWidget(self._detect_grp)

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

        # Rename row
        self._rename_row = QHBoxLayout()
        self._rename_edit = QLineEdit()
        self._rename_edit.setPlaceholderText("New Alias...")
        self._rename_btn = QPushButton("Rename")
        self._rename_btn.clicked.connect(self._on_rename_clicked)
        self._rename_row.addWidget(self._rename_edit)
        self._rename_row.addWidget(self._rename_btn)
        self._form.addRow("Alias:", self._rename_row)

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

        # Action Buttons Row
        action_row = QHBoxLayout()
        self._select_btn = QPushButton("Select")
        self._assign_btn = QPushButton("Assign")
        self._assign_btn.clicked.connect(self._save_assignment)
        self._clear_emu_btn = QPushButton("Clear")
        self._clear_emu_btn.clicked.connect(self._on_clear_emu_clicked)
        self._delete_emu_btn = QPushButton("Delete")
        self._delete_emu_btn.clicked.connect(self._on_delete_emu_clicked)
        
        action_row.addWidget(self._select_btn)
        action_row.addWidget(self._assign_btn)
        action_row.addWidget(self._clear_emu_btn)
        action_row.addWidget(self._delete_emu_btn)
        self._form.addRow("", action_row)

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

    def log(self, message: str):
        """Route messages to the status bar and the SettingsTab log viewer."""
        self.set_status(message)
        main_win = self.window()
        # Find the SettingsTab instance to append to its log
        if hasattr(main_win, "_settings_tab"):
            main_win._settings_tab.append_log(message)

    def set_progress(self, value: int):
        """Update progress bar in Settings tab."""
        main_win = self.window()
        if hasattr(main_win, "_settings_tab"):
            main_win._settings_tab.set_progress(value)

    def refresh_ui(self):
        """Sync UI lists with data registries."""
        self._populate_list()

    def _update_active_domain(self, category: str):
        self._search_combo.clear()
        items = []
        if category == "Systems":
            items = self._systems.all_systems()
        elif category == "Emulators":
            items = [e.name for e in self._emus.emulators()]
        elif category == "Frontends":
            items = [e.name for e in self._emus.frontends()]
        elif category == "Utilities":
            items = [e.name for e in self._emus.utilities()]
            
        self._search_combo.addItems(items)
        
        # Apply status-indicating font-color-coding: gray out if NOT currently assigned/installed
        for i in range(self._search_combo.count()):
            name = self._search_combo.itemText(i)
            # Matches logic used in _populate_list
            is_installed = self._assignments.get_assignment(name) if category == "Systems" else self._emus.get(name).exe
            if not is_installed:
                self._search_combo.setItemData(i, QBrush(Qt.GlobalColor.gray), Qt.ItemDataRole.ForegroundRole)

    def _populate_systems(self):
        self._populate_list()
        self._emu_combo.clear()
        self._emu_combo.addItem("")
        for entry in self._emus.emulators():
            self._emu_combo.addItem(entry.name)

    def _populate_list(self):
        category = self._category_ddl.currentText()
        self._update_active_domain(category)
        self._item_list.clear()
        if category == "Systems":
            items = self._systems.all_systems()
            # Clean up stray "E" or single char entries that may have been parsed incorrectly
            items = [i for i in items if len(i) > 1]
        elif category == "Emulators":
            items = [e.name for e in self._emus.emulators()]
        elif category == "Frontends":
            items = [e.name for e in self._emus.frontends()]
        elif category == "Utilities":
            items = [e.name for e in self._emus.utilities()]
        else:
            items = []
            
        # Inverted color logic: grey out if NOT currently assigned/installed
        for name in items:
            item = QListWidgetItem(name)
            is_installed = self._assignments.get_assignment(name) if category == "Systems" else self._emus.get(name).exe
            if not is_installed:
                item.setForeground(QBrush(Qt.GlobalColor.gray))
            self._item_list.addItem(item)

    def _update_field_styling(self):
        """Apply Green (active) or Yellow (pending/missing) backgrounds to assignment fields."""
        def apply_style(widget, path_str):
            if not path_str:
                widget.setStyleSheet("")
                return
            exists = any(Path(p.strip()).exists() for p in path_str.split('|') if p.strip())
            if exists:
                widget.setStyleSheet("background-color: #28a745; color: white; font-weight: bold;")
            else:
                widget.setStyleSheet("background-color: #ffc107; color: black; font-weight: bold;")

        apply_style(self._exe_path_edit, self._exe_path_edit.text())
        apply_style(self._emu_combo, self._emu_combo.currentText())

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
        self._detect_grp.setTitle(f"{category} Management")
        self._detect_sys_btn.setText(f"Detect {category}")
        self._update_ui_visibility()
        self._name_label.setText("")

    def _on_search_text_changed(self, text: str):
        for i in range(self._item_list.count()):
            item = self._item_list.item(i)
            item.setHidden(text.lower() not in item.text().lower())

    def _on_search_index_changed(self, index: int):
        if index < 0: return
        text = self._search_combo.itemText(index)
        found = self._item_list.findItems(text, Qt.MatchFlag.MatchExactly)
        if found:
            self._item_list.setCurrentItem(found[0])

    def _clear_search(self):
        self._search_combo.setCurrentText("")
        self._on_search_text_changed("")

    def _on_filter_detected_toggled(self, checked: bool):
        cat = self._category_ddl.currentText()
        for i in range(self._item_list.count()):
            item = self._item_list.item(i)
            name = item.text()
            is_installed = self._assignments.get_assignment(name) if cat == "Systems" else self._emus.get(name).exe
            if checked:
                item.setHidden(not is_installed)
            else:
                search_text = self._search_combo.currentText().lower()
                item.setHidden(search_text not in name.lower())

    def _on_detect_clicked(self):
        if self._category_ddl.currentText() == "Systems":
            self._on_detect_systems_clicked()
        else:
            self._on_detect_emus_clicked()

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
            if idx >= 0: 
                self._emu_combo.setCurrentIndex(idx)
            else:
                self._emu_combo.setCurrentText(assigned)

            self._populate_roms(self._rom_path_combo.current_path())
            self._update_field_styling()
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
                self._update_field_styling()

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
        """Perform multi-stage system detection."""
        self.log("Indexing drives for primary ROM/Emu roots...")
        self.set_progress(10)
        
        # 1. Discover primary directories on drive roots
        found_sys_roots, found_emu_roots = self._systems.discover_primary_dirs()
        for r in found_sys_roots + found_emu_roots:
            self.log(f"Discovered primary root: {r}")

        # Refresh Settings Tab to show the newly discovered roots
        main_win = self.window()
        if hasattr(main_win, "_settings_tab"):
            main_win._settings_tab.refresh_ui()
        self.set_progress(20)

        # 2. Scan within established dirs and exact match drive roots
        self.log("Matching identified folders against known systems...")
        self._systems.detect_systems() 
        self.set_progress(30)
        found_exact = self._systems.exact_match_scan("Systems")
        for item in found_exact:
            self.log(f"Identified system: {item}")

        self._systems.save()
        self.log("System detection complete.")

        main_win = self.window()
        if hasattr(main_win, "refresh_all_tabs"):
            main_win.refresh_all_tabs()

    def _on_rename_clicked(self):
        """Handle system renaming and alias assignment."""
        item = self._item_list.currentItem()
        if not item:
            return
        old_name = item.text()
        new_name = self._rename_edit.text().strip()
        if not new_name or new_name == old_name:
            return

        # Update System Registry
        path = self._systems.get_path(old_name)
        self._systems.set_path(new_name, path)
        self._systems.save()
        
        # Update Assignments/Overrides
        assigned_emu = self._assignments.get_assignment(old_name)
        if assigned_emu:
            self._assignments.set_override(new_name, assigned_emu)
            self._assignments.save()

        self.refresh_ui()
        self.set_status(f"System renamed: {old_name} -> {new_name}")

    def _on_clear_emu_clicked(self):
        """Clear the emulator assignment for the selected item."""
        item = self._item_list.currentItem()
        if not item:
            return
        name = item.text()
        self._assignments.clear_override(name)
        self._assignments.save()
        self.refresh_ui()
        self.set_status(f"Cleared assignment for {name}")

    def _on_delete_emu_clicked(self):
        """Remove assignment and delete the physical executable file."""
        item = self._item_list.currentItem()
        if not item or self._category_ddl.currentText() == "Systems":
            return
            
        exe_path = self._exe_path_edit.text().strip()
        if exe_path and Path(exe_path).exists():
            ans = QMessageBox.warning(self, "Confirm Delete", f"Delete executable at {exe_path}?",
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if ans == QMessageBox.StandardButton.Yes:
                Path(exe_path).unlink(missing_ok=True)
                self._on_clear_emu_clicked()

    def _on_detect_emus_clicked(self):
        """Scan established emulator directories and drive roots for known components."""
        category = self._category_ddl.currentText()
        self.set_status(f"Scanning for {category}...")
        
        # 1. Search within established emulator directories
        emu_dirs_raw = self._cfg.get("GLOBAL", "emulators_directory", fallback="")
        established = [Path(p.strip()) for p in emu_dirs_raw.split('|') if p.strip() and Path(p.strip()).exists()]
        
        # 2. Setup drive roots for exact folder matching
        drives = []
        import os, string
        if os.name == 'nt':
            for d in string.ascii_uppercase:
                p = Path(f"{d}:/")
                if p.exists(): drives.append(p)

        cat_map = {"Emulators": "emulator", "Frontends": "frontend", "Utilities": "utility"}
        target_cat = cat_map.get(category, "emulator")

        found_count = 0
        
        # Scan established directories (recursive search for known EXEs)
        for root in established:
            try:
                for p in root.rglob("*.exe"):
                    for entry in self._emus.by_category(target_cat):
                        if entry.exe.lower() == p.name.lower():
                            found_count += self._register_found_app(category, entry.name, p)
                            break
            except (PermissionError, OSError): continue
            
        # Scan drive roots (non-recursive exact folder match)
        for d in drives:
            try:
                for item in d.iterdir():
                    if item.is_dir():
                        for entry in self._emus.by_category(target_cat):
                            if item.name.lower() == entry.name.lower():
                                exe_path = item / entry.exe
                                if exe_path.exists():
                                    found_count += self._register_found_app(category, entry.name, exe_path)
                                    break
            except (PermissionError, OSError): continue

        main_win = self.window()
        if hasattr(main_win, "refresh_all_tabs"):
            main_win.refresh_all_tabs()
        self.set_status(f"Detection complete. Found {found_count} {category}.")

    def _register_found_app(self, category: str, name: str, path: Path) -> int:
        from core.config import Config
        apps = Config(Config.APPS_FILE)
        sec = "EMULATORS" if category == "Emulators" else category.upper()
        apps.set(sec, name, f'"{path}"')
        apps.save()
        return 1

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
        
        self._update_field_styling()

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
