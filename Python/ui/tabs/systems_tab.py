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
    QMenu, QCheckBox
)
import shutil
import hashlib
import xml.etree.ElementTree as ET

from core.config import global_config
from core.launcher import Launcher, LaunchConfig, suspend_frontends, resume_frontends
from data.systems import SystemRegistry, SystemEntry
from core.scanner import SystemScanner
import os
import re
import string
import threading
from core.task_manager import TaskManager
from concurrent.futures import ThreadPoolExecutor
from core.config import Config
from data.assignments import AssignmentRegistry
from data.launch_params import LaunchParamsRegistry
from data.emulators import EmuRegistry
from ui.tabs.base_tab import BaseTab
from utils.paths import app_home, check_paths_exist, img_dir



from ui.tabs.settings_tab import _PathCombo, _save_paths

class DetectionWorker(QThread):
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
        self._claimed_dirs = set()

        # Comprehensive exclusion list to filter non-emulator executables
        self.exclusion_list = [
            "tool", "util", "debug", "crack", "patch", "addon", "dlc",
            "test", "unins", "config", "shipping", "settings", "helper", "updater", "sync", "agent",
            "setup", "webview", "crashhandler", "redist", "plugin", "licens", "gmlive", "dxweb",
            "ffmp", "ffpr", "dotnet", "crashsend", "crashreport", "dump", "devcon", "errmsg",
            "dll", "install", "ndp48", "jpg", "png", "app.publish"
        ]

    def cancel(self):
        self._is_cancelled = True
    def _is_fuzzy_match(self, p_name: str, entry_exe: str) -> bool:
        """
        Matches based on: (opt sep) + base_stem + (opt sep) + (opt arch) + .* + .exe
        """
        p_name_lower = p_name.lower()
        if any(keyword in p_name_lower for keyword in self.exclusion_list):
            return False

        exe_name_lower = Path(entry_exe).name.lower()
        if p_name_lower == exe_name_lower: # Exact Match
            return True

        stem = Path(exe_name_lower).stem
        sep = r"[\s\.\-_)]"
        arch = r"(?:x64|x86|64|32|win|amd|sse|avx)"
        
        # Derive base_stem by stripping arch suffixes from metadata stem (e.g. mame64 -> mame)
        base_stem = re.sub(rf"(?:{sep}|{arch})+$", "", stem, flags=re.IGNORECASE)

        # Implementation of requested regex: (opt sep) + base_stem + (opt sep) + (opt arch) + .* + .exe
        pattern = rf"^(?:{sep}*)?{re.escape(base_stem)}(?:{sep}*)?(?:{arch})?.*\.exe$"
        return bool(re.match(pattern, p_name_lower, re.IGNORECASE))

    def _get_claim_root(self, path: Path, roots: list[Path]) -> Path | None:
        """Identify the top-level folder inside an emu_root or drive root to claim."""
        try:
            p = path.resolve()
            for r in roots:
                if str(p).lower().startswith(str(r).lower()):
                    relative = p.relative_to(r)
                    if relative.parts: return (r / relative.parts[0]).resolve()
            drive_root = Path(p.anchor)
            relative = p.relative_to(drive_root)
            if relative.parts: return (drive_root / relative.parts[0]).resolve()
        except: pass
        return None

    def run(self):
        if self._is_cancelled: return
        
        self._claimed_dirs.clear()
        self._apps_cfg.reload()

        # Ensure search roots are defined. If empty, perform primary environment discovery.
        emu_roots_str = self.cfg.get("GLOBAL", "emulators_directory", fallback="").strip()
        sys_roots_str = self.cfg.get("GLOBAL", "systems_directory", fallback="").strip()
        if not emu_roots_str or not sys_roots_str:
            self.log.emit("Missing directory configuration. Performing primary drive scan...")
            self.scanner.discover_primary_dirs()
            self.cfg.reload()
            emu_roots_str = self.cfg.get("GLOBAL", "emulators_directory", fallback="").strip()

        emu_roots = [Path(p).resolve() for p in emu_roots_str.split("|") if p.strip() and Path(p).exists()]
        drives = [Path(f"{d}:/") for d in string.ascii_uppercase if os.path.exists(f"{d}:/")] if os.name == 'nt' else [Path("/")]
        
        all_emus = self.emus.by_category("emulator")
        emus_to_find = {e.name.lower(): e for e in all_emus}

        # 0. Pre-index existing apps.json to claim their directories
        for sect in ["EMULATORS", "CORES", "UTILITIES"]:
            for name, path_str in self._apps_cfg.items(sect):
                p_path = Path(path_str.strip('"'))
                if p_path.exists():
                    claim = self._get_claim_root(p_path, emu_roots)
                    if claim: self._claimed_dirs.add(claim)
                    emus_to_find.pop(name.lower(), None)

        # Phase 1: Search Drive Roots + Common generic roots (\emu, \emulators)
        self.log.emit("Phase 1: Searching drive roots for emulators...")
        generic_roots = ["emu", "emulators", "rom programs", "bin"]
        
        for d in drives:
            if self._is_cancelled: break
            try:
                # Check drive root directly and children of generic folders like C:\emu
                targets = [d] + [d / g for g in generic_roots if (d / g).exists()]
                for target in targets:
                    for item in target.iterdir():
                        if not item.is_dir() or item.name.startswith('$'): continue
                        item_res = item.resolve()
                        if item_res in self._claimed_dirs: continue
                        
                        name_low = item.name.lower()
                        if name_low in emus_to_find: # Folder matches emulator name
                            entry = emus_to_find[name_low]
                            exe_path = item / entry.exe
                            if exe_path.exists():
                                self._register_app(entry.name, exe_path)
                                self.log.emit(f"Found {entry.name}: {exe_path}")
                                del emus_to_find[name_low]
                                self._claimed_dirs.add(item_res)
                            else:
                                # Fuzzy scan matching folder if exact EXENAM is missing
                                for p in item.rglob("*.exe"):
                                    if self._is_fuzzy_match(p.name, entry.exe):
                                        self._register_app(entry.name, p)
                                        self.log.emit(f"Found {entry.name} (Fuzzy): {p}")
                                        del emus_to_find[name_low]
                                        self._claimed_dirs.add(item_res)
                                        break
            
            except (PermissionError, OSError): continue
        self.progress.emit(40)

        # Phase 2: Global recursive scan of emu_roots
        self.log.emit(f"Phase 2: Scanning emu_roots for {len(emus_to_find)} remaining emulators...")
        for root in emu_roots:
            if self._is_cancelled: break
            try:
                for p in root.rglob("*.exe"):
                    if self._is_cancelled: break
                    # Check if this file is in a folder already claimed by another emulator
                    if any(str(p.resolve()).lower().startswith(str(c).lower()) for c in self._claimed_dirs):
                        continue

                    # Sort by name length descending to ensure specific emulators (VisualBoyAdvance-M) match before generic ones
                    for name in sorted(emus_to_find.keys(), key=len, reverse=True):
                        entry = emus_to_find[name]
                        if self._is_fuzzy_match(p.name, entry.exe):
                            self._register_app(entry.name, p)
                            self.log.emit(f"Found {entry.name} (Fuzzy): {p}")
                            claim = self._get_claim_root(p, emu_roots)
                            if claim: self._claimed_dirs.add(claim)
                            emus_to_find.pop(name)
                            break
            except (PermissionError, OSError): continue
        
        # Phase 3: RetroArch Core Detection
        ra_path = self._apps_cfg.get("EMULATORS", "retroArch") or self._apps_cfg.get("EMULATORS", "retroarch")
        if ra_path:
            self.log.emit("Phase 3: Detecting RetroArch cores...")
            cores_dir = Path(ra_path.strip('"')).parent / "cores"
            if cores_dir.exists():
                for f in cores_dir.glob("*.dll"):
                    core_key = f.stem.replace("_libretro", "")
                    self._apps_cfg.set("CORES", core_key, f'"{f}"')
                    self.log.emit(f"  Detected Core: {core_key}")
                self._apps_cfg.save()

        # Phase 4: System Discovery
        self.log.emit("Phase 4: Matching identified folders against known systems...")
        self.progress.emit(80)
        try:
            self.scanner.detect_systems(log_callback=lambda msg: self.log.emit(msg))
            self.scanner.exact_match_scan("Systems")
            self.scanner._registry.save()
            
            # Auto-associate emulators with systems
            self.log.emit("Auto-assigning emulators to systems...")
            self._auto_assign_emulators()
        except Exception as e:
            self.log.emit(f"Error during system discovery: {e}")

        self.log.emit(f"Discovery process finished. {len(self._claimed_dirs)} directories indexed.")
        self.progress.emit(100)
        self.finished.emit()

    def _register_app(self, name: str, path: Path):
        """Update internal apps configuration with found executable path."""
        self._apps_cfg.set("EMULATORS", name, f'"{path}"')
        self._apps_cfg.save()

    def _auto_assign_emulators(self):
        """Auto-assign emulators to systems based on EMUPRESET and SUPEMU."""
        try:
            self._apps_cfg.reload()
            installed_emus = {k.lower(): k for k, v in self._apps_cfg.items("EMULATORS")}
            installed_cores = {k.lower(): k for k, v in self._apps_cfg.items("CORES")}
            
            if not installed_emus:
                return

            for sys_name, entry in self.scanner._registry._data.items():
                # Skip systems without ROM paths (not discovered)
                if not entry.rom_paths:
                    continue
                
                assigned = False
                # 1. Try EMUPRESET
                if entry.emu_reset and entry.emu_reset.lower() in installed_emus:
                    self.assignments.set_override(sys_name, installed_emus[entry.emu_reset.lower()])
                    assigned = True
                
                # 2. Try SUPEMU
                if not assigned and entry.supported_emus:
                    for emu_name in entry.supported_emus:
                        if emu_name.lower() in installed_emus:
                            self.assignments.set_override(sys_name, installed_emus[emu_name.lower()])
                            assigned = True
                            break

                # 3. Try SUPCORE
                if not assigned and entry.supported_cores:
                    for core_name in entry.supported_cores:
                        if core_name.lower() in installed_cores:
                            self.assignments.set_override(sys_name, "retroArch")
                            assigned = True
                            break
                
            self.assignments.save()
        except Exception as e:
            self.log.emit(f"ERROR in auto-assignment: {str(e)}")

