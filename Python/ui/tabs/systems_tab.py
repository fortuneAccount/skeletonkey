"""
ui/tabs/systems_tab.py

Systems tab – browse systems, set ROM directories, assign emulators,
and launch ROMs.
"""
from pathlib import Path

from PyQt6.QtCore import Qt, QThread, pyqtSignal, QCoreApplication, QTimer
from PyQt6.QtGui import QBrush, QPixmap
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QListWidget, QListWidgetItem, QLabel, QLineEdit,
    QPushButton, QFileDialog, QComboBox, QGroupBox,
    QFormLayout, QMessageBox, QPlainTextEdit, QProgressDialog, QVBoxLayout
)

from core.config import global_config
from core.launcher import Launcher, LaunchConfig, suspend_frontends, resume_frontends
from data.systems import SystemRegistry
from core.scanner import SystemScanner
from data.assignments import AssignmentRegistry
from data.launch_params import LaunchParamsRegistry
from ui.tabs.settings_tab import _PathCombo, _load_paths, _save_paths
from data.emulators import EmuRegistry
from ui.tabs.base_tab import BaseTab
from utils.paths import app_home, check_paths_exist, img_dir


def match_emu_exe(entry, path: Path) -> bool:
    """Check if executable matches emulator entry, handling placeholders and flexible matching."""
    exe_name = entry.exe.lower()
    file_name = path.name.lower()
    
    # Handle [ARCH] placeholder - replace with common patterns
    if '[arch]' in exe_name:
        base = exe_name.replace('[arch]', '{}')
        patterns = [base.format(''), base.format('x64'), base.format('x86'), 
                   base.format('64'), base.format('32'), base.format('amd64')]
        if any(file_name == p for p in patterns):
            return True
    
    # Exact match
    if exe_name == file_name:
        return True
    
    # Check if exe name is contained in file name (e.g., "pcsx2.exe" matches "pcsx2-qt.exe")
    exe_stem = Path(exe_name).stem  # remove .exe
    file_stem = path.stem.lower()
    if exe_stem == file_stem or exe_stem in file_stem or file_stem in exe_stem:
        return True
    
    # Check if emulator name matches directory name and file is a known emulator exe
    dir_name = path.parent.name.lower()
    if entry.name.lower() in dir_name or dir_name in entry.name.lower():
        # Additional check: file name contains emulator name or common exe names
        if any(part in file_name for part in [entry.name.lower(), 'emulator', 'emu', 'qt']):
            return True
    
    return False


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

