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
    QFormLayout, QMessageBox, QPlainTextEdit, QProgressDialog,
    QMenu
)

from core.config import global_config
from core.launcher import Launcher, LaunchConfig, suspend_frontends, resume_frontends
from data.systems import SystemRegistry
from core.scanner import SystemScanner
import re
from core.config import Config
from data.assignments import AssignmentRegistry
from data.launch_params import LaunchParamsRegistry
from ui.tabs.settings_tab import _PathCombo, _load_paths, _save_paths
from data.emulators import EmuRegistry
from ui.tabs.base_tab import BaseTab
from utils.paths import app_home, check_paths_exist, img_dir


def match_emu_exe(entry, path: Path) -> bool:
    """
    Check if executable matches emulator entry using hierarchical matching rules.
    
    Matching hierarchy (broadest to most specific):
    1. Broadest matching: emulator name in exe + dir conditions with flexible delimiters
    2. Refined matching: emulator name in exe + exact folder name match  
    3. Further refined: folder begins/ends with emulator name + exe contains name
    4. Further refined: exact EXENAM match + parent dir contains emulator name  
    5. Final match: exact EXENAM + exact parent directory name match
    """
    file_name = path.name.lower()
    file_stem = path.stem.lower()
    
    # Ignore common non-emulator executables to prevent false positives
    if any(x in file_name for x in ['unins', 'setup', 'vcredist', 'dxwebsetup', 
                                     'crashpad', 'updater', 'orgview', 'helper', 
                                     'tool', 'config', 'debug', 'test', 'utility', 
                                     'shipping', 'batchfile']):
        return False

    exe_name = entry.exe.lower()
    exe_stem = Path(exe_name).stem.lower() if '.' in exe_name else exe_name
    
    # Handle [ARCH] placeholder - replace with common patterns
    arch_patterns = []
    if '[arch]' in exe_name:
        base = exe_name.replace('[arch]', '{}')
        arch_patterns = [base.format(''), base.format('x64'), base.format('x86'),
                        base.format('64'), base.format('32'), base.format('win'),
                        base.format('amd64')]
        # Also handle EXENAM with [ARCH] in stem
        if exe_stem:
            base_stem = exe_stem.replace('[arch]', '{}')
            arch_patterns.extend([base_stem.format(''), base_stem.format('x64'),
                                 base_stem.format('x86'), base_stem.format('64'),
                                 base_stem.format('32'), base_stem.format('win'),
                                 base_stem.format('amd64')])
    
    dir_name = path.parent.name.lower()
    name_low = entry.name.lower()
    
    # Broad terms that can precede or follow the emulator name
    broad_terms = ['-', '_', ' ', '(', ')', '.']
    broad_suffixes = ['-', '_', ' ', '(', ')', '.', 'x64', '64', '32', 
                     'win', 'amd64', 'x86', 'qt', 'sdl', 'avx', 'sse']
    dir_suffixes = ['x64', '64', '32', 'win', 'amd64', 'x86', 'qt', 
                   'sdl', 'avx', 'sse']
    
    # Helper functions for matching
    def has_delim_or_suffix(s, delims, suffixes):
        for d in delims:
            if s.startswith(d) or s.endswith(d):
                return True
        for suf in suffixes:
            if s.endswith(suf):
                return True
        return False
    
    def has_exact_delim_or_suffix(s, delims, suffixes):
        return has_delim_or_suffix(s, delims, suffixes)
    
    # For very short names like 'xe', require a whole-word match to avoid path collisions (e.g. nxengine)
    if len(name_low) <= 2:
        exe_contains_name = bool(re.search(rf'\b{re.escape(name_low)}\b', file_stem))
        dir_contains_name = bool(re.search(rf'\b{re.escape(name_low)}\b', dir_name))
    else:
        exe_contains_name = name_low in file_stem
        dir_contains_name = name_low in dir_name
    exact_exe_match = (file_name == exe_name)
    
    # ============================================================
    # Level 1: Broadest matching (most permissive)
    # ============================================================
    # EXE condition: emulator name in exe stem
    # DIR condition: emulator name in parent dir  
    # Both must have delimiter/suffix patterns
    if exe_contains_name and dir_contains_name:
        exe_has_delim = has_delim_or_suffix(file_stem, broad_terms, broad_suffixes)
        dir_has_delim = has_delim_or_suffix(dir_name, broad_terms, dir_suffixes)
        
        if exe_has_delim and dir_has_delim:
            # Handle [ARCH] pattern matches
            if arch_patterns and any(file_name == p.lower() for p in arch_patterns):
                return True
            return True
    
    # Also check [ARCH] patterns directly
    if arch_patterns and any(file_name == p.lower() for p in arch_patterns):
        if dir_contains_name and has_delim_or_suffix(dir_name, broad_terms, dir_suffixes):
            return True
    
    # ============================================================
    # Level 2: Refined matching
    # ============================================================
    # EXE: emulator name in exe stem with delimiters
    # DIR: folder name EXACTLY matches emulator name
    if exe_contains_name and dir_name == name_low:
        if has_exact_delim_or_suffix(file_stem, broad_terms, broad_suffixes):
            if arch_patterns and any(file_name == p.lower() for p in arch_patterns):
                return True
            return True
    
    # ============================================================
    # Level 3: Further refined
    # ============================================================
    # DIR: folder begins or ends with emulator name + delimiter
    # EXE: emulator name in exe stem
    dir_begins = False
    dir_ends = False
    for term in broad_terms + dir_suffixes:
        if dir_name.startswith(name_low + term):
            dir_begins = True
            break
        if dir_name.endswith(term + name_low):
            dir_begins = True
            break
    for term in broad_terms:
        if dir_name.startswith(term + name_low):
            dir_begins = True
            break
        if dir_name.endswith(name_low + term):
            dir_ends = True
            break
    
    if (dir_begins or dir_ends) and exe_contains_name:
        return True
    
    # ============================================================
    # Level 4: Further refined
    # ============================================================
    # EXE: exact EXENAM match
    # DIR: parent dir contains emulator name with delimiters
    if exact_exe_match and dir_contains_name:
        if has_exact_delim_or_suffix(dir_name, broad_terms, dir_suffixes):
            return True
    
    # ============================================================
    # Level 5: Final match (most strict)
    # ============================================================
    # EXE: exact EXENAM match
    # DIR: parent dir EXACTLY matches emulator name
    if exact_exe_match and dir_name == name_low:
        return True
    
    # ============================================================
    # Strict fallback: only match [ARCH] patterns or exact exe name
    # ============================================================
    # [ARCH] placeholder patterns - STRICT match only
    if '[arch]' in exe_name:
        if any(file_name == p.lower() for p in arch_patterns):
            return True
    
    # Exact executable name match
    if exact_exe_match:
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
        self._apps_cfg = Config(Config.APPS_FILE)
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
                
            # Drive roots: Search inside folders that match emulator names
            for d in drives:
                if self._is_cancelled: break
                try:
                    for item in d.iterdir():
                        if not item.is_dir(): continue
                        for entry in self.emus.by_category("emulator"):
                            # Whole-word check for short names to avoid 'xe' matching 'nxengine'
                            name_low = entry.name.lower()
                            if len(name_low) <= 2:
                                folder_match = bool(re.search(rf'\b{re.escape(name_low)}\b', item.name.lower()))
                            else:
                                folder_match = name_low in item.name.lower()
                            
                            if folder_match:
                                # Search root of folder and first-level subdirectories (e.g. /bin)
                                for p in list(item.glob("*.exe")) + list(item.glob("*/*.exe")):
                                    if match_emu_exe(entry, p):
                                        self._register_app(entry.name, p)
                                        self.log.emit(f"Found {entry.name}: {p}")
                                        break
                except (PermissionError, OSError): continue
            
            if self._is_cancelled: return

            for root in established:
                if self._is_cancelled: break
                try:
                    for p in root.rglob("*.exe"):
                        for entry in self.emus.by_category("emulator"):
                            if match_emu_exe(entry, p):
                                self._register_app(entry.name, p)
                                self.log.emit(f"Found {entry.name}: {p}")
                                break
                except (PermissionError, OSError): continue
            
            # Persist all identified emulators to apps.json once
            self._apps_cfg.save()

            # 2.5 Discover RetroArch Cores
            ra_path_str = self._apps_cfg.get("EMULATORS", "retroArch") or self._apps_cfg.get("EMULATORS", "retroarch")
            if ra_path_str:
                self.log.emit("Scanning for RetroArch Cores...")
                ra_path = Path(ra_path_str.strip('"'))
                cores_dir = ra_path.parent / "cores"
                if cores_dir.exists():
                    found_cores = 0
                    for f in cores_dir.glob("*.dll"):
                        core_key = f.stem.replace("_libretro", "")
                        self._apps_cfg.set("CORES", core_key, f'"{f}"')
                        found_cores += 1
                    if found_cores > 0:
                        self.log.emit(f"Detected {found_cores} RetroArch cores.")
                        self._apps_cfg.save()

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
        """Update internal apps configuration with found executable path."""
        self._apps_cfg.set("EMULATORS", name, f'"{path}"')

    def _auto_assign_emulators(self):
        """Auto-assign emulators to systems based on EMUPRESET and SUPEMU."""
        try:
            # Get installed emulators from apps.json
            self._apps_cfg.reload()
            installed_emus = {k.lower(): k for k, v in self._apps_cfg.items("EMULATORS")}
            installed_cores = {k.lower(): k for k, v in self._apps_cfg.items("CORES")}
            
            if not installed_emus:
                self.log.emit("No emulators installed - skipping auto-association.")
                return

            # Get all systems from the registry
            for sys_name, entry in self.scanner._registry._data.items():
                if not entry.supported_emus and not entry.emu_reset and not entry.supported_cores:
                    continue
                
                assigned = False
                
                # First try EMUPRESET (preferred emulator)
                if entry.emu_reset and entry.emu_reset.lower() in installed_emus:
                    self.assignments.set_override(sys_name, installed_emus[entry.emu_reset.lower()])
                    self.log.emit(f"Assigned {entry.emu_reset} to {sys_name} (EMUPRESET)")
                    assigned = True
                
                if not assigned and entry.supported_emus:
                    for emu_name in entry.supported_emus:
                        if emu_name.lower() in installed_emus:
                            self.assignments.set_override(sys_name, installed_emus[emu_name.lower()])
                            self.log.emit(f"Assigned {emu_name} to {sys_name} (SUPEMU)")
                            assigned = True
                            break

                # Finally try SUPCORE (if matches a detected RetroArch core)
                if not assigned and entry.supported_cores:
                    for core_name in entry.supported_cores:
                        if core_name.lower() in installed_cores:
                            self.assignments.set_override(sys_name, "retroArch")
                            self.log.emit(f"Assigned retroArch to {sys_name} via {core_name} (SUPCORE)")
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
        if self._cfg.get("GLOBAL", "first_run", fallback="1") == "1":
            self._start_detection_process(is_first_run=True)

    def _start_detection_process(self, is_first_run=False):
        """Trigger the consolidated background detection worker with progress splash."""
        main_win = self.window()
        if is_first_run and hasattr(main_win, "_tabs") and hasattr(main_win, "_settings_tab"):
            main_win._tabs.setCurrentWidget(main_win._settings_tab)

        # Initialize modal Progress dialog with image
        self._first_run_dialog = QProgressDialog(self.window())
        self._first_run_dialog.setWindowTitle("skeletonKey - Environment Discovery")
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
        self._detection_worker.log.connect(self._first_run_dialog.setLabelText)
        self._detection_worker.progress.connect(self._first_run_dialog.setValue)
        self._detection_worker.finished.connect(self._on_first_run_finished)
        
        self._first_run_dialog.canceled.connect(self._detection_worker.cancel)
        
        self._detection_worker.start()
        self._first_run_dialog.show()

    def _on_first_run_finished(self):
        """Cleanup after background detection completes."""
        if self._cfg.get("GLOBAL", "first_run") == "1":
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

        self._item_list = QListWidget()
        self._item_list.currentItemChanged.connect(lambda cur, prev: self._on_item_selected(cur.text() if cur else ""))
        self._item_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._item_list.customContextMenuRequested.connect(self._show_context_menu)
        
        left_layout.addWidget(self._item_list)
        splitter.addWidget(left)

        # Right panel: system details + ROM browser
        right = QWidget()
        right_layout = QVBoxLayout(right)

        self._info_group = QGroupBox("Configuration")
        self._form = QFormLayout(self._info_group)

        self._name_edit = QLineEdit()
        self._name_edit.setPlaceholderText("System Name (e.g. Nintendo - NES)")
        self._form.addRow("Name:", self._name_edit)

        # Rename row
        self._rename_row = QHBoxLayout()
        self._rename_edit = QLineEdit()
        self._rename_edit.setPlaceholderText("Display Alias...")
        self._rename_btn = QPushButton("Rename")
        self._rename_btn.clicked.connect(self._on_rename_clicked)
        self._rename_row.addWidget(self._rename_edit)
        self._rename_row.addWidget(self._rename_btn)
        self._form.addRow("Alias:", self._rename_row)

        self._rom_path_combo = _PathCombo("ROMs")
        self._form.addRow("ROM Path:", self._rom_path_combo)

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

    # ------------------------------------------------------------------
    # Data population
    # ------------------------------------------------------------------

    def refresh_ui(self):
        """Sync UI lists with data registries."""
        self._systems.reload()
        self._emus.reload() # Keep reloaded for emu dropdown
        self._assignments.reload()
        self._populate_list()

    def _populate_systems(self):
        self._populate_list()

    def _populate_list(self):
        self._search_combo.clear()
        items = self._systems.all_systems()
        self._search_combo.addItems(items)

        self._item_list.clear()
        self._item_list.addItem("Add Custom")
        for name in items:
            item = QListWidgetItem(name)
            entry = self._systems._data.get(name)
            is_detected = False
            if entry:
                path_str = "|".join(entry.rom_path_list)
                is_detected = check_paths_exist(path_str)

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

        apply_style(self._emu_combo, self._emu_combo.currentText())

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
        for i in range(self._item_list.count()):
            item = self._item_list.item(i)
            name = item.text()
            entry = self._systems._data.get(name)
            is_detected = any(Path(p).exists() for p in entry.rom_path_list) if entry else False

            if checked:
                item.setHidden(not is_detected)
            else:
                search_text = self._search_combo.currentText().lower()
                item.setHidden(search_text not in name.lower())

    def _on_detect_clicked(self):
        self._start_detection_process(is_first_run=False)

    def _on_item_selected(self, name: str):
        if not name:
            return
        
        is_custom = (name == "Add Custom")
        self._name_edit.setText("" if is_custom else name)
        self._name_edit.setReadOnly(not is_custom)
        self._ext_edit.setReadOnly(not is_custom)
        self._req_files_edit.setReadOnly(not is_custom)
        
        # Reset fields
        self._ext_edit.clear()
        self._req_files_edit.setPlainText("")
        self._rom_path_combo.set_paths([])

        entry = self._systems._data.get(name)
        if not entry and not is_custom:
            return

        if entry:
            self._rom_path_combo.set_paths(entry.rom_path_list)
            self._ext_edit.setText(", ".join(entry.extensions))
            self._req_files_edit.setPlainText("\n".join(entry.supported_cores))
            self._rename_edit.setText(entry.platform)

        self._emu_combo.blockSignals(True)
        self._emu_combo.clear()
        self._emu_combo.addItem("")

        # Gather supported emulators from master metadata
        supported = []
        if entry:
            supported = list(entry.supported_emus)
            if entry.emu_reset and entry.emu_reset not in supported:
                supported.insert(0, entry.emu_reset)
            
            assigned = self._assignments.get_assignment(name)
            if assigned.primary and assigned.primary not in supported:
                supported.append(assigned.primary)
        else:
            supported = [e.name for e in self._emus.emulators()]

        self._emu_combo.addItems(supported)

        # Sync the combo box to the currently assigned emulator
        if not is_custom:
            idx = self._emu_combo.findText(assigned.primary)
            if idx >= 0: 
                self._emu_combo.setCurrentIndex(idx)
            else:
                self._emu_combo.setCurrentText(assigned.primary)
        self._emu_combo.blockSignals(False)

        self._populate_roms(self._rom_path_combo.current_path())
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

    def _show_context_menu(self, pos):
        item = self._item_list.itemAt(pos)
        if not item or item.text() == "Add Custom": return
        
        menu = QMenu(self)
        del_act = menu.addAction(f"Delete '{item.text()}'")
        del_act.triggered.connect(lambda: self._delete_system(item.text()))
        menu.exec(self._item_list.mapToGlobal(pos))

    def _delete_system(self, name: str):
        ans = QMessageBox.question(self, "Confirm Delete", f"Remove system '{name}' from registry?")
        if ans == QMessageBox.StandardButton.Yes:
            if name in self._systems._data:
                del self._systems._data[name]
                self._systems.save()
                self._assignments.clear_override(name)
                self._assignments.save()
                self.refresh_ui()

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
        pass

    def _edit_system_path(self):
        system = self._item_list.currentItem()
        if not system:
            return

    def _save_assignment(self): # This method is called when the "Save Assignment" button is clicked.
        item = self._item_list.currentItem()
        if not item:
            return
        
        name = self._name_edit.text().strip()
        if not name or name == "Add Custom":
            QMessageBox.warning(self, "Invalid Name", "Please enter a valid system name.")
            return
        
        emu = self._emu_combo.currentText().strip()
        self._assignments.set_override(name, emu)
        self._assignments.save()
        self._systems.set_path(name, _save_paths(self._rom_path_combo.paths()))
        
        # Save custom metadata if it's a new entry
        if name not in self._systems._data:
            from data.systems import SystemEntry
            self._systems._data[name] = SystemEntry(name=name, extensions=[x.strip() for x in self._ext_edit.text().split(",") if x.strip()])
            
        self._systems.save()
        self.set_status(f"Saved System: {name} → {emu}")
        
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