class JacketizeWorker(QThread):
    """Background worker for organizing ROMs into title-based folders."""
    progress = pyqtSignal(int)
    status = pyqtSignal(str)
    finished = pyqtSignal()

    def __init__(self, rom_dir, use_dat, use_filename, inverted, recursive, include_all, dat_data, extensions):
        super().__init__()
        self.rom_dir = Path(rom_dir)
        self.use_dat = use_dat
        self.use_filename = use_filename
        self.inverted = inverted
        self.recursive = recursive
        self.include_all = include_all
        self.dat_data = dat_data
        self.extensions = [e.lower() for e in extensions]
        self._is_cancelled = False

    def cancel(self):
        self._is_cancelled = True

    def _get_clean_name(self, name: str) -> str:
        name = re.sub(r'\(.*?\)|\[.*?\]', '', name)
        return re.sub(r'[<>:"/\\|?*]', '-', name).strip()

    def _get_alpha_key(self, name: str) -> str:
        return re.sub(r'[^a-z0-9]', '', name.lower())

    def _get_inverted_name(self, name: str) -> str:
        lower = name.lower()
        for prefix in ["the ", "a ", "an "]:
            if lower.startswith(prefix):
                return name[len(prefix):].strip() + ", " + name[:len(prefix)].strip()
        return name

    def run(self):
        self.status.emit("Scanning directory...")
        pattern = "**/*" if self.recursive else "*"
        all_files = list(self.rom_dir.glob(pattern))
        rom_files = [f for f in all_files if f.is_file() and f.suffix.lower().lstrip('.') in self.extensions]
        
        if not rom_files:
            self.status.emit("No ROMs found to organize.")
            return

        # Calculate hashes in parallel
        self.status.emit("Calculating file hashes...")
        rom_hashes = {}
        with concurrent.futures.ThreadPoolExecutor() as executor:
            future_to_rom = {executor.submit(_calculate_md5, rf): rf for rf in rom_files}
            for i, future in enumerate(concurrent.futures.as_completed(future_to_rom)):
                if self._is_cancelled: break
                file_path, md5_val = future.result()
                rom_hashes[file_path] = md5_val
                self.progress.emit(int((i / total) * 25)) # First 25% for hashing
        
        if self._is_cancelled: return

        groups = {} # alpha_key -> {folder_name, roms: []}

        for i, rom_file in enumerate(rom_files): # Iterate through rom_files again to use calculated hashes
            if self._is_cancelled: break
            self.progress.emit(25 + int((i / total) * 25)) # Next 25% for grouping
            
            title = None
            if self.use_dat and rom_file in rom_hashes:
                md5_val = rom_hashes[rom_file]
                if md5_val in self.dat_data:
                    title = self.dat_data[md5_val]

            if not title and self.use_filename:
                title = self._get_clean_name(rom_file.stem)

            if title:
                key = self._get_alpha_key(self._get_clean_name(title))
                if key not in groups:
                    folder_name = self._get_clean_name(title)
                    if self.inverted:
                        folder_name = self._get_inverted_name(folder_name)
                    groups[key] = {"name": folder_name, "roms": []}
                groups[key]["roms"].append(rom_file)

        count = 0
        self.status.emit("Moving files...")
        for i, (key, data) in enumerate(groups.items()):
            if self._is_cancelled: break
            self.progress.emit(50 + int((i / len(groups)) * 50)) # Last 50% for moving
            
            dest_dir = self.rom_dir / data["name"]
            dest_dir.mkdir(exist_ok=True)
            
            for rom_file in data["roms"]:
                try:
                    # Move the ROM
                    target = dest_dir / rom_file.name
                    shutil.move(str(rom_file), str(target))
                    count += 1

                    # Handle "Include All ROM-file matches"
                    if self.include_all:
                        rom_alpha = self._get_alpha_key(self._get_clean_name(rom_file.stem))
                        # Scan only same directory for adjacent files
                        for adj in rom_file.parent.iterdir():
                            if adj.is_file() and adj.suffix.lower().lstrip('.') not in self.extensions:
                                if self._get_alpha_key(self._get_clean_name(adj.stem)) == rom_alpha:
                                    try:
                                        shutil.move(str(adj), str(dest_dir / adj.name))
                                    except: pass
                except: pass

        self.status.emit(f"Jacketize complete: {count} files organized.")
        self.finished.emit()