class DetectionWorker(QThread):
    """Handles the heavy lifting of system and emulator discovery in the background."""
    progress = pyqtSignal(int)
    log = pyqtSignal(str)
    finished = pyqtSignal()

    def __init__(self, scanner: SystemScanner, emus: EmuRegistry, cfg, assignments: AssignmentRegistry):
        super().__init__()
        self.scanner = scanner
        self.emus = emus
        self.cfg = cfg
        self.assignments = assignments
        self._is_cancelled = False

    def cancel(self):
        self._is_cancelled = True

    def run(self):
        if self._is_cancelled: return
        
        # 1. Discover primary directories FIRST (populates config with emu_roots paths)
        try:
            self.log.emit("Indexing drives for primary ROM/Emu roots...")
            self.progress.emit(10)
            
            found_sys_roots, found_emu_roots = self.scanner.discover_primary_dirs()
            for r in found_sys_roots + found_emu_roots:
                self.log.emit(f"Discovered primary root: {r}")
        except Exception as e:
            self.log.emit(f"ERROR in step 1: {str(e)}")
            self.finished.emit()
            return

        if self._is_cancelled: return
        self.progress.emit(25)

        # 2. Emulator Detection
        try:
            self.log.emit("Scanning for Emulators...")
            self.progress.emit(40)

            emu_dirs_raw = self.cfg.get("GLOBAL", "emulators_directory", fallback="")
            established = [Path(p.strip()) for p in emu_dirs_raw.split('|') if p.strip() and Path(p.strip()).exists()]
            
            # Drive roots
            import os, string
            drives = [Path(f"{d}:/") for d in string.ascii_uppercase if os.path.exists(f"{d}:/")] if os.name == 'nt' else [Path("/")]

            for root in established:
                if self._is_cancelled: break
                try:
                    for p in root.rglob("*.exe"):
                        for entry in self.emus.by_category("emulator"):
                            if match_emu_exe(entry, p):
                                self._register_app(entry.name, p)
                                break
                except (PermissionError, OSError): continue
        except Exception as e:
            self.log.emit(f"ERROR in step 2: {str(e)}")
            self.finished.emit()
            return

        if self._is_cancelled: return
        self.progress.emit(60)

        # 3. System Detection
        try:
            self.log.emit("Matching identified folders against known systems...")
            self.scanner.detect_systems(log_callback=lambda msg: self.log.emit(msg))
            
            if self._is_cancelled: return
            self.progress.emit(75)
            self.scanner.exact_match_scan("Systems")
            self.scanner._registry.save()
        except Exception as e:
            self.log.emit(f"ERROR in step 3: {str(e)}")
            self.finished.emit()
            return

        # 4. Auto-associate emulators with systems
        if self._is_cancelled: return
        try:
            self.log.emit("Auto-assigning emulators to systems...")
            self.progress.emit(85)
            self._auto_assign_emulators()
        except Exception as e:
            self.log.emit(f"ERROR in step 4: {str(e)}")
            self.finished.emit()
            return

        self.progress.emit(100)
        self.log.emit("Discovery complete.")
        self.finished.emit()

    def _register_app(self, name: str, path: Path):
        from core.config import Config
        apps = Config(Config.APPS_FILE)
        apps.set("EMULATORS", name, f'"{path}"')
        apps.save()

    def _auto_assign_emulators(self):
        """Auto-assign emulators to systems based on EMUPRESET and SUPEMU."""
        try:
            from core.config import Config
            
            # Get installed emulators from apps.json
            apps = Config(Config.APPS_FILE)
            installed_emus = [k for k, v in apps.items("EMULATORS")]
            
            if not installed_emus:
                self.log.emit("No emulators installed - skipping auto-association.")
                return

            # Get all systems from the registry
            for sys_name, entry in self.scanner._registry._data.items():
                if not entry.supported_emus and not entry.emu_reset:
                    continue
                
                assigned = False
                
                # First try EMUPRESET (preferred emulator)
                if entry.emu_reset and entry.emu_reset in installed_emus:
                    self.assignments.set_override(sys_name, entry.emu_reset)
                    self.log.emit(f"Assigned {entry.emu_reset} to {sys_name} (EMUPRESET)")
                    assigned = True
                
                # If not assigned, try SUPEMU (supported emulators in priority order)
                if not assigned and entry.supported_emus:
                    for emu_name in entry.supported_emus:
                        if emu_name in installed_emus:
                            self.assignments.set_override(sys_name, emu_name)
                            self.log.emit(f"Assigned {emu_name} to {sys_name} (SUPEMU)")
                            assigned = True
                            break
                
                if not assigned:
                    self.log.emit(f"No suitable emulator found for {sys_name}")
            
            # Save assignments
            self.assignments.save()
        except Exception as e:
            self.log.emit(f"ERROR in auto-assignment: {str(e)}")
            import traceback
            self.log.emit(traceback.format_exc())


class SystemsTab(BaseTab):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._cfg = global_config()
        self._systems = SystemRegistry()
        self._scanner = SystemScanner(self._systems)
        self._assignments = AssignmentRegistry()
        self._launch_params = LaunchParamsRegistry()
        self._emus = EmuRegistry()
        self._launch_thread: _LaunchThread | None = None
        self._detection_worker: DetectionWorker | None = None
        self._first_run_dialog: QProgressDialog | None = None
        self._build_ui()
        self._populate_systems()
        
        # Delay first-run logic slightly to ensure main window references are settled
        QTimer.singleShot(100, self._handle_first_run)

    def _handle_first_run(self):
        """Execute full environment discovery on first launch."""
        if self._cfg.get("GLOBAL", "first_run", fallback="1") != "1":
            return

        main_win = self.window()
        if hasattr(main_win, "_tabs") and hasattr(main_win, "_settings_tab"):
            main_win._tabs.setCurrentWidget(main_win._settings_tab)

        # Initialize modal Splash/Progress dialog with image
        self._first_run_dialog = QProgressDialog(self.window())
        self._first_run_dialog.setWindowTitle("skeletonKey - First Run Setup")
        self._first_run_dialog.setLabelText("Initializing Environment...")
        self._first_run_dialog.setWindowModality(Qt.WindowModality.WindowModal)
        self._first_run_dialog.setRange(0, 100)
        self._first_run_dialog.setAutoClose(True)

        # Add custom splash image to dialog layout
        layout = self._first_run_dialog.layout()
        if layout:
            splash_label = QLabel()
            pix = QPixmap(str(img_dir() / "splash.png"))
            if not pix.isNull():
                splash_label.setPixmap(pix.scaledToWidth(400, Qt.TransformationMode.SmoothTransformation))
                splash_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
                layout.insertWidget(0, splash_label)

        self._first_run_dialog.resize(450, 300)
        
        # Setup Worker Thread
        self._detection_worker = DetectionWorker(self._scanner, self._emus, self._cfg, self._assignments)
        self._detection_worker.log.connect(self.log)
        self._detection_worker.progress.connect(self.set_progress)
        self._detection_worker.finished.connect(self._on_first_run_finished)
        
        self._first_run_dialog.canceled.connect(self._detection_worker.cancel)
        
        self._detection_worker.start()
        self._first_run_dialog.show()

    def _on_first_run_finished(self):
        """Cleanup after background detection completes."""
        self._cfg.set("GLOBAL", "first_run", "0")
        self._cfg.save()
        
        main_win = self.window()
        if hasattr(main_win, "refresh_all_tabs"):
            main_win.refresh_all_tabs()
        
        if self._first_run_dialog:
            self._first_run_dialog.close()

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
        self._category_ddl.addItems(["Systems", "Emulators"])
        self._category_ddl.currentTextChanged.connect(self._on_category_changed)
        left_layout.addWidget(self._category_ddl)

        self._item_list = QListWidget()
        self._item_list.currentItemChanged.connect(lambda cur, prev: self._on_item_selected(cur.text() if cur else ""))
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
            
        if self._first_run_dialog:
            self._first_run_dialog.setLabelText(message)
            QCoreApplication.processEvents()

    def set_progress(self, value: int):
        """Update progress bar in Settings tab."""
        main_win = self.window()
        if hasattr(main_win, "_settings_tab"):
            main_win._settings_tab.set_progress(value)
            
        if self._first_run_dialog:
            self._first_run_dialog.setValue(value)
            QCoreApplication.processEvents()

    def refresh_ui(self):
        """Sync UI lists with data registries."""
        self._systems.reload()
        self._populate_list()

    def _update_active_domain(self, category: str):
        self._search_combo.clear()
        items = []
        if category == "Systems":
            items = self._systems.all_systems()
        elif category == "Emulators":
            items = [e.name for e in self._emus.emulators()]
            
        self._search_combo.addItems(items)
        
        # Apply status-indicating font-color-coding: gray out if NOT currently assigned/installed
        for i in range(self._search_combo.count()):
            name = self._search_combo.itemText(i)
            # Matches logic used in _populate_list
            if category == "Systems":
                entry = self._systems._data.get(name)
                is_detected = any(Path(p).exists() for p in entry.rom_path_list) if entry else False
            else:
                # Check if emulator is installed via apps.json
                is_detected = bool(self._emus._apps_cfg.get("EMULATORS", name))

            if not is_detected:
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
        elif category == "Emulators":
            items = [e.name for e in self._emus.emulators()]
        else:
            items = []
            
        # Color logic: grey out if ROM path doesn't exist (Systems) or not installed (Emulators)
        for name in items:
            item = QListWidgetItem(name)
            if category == "Systems":
                entry = self._systems._data.get(name)
                is_detected = check_paths_exist(entry.rom_paths) if entry else False
            else:
                is_detected = bool(self._emus._apps_cfg.get("EMULATORS", name))

            if not is_detected:
                item.setForeground(QBrush(Qt.GlobalColor.gray))
            self._item_list.addItem(item)
            
        # Only apply filtering if the 'Detected Only' toggle is active.
        # Do NOT auto-filter by the search combo text here, as that would 
        # immediately hide all but the first system in the list.
        if self._filter_detected_btn.isChecked():
            self._on_filter_detected_toggled(True)
        else:
            # Ensure everything is visible
            self._on_search_text_changed("")

    def _update_field_styling(self):
        """Apply Green (active) or Yellow (pending/missing) backgrounds to assignment fields."""
        def apply_style(widget, value: str):
            if not value:
                widget.setStyleSheet(""); return
            
            color = "#28a745" if check_paths_exist(value) else "#ffc107"
            text_color = "white" if color == "#28a745" else "black"
            widget.setStyleSheet(f"background-color: {color}; color: {text_color}; font-weight: bold;")

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
            
            if cat == "Systems":
                entry = self._systems._data.get(name)
                is_detected = any(Path(p).exists() for p in entry.rom_path_list) if entry else False
            else:
                is_detected = bool(self._emus._apps_cfg.get("EMULATORS", name))

            if checked:
                item.setHidden(not is_detected)
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
            entry = self._systems._data.get(name)
            if not entry:
                return

            self._rom_path_combo.set_paths(entry.rom_path_list)
            
            # Load metadata from SystemEntry
            if entry:
                self._ext_edit.setText(", ".join(entry.extensions))
                self._req_files_edit.setPlainText("\n".join(entry.supported_cores))

            assigned = self._assignments.get_assignment(name)
            idx = self._emu_combo.findText(assigned.primary)
            if idx >= 0: 
                self._emu_combo.setCurrentIndex(idx)
            else:
                self._emu_combo.setCurrentText(assigned.primary)

            self._populate_roms(self._rom_path_combo.current_path())
            self._update_field_styling()
        else:
            entry = self._emus.get(name)
            if entry:
                from core.config import Config
                apps = Config(Config.APPS_FILE)
                sec = "EMULATORS" if category == "Emulators" else category.upper()
                path = apps.get(sec, name, fallback="")
                
                self._exe_path_edit.setText(path)
                self._config_path_combo.set_paths(entry.configs or [])
                self._ext_edit.setText(", ".join(entry.extensions or []))
                self._req_files_edit.setPlainText("\n".join(entry.required_files or []))
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
        found_sys_roots, found_emu_roots = self._scanner.discover_primary_dirs()
        for r in found_sys_roots + found_emu_roots:
            self.log(f"Discovered primary root: {r}")

        # Refresh Settings Tab to show the newly discovered roots
        main_win = self.window()
        if hasattr(main_win, "_settings_tab"):
            main_win._settings_tab.refresh_ui()
        self.set_progress(20)

        # 2. Scan within established dirs and exact match drive roots
        self.log("Matching identified folders against known systems...")
        self._scanner.detect_systems(log_callback=self.log) 
        self.set_progress(30)
        found_exact = self._scanner.exact_match_scan("Systems")
        for item in found_exact:
            self.log(f"Identified system: {item}")

        self._systems.save()
        self.refresh_ui()
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

        cat_map = {"Emulators": "emulator"}
        target_cat = cat_map.get(category, "emulator")

        found_count = 0
        
        # Scan established directories (recursive search for known EXEs)
        for root in established:
            try:
                for p in root.rglob("*.exe"):
                    for entry in self._emus.by_category(target_cat):
                        if match_emu_exe(entry, p):
                            found_count += self._register_found_app(category, entry.name, p)
                            break
            except (PermissionError, OSError): continue
            
        # Scan drive roots (non-recursive exact folder match)
        for d in drives:
            self.log(f"Checking drive {d} for {category}...")
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
            sec = "EMULATORS"
            path = self._exe_path_edit.text().strip()
            apps.set(sec, name, f'"{path}"')
            apps.save()

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

        assignment = self._assignments.get_assignment(system_name)
        emu_entry = self._emus.get(assignment.primary)
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
            include_extension=True, # Standard for manual launches
            include_path=True,
            working_dir=str(emu_dir),
            extract_rom=lp.extract,
            clean_after=lp.clean
        )
        launcher = Launcher(cfg)
        self._launch_thread = _LaunchThread(launcher)
        self._launch_thread.finished.connect(self._on_launch_finished)
        self._launch_thread.start()
        self.set_status(f"Launching {item.text()}…")

    def _on_launch_finished(self, exit_code: int):
        self.set_status(f"Emulator exited (code {exit_code})")