class UndoWorker(QThread):
    """Background worker to reverse file movements from a log file."""
    progress = pyqtSignal(int)
    status = pyqtSignal(str)
    finished = pyqtSignal()

    def __init__(self, log_path: Path):
        super().__init__()
        self.log_path = log_path

    def run(self):
        try:
            with open(self.log_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            
            moves = data.get("moves", [])
            total = len(moves)
            count = 0
            
            # Process in reverse order to undo nested movements correctly
            for i, move in enumerate(reversed(moves)):
                src = Path(move["src"])
                dst = Path(move["dst"])
                if dst.exists():
                    src.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(dst), str(src))
                    count += 1
                self.progress.emit(int(((i + 1) / total) * 100))
            
            self.status.emit(f"Undo complete: {count} files restored.")
        except Exception as e:
            self.status.emit(f"Undo failed: {e}")
        self.finished.emit()


class _LaunchThread(QThread):
    finished = pyqtSignal(int)
    def __init__(self, launcher):
        super().__init__()
        self._launcher = launcher
    def run(self):
        from core.launcher import suspend_frontends, resume_frontends
        suspend_frontends(); code = self._launcher.launch(); resume_frontends()
        self.finished.emit(code)


class SystemsTab(BaseTab):
    def __init__(self, systems: SystemRegistry, emus: EmuRegistry, assignments: AssignmentRegistry, launch_params: LaunchParamsRegistry, tasks: TaskManager, parent=None):
        super().__init__(parent)
        self._cfg = global_config()
        self._systems = systems
        self._scanner = SystemScanner(self._systems)
        self._assignments = assignments
        self._launch_params = launch_params
        self._tasks = tasks
        self._emus = emus
        self._launch_thread: _LaunchThread | None = None
        self._detection_worker: DetectionWorker | None = None
        self._dat_data = {}
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
        """Trigger background detection and route all output to Activity Log."""
        main_win = self.window()
        if is_first_run and hasattr(main_win, "_tabs") and hasattr(main_win, "_settings_tab"):
            main_win._tabs.setCurrentWidget(main_win._settings_tab)

        self._detection_worker = DetectionWorker(self._scanner, self._emus, self._cfg, self._assignments)
        
        # Route progress and log directly to the Settings tab UI
        if hasattr(main_win, "_settings_tab"):
            self._detection_worker.log.connect(main_win._settings_tab.append_log)
            self._detection_worker.progress.connect(main_win._settings_tab.set_progress)

        self._detection_worker.finished.connect(self._on_first_run_finished)
        self._tasks.start_task("system_detection", self._detection_worker)

    def _on_first_run_finished(self):
        if self._cfg.get("GLOBAL", "first_run") == "1":
            self._cfg.set("GLOBAL", "first_run", "0")
        self._cfg.save()
        
        main_win = self.window()
        if hasattr(main_win, "refresh_all_tabs"):
            main_win.refresh_all_tabs()

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
        self._clear_search_btn.setToolTip("Clear current search")
        self._clear_search_btn.clicked.connect(self._clear_search)

        self._filter_detected_btn = QPushButton("Y")
        self._filter_detected_btn.setFixedWidth(24)
        self._filter_detected_btn.setCheckable(True)
        self._filter_detected_btn.setToolTip("Filter detected systems only")
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
        self._rom_path_combo._combo.currentTextChanged.connect(self._populate_roms)
        self._form.addRow("ROM Path:", self._rom_path_combo)

        self._ext_edit = QLineEdit()
        self._ext_edit.setPlaceholderText("e.g. zip, sfc, smc")
        self._form.addRow("Extensions:", self._ext_edit)

        self._req_files_edit = QPlainTextEdit()
        self._req_files_edit.setMaximumHeight(60)
        self._form.addRow("Required Files:", self._req_files_edit)
        
        self._dat_meta_btn = QPushButton("dat")
        self._dat_meta_btn.setFixedWidth(60)
        self._form.addRow("", self._dat_meta_btn)

        self._emu_combo = QComboBox()
        self._emu_combo.setEditable(True)
        self._emu_combo.currentTextChanged.connect(self._on_emu_changed)
        self._form.addRow("Emulator:", self._emu_combo)

        self._emu_opts_edit = QLineEdit()
        self._form.addRow("Options:", self._emu_opts_edit)

        self._emu_args_edit = QLineEdit()
        self._form.addRow("Arguments:", self._emu_args_edit)

        # Action Buttons Row
        action_row = QHBoxLayout()
        self._select_btn = QPushButton("Select")
        self._select_btn.clicked.connect(self._on_select_emu_clicked)
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
        right_layout.addWidget(QLabel("ROMs"))

        self._rom_list = QListWidget()
        self._rom_list.itemDoubleClicked.connect(self._launch_rom)
        right_layout.addWidget(self._rom_list)

        # Bottom library controls aligned right
        lib_controls = QVBoxLayout()
        
        chk_layout = QHBoxLayout()
        chk_layout.addStretch()
        self._recursive_chk = QCheckBox("Recursive")
        self._include_all_chk = QCheckBox("Include All ROM-file matches")
        self._use_dat_chk = QCheckBox("Dat")
        self._use_filename_chk = QCheckBox("Filename")
        self._inverted_title_chk = QCheckBox("Inverted Title")
        for chk in [self._recursive_chk, self._include_all_chk, self._use_dat_chk, self._use_filename_chk, self._inverted_title_chk]:
            chk_layout.addWidget(chk)
        lib_controls.addLayout(chk_layout)

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        self._load_dat_btn = QPushButton("Load dat")
        self._load_dat_btn.clicked.connect(self._on_load_dat_clicked)
        self._jacketize_btn = QPushButton("Jacketize")
        self._jacketize_btn.clicked.connect(self._on_jacketize_clicked)
        self._individuate_btn = QPushButton("Individuate")
        self._individuate_btn.clicked.connect(self._on_individuate_clicked)
        self._undo_lib_btn = QPushButton("Undo")
        self._undo_lib_btn.clicked.connect(self._on_undo_clicked)
        self._cancel_lib_btn = QPushButton("Cancel")
        self._cancel_lib_btn.setEnabled(False)
        self._cancel_lib_btn.clicked.connect(self._on_cancel_lib_clicked)
        for btn in [self._load_dat_btn, self._jacketize_btn, self._individuate_btn, self._undo_lib_btn, self._cancel_lib_btn]:
            btn_layout.addWidget(btn)
        lib_controls.addLayout(btn_layout)
        
        right_layout.addLayout(lib_controls)

        launch_layout = QHBoxLayout()
        launch_layout.addStretch()
        self._launch_btn = QPushButton("RUN")
        self._launch_btn.setMinimumWidth(100)
        self._launch_btn.setStyleSheet("font-weight: bold;")
        self._launch_btn.clicked.connect(self._launch_selected)
        launch_layout.addWidget(self._launch_btn)
        right_layout.addLayout(launch_layout)

        splitter.addWidget(right)
        splitter.setSizes([200, 600])

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
        self._rom_path_combo._combo.blockSignals(True)
        self._rom_path_combo.set_paths([])

        entry = self._systems._data.get(name)
        if not entry and not is_custom:
            self._rom_path_combo._combo.blockSignals(False)
            return

        if entry:
            self._rom_path_combo.set_paths(entry.rom_path_list)
            self._ext_edit.setText(", ".join(entry.extensions))
            self._req_files_edit.setPlainText("\n".join(entry.supported_cores))
            self._rename_edit.setText(entry.platform)
        self._rom_path_combo._combo.blockSignals(False)

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

        # Clear options/args fields before updating
        self._emu_opts_edit.clear()
        self._emu_args_edit.clear()

        # Sync the combo box to the currently assigned emulator
        if not is_custom:
            idx = self._emu_combo.findText(assigned.primary)
            if idx >= 0: 
                self._emu_combo.setCurrentIndex(idx)
            else:
                self._emu_combo.setCurrentText(assigned.primary)
                
            # Explicitly trigger option loading for the selected emulator
            self._on_emu_changed(self._emu_combo.currentText())

        self._emu_combo.blockSignals(False)

        self._populate_roms(self._rom_path_combo.current_path())
        self._update_field_styling()

    def _on_emu_changed(self, emu_name: str):
        """Load options/arguments for the selected emulator from systems metadata."""
        system_item = self._item_list.currentItem()
        if not system_item or not emu_name: return
        
        opts, args = self._systems.get_emu_metadata(system_item.text(), emu_name)
        self._emu_opts_edit.setText(opts[0] if opts else "")
        self._emu_args_edit.setText(args[0] if args else "")

    def _populate_roms(self, rom_dir: str):
        """List files in the selected ROM directory filtering by extension."""
        self._rom_list.clear()
        if not rom_dir:
            return
            
        path = Path(rom_dir)
        if not path.exists() or not path.is_dir():
            return
            
        ext_str = self._ext_edit.text().lower()
        exts = [x.strip() for x in ext_str.split(",") if x.strip()]
            
        try:
            # Sort files alphabetically
            items = sorted([f for f in path.iterdir() if f.is_file()], key=lambda x: x.name.lower())
            for item in items:
                if not exts or item.suffix.lower().lstrip('.') in exts:
                    self._rom_list.addItem(item.name)
        except (PermissionError, OSError):
            pass

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

    def _on_load_dat_clicked(self):
        """Load a Logiqx XML Dat file for hash matching."""
        path, _ = QFileDialog.getOpenFileName(self, "Select Dat File", "", "Dat Files (*.dat *.xml);;All Files (*)")
        if not path:
            return
        
        self._dat_data = {}
        try:
            tree = ET.parse(path)
            root = tree.getroot()
            for game in root.findall('game'):
                game_name = game.get('name')
                for rom in game.findall('rom'):
                    md5 = rom.get('md5')
                    if md5:
                        self._dat_data[md5.lower()] = game_name
            self.set_status(f"Loaded {len(self._dat_data)} entries from dat.")
        except Exception as e:
            self.set_status(f"Failed to load dat: {e}")

    def _on_individuate_clicked(self):
        rom_dir = self._rom_path_combo.current_path()
        if not rom_dir or not Path(rom_dir).exists(): return
        
        rom_items = [self._rom_list.item(i).text() for i in range(self._rom_list.count())]
        worker = IndividuateWorker(rom_dir, rom_items)
        
        worker.status.connect(self.set_status)
        worker.finished.connect(lambda: self._on_lib_task_finished(rom_dir))
        
        main_win = self.window()
        if hasattr(main_win, "_settings_tab"):
            worker.progress.connect(main_win._settings_tab.set_progress)

        self._cancel_lib_btn.setEnabled(True)
        self._tasks.start_task("individuate_process", worker)

    def _on_undo_clicked(self):
        """Open file browser to select a movement log and trigger undo."""
        log_dir = temp_dir()
        path_str, _ = QFileDialog.getOpenFileName(
            self, "Select Movement Log to Undo", str(log_dir), 
            "Movement Logs (*_log_*.json);;All Files (*)"
        )
        if not path_str:
            return

        path = Path(path_str)
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            moves = data.get("moves", [])
            count = len(moves)

            if count > 10:
                reply = QMessageBox.question(
                    self, "Confirm Undo",
                    f"This operation will restore {count} files to their original locations.\n\nDo you want to proceed?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
                )
                if reply == QMessageBox.StandardButton.No:
                    return
        except Exception as e:
            self.set_status(f"Error reading log file: {e}")
            return

        worker = UndoWorker(path)
        worker.status.connect(self.set_status)
        worker.finished.connect(lambda: self._on_lib_task_finished(self._rom_path_combo.current_path()))
        
        main_win = self.window()
        if hasattr(main_win, "_settings_tab"):
            worker.progress.connect(main_win._settings_tab.set_progress)

        self._tasks.start_task("undo_process", worker)

    def _on_cancel_lib_clicked(self):
        self._tasks.cancel_task("jacketize_process")
        self._tasks.cancel_task("individuate_process")
        self._cancel_lib_btn.setEnabled(False)

    def _on_jacketize_clicked(self):
        """
        Starts the Jacketize background process via TaskManager.
        """
        rom_dir = self._rom_path_combo.current_path()
        if not rom_dir or not Path(rom_dir).exists(): return
        
        if not (self._use_dat_chk.isChecked() or self._use_filename_chk.isChecked()):
            QMessageBox.information(self, "Jacketize", "Select Dat or Filename source.")
            return
            
        if self._use_dat_chk.isChecked() and not self._dat_data:
            QMessageBox.warning(self, "Jacketize", "No dat loaded. Use 'Load dat' first.")
            return

        ext_str = self._ext_edit.text().lower()
        extensions = [x.strip() for x in ext_str.split(",") if x.strip()]

        worker = JacketizeWorker(
            rom_dir=rom_dir,
            use_dat=self._use_dat_chk.isChecked(),
            use_filename=self._use_filename_chk.isChecked(),
            inverted=self._inverted_title_chk.isChecked(),
            recursive=self._recursive_chk.isChecked(),
            include_all=self._include_all_chk.isChecked(),
            dat_data=self._dat_data,
            extensions=extensions
        )
        
        worker.status.connect(self.set_status)
        worker.finished.connect(lambda: self._on_lib_task_finished(rom_dir))
        
        main_win = self.window()
        if hasattr(main_win, "_settings_tab"):
            worker.progress.connect(main_win._settings_tab.set_progress)

        self._cancel_lib_btn.setEnabled(True)
        self._tasks.start_task("jacketize_process", worker)

    def _on_lib_task_finished(self, rom_dir):
        self._cancel_lib_btn.setEnabled(False)
        self._populate_roms(rom_dir)

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

    def _on_select_emu_clicked(self):
        """Switch to the Emulators tab and select the current emulator."""
        emu_name = self._emu_combo.currentText().strip()
        if not emu_name:
            return

        main_win = self.window()
        # MainWindow defines _tabs and _emulators_tab
        if main_win and hasattr(main_win, "_tabs") and hasattr(main_win, "_emulators_tab"):
            main_win._tabs.setCurrentWidget(main_win._emulators_tab)
            main_win._emulators_tab.select_emulator(emu_name)

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
        
        # Update metadata in SystemRegistry including options and arguments
        if name in self._systems._data:
            entry = self._systems._data[name]
            entry.is_modified = True
            if emu:
                entry.extra_metadata[f"{emu}_opts"] = self._emu_opts_edit.text().strip()
                entry.extra_metadata[f"{emu}_args"] = self._emu_args_edit.text().strip()
        else:
            extra = {}
            if emu:
                extra[f"{emu}_opts"] = self._emu_opts_edit.text().strip()
                extra[f"{emu}_args"] = self._emu_args_edit.text().strip()
            self._systems._data[name] = SystemEntry(
                name=name, 
                extensions=[x.strip() for x in self._ext_edit.text().split(",") if x.strip()],
                extra_metadata=extra,
                is_modified=True
            )

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
            system_name=system_name,
            emu_name=emu_entry.name,
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
