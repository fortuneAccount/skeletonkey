"""
ui/tabs/main_tab.py

MAIN tab – the primary ROM launcher interface.

Mirrors the ":=: MAIN :=:" tab from working.ahk, including:
  - System selector dropdown
  - ROM folder / playlist toggle
  - ROM list with search/filter
  - Core / emulator selector
  - LAUNCH button
  - Per-game options (custom switches, options, arguments)
  - Mini-mode toggle
  - Right-click context menu on ROM list
"""
import re
import subprocess
from pathlib import Path

from PyQt6.QtCore import Qt, QSortFilterProxyModel, QStringListModel, QThread, pyqtSignal
from PyQt6.QtGui import QAction, QFont, QIcon, QColor, QBrush
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QLabel, QPushButton, QComboBox, QLineEdit,
    QListWidget, QListWidgetItem, QCheckBox, QRadioButton,
    QGroupBox, QButtonGroup, QSizePolicy, QMenu,
    QFileDialog, QMessageBox, QFrame, QToolButton,
)

from core.config import global_config
from core.launcher import Launcher, LaunchConfig, suspend_frontends, resume_frontends, check_launch_prerequisites, verify_bios
from data.systems import SystemRegistry
from data.assignments import AssignmentRegistry
from data.launch_params import LaunchParamsRegistry
from data.emulators import EmuRegistry
from data.cores import CoreRegistry
from ui.tabs.base_tab import BaseTab
from utils.paths import app_home


class _LaunchThread(QThread):
    finished = pyqtSignal(int)

    def __init__(self, launcher: Launcher):
        super().__init__()
        self._launcher = launcher

    def run(self):
        suspend_frontends()
        code = self._launcher.launch()
        resume_frontends()
        self.finished.emit(code)


class MainTab(BaseTab):
    """Primary ROM launcher tab."""

    def __init__(self, systems: SystemRegistry, emus: EmuRegistry, assignments: AssignmentRegistry, launch_params: LaunchParamsRegistry, parent=None):
        super().__init__(parent)
        self._cfg = global_config()
        self._systems = systems
        self._assignments = assignments
        self._launch_params = launch_params
        self._emus = emus
        self._cores = self._emus._cores
        self._launch_thread: _LaunchThread | None = None
        self._all_rom_items: list[str] = []
        self._build_ui()
        self._populate_systems()
        self._restore_last()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(4)

        # ── Row 1: System selector + source toggle + mini button ──────
        row1 = QHBoxLayout()

        # System dropdown
        self._system_ddl = QComboBox()
        self._system_ddl.setMinimumWidth(260)
        self._system_ddl.setSizePolicy(QSizePolicy.Policy.Expanding,
                                       QSizePolicy.Policy.Fixed)
        self._system_ddl.currentTextChanged.connect(self._on_system_changed)
        self._system_ddl.setToolTip("Select a playlist file or directory containing ROMs")
        row1.addWidget(self._system_ddl)

        # Edit-system button (mirrors RUNSYSBTN)
        sys_edit_btn = QToolButton()
        sys_edit_btn.setText("E")
        sys_edit_btn.setToolTip("Edit the path or search filters for the selected system")
        sys_edit_btn.clicked.connect(self._edit_system_path)
        row1.addWidget(sys_edit_btn)

        row1.addStretch()

        # Mini-mode toggle (mirrors MINISWITCH)
        self._mini_btn = QPushButton("−")
        self._mini_btn.setFixedSize(22, 22)
        self._mini_btn.setToolTip("Toggle Search panel visibility (Mini-Mode)")
        self._mini_btn.clicked.connect(self._toggle_mini)
        row1.addWidget(self._mini_btn)

        root.addLayout(row1)

        # ── Row 2: ROM path / core selector + LAUNCH ─────────────────
        row2 = QHBoxLayout()

        # Browse ROM button (mirrors GROM)
        browse_btn = QToolButton()
        browse_btn.setText("…")
        browse_btn.setToolTip("Browse for a ROM file")
        browse_btn.clicked.connect(self._browse_rom)
        row2.addWidget(browse_btn)

        # ROM path combo (mirrors MORROM / RUNROMCBX)
        self._rom_cbx = QComboBox()
        self._rom_cbx.setEditable(True)
        self._rom_cbx.setSizePolicy(QSizePolicy.Policy.Expanding,
                                    QSizePolicy.Policy.Fixed)
        self._rom_cbx.setMinimumWidth(300)
        self._rom_cbx.setToolTip("The ROM which will be launched. Supports manual path entry.")
        row2.addWidget(self._rom_cbx)

        # Core / emulator dropdown (mirrors LCORE)
        self._core_ddl = QComboBox()
        self._core_ddl.setMinimumWidth(160)
        self._core_ddl.setToolTip("Select an emulator or RetroArch core to use for the launch")
        self._core_ddl.currentTextChanged.connect(self._on_core_changed)
        row2.addWidget(self._core_ddl)

        # LAUNCH button
        self._launch_btn = QPushButton("LAUNCH")
        self._launch_btn.setMinimumWidth(80)
        self._launch_btn.setMinimumHeight(30)
        bold = QFont()
        bold.setBold(True)
        self._launch_btn.setFont(bold)
        self._launch_btn.setEnabled(False)
        self._launch_btn.setToolTip("Launch the currently displayed ROM\nRight-Click for quick-select presets")
        self._launch_btn.clicked.connect(self._launch)
        row2.addWidget(self._launch_btn)

        # Right-click / more options button (mirrors RCLLNCH)
        more_btn = QToolButton()
        more_btn.setText("▶")
        more_btn.setToolTip("Quick-Launch Presets and folder options")
        more_btn.clicked.connect(self._show_launch_menu)
        row2.addWidget(more_btn)

        root.addLayout(row2)

        # ── Search / filter group (mirrors SRCHGRP) ───────────────────
        search_group = QGroupBox("SEARCH")
        sg_layout = QVBoxLayout(search_group)
        sg_layout.setContentsMargins(4, 8, 4, 4)
        sg_layout.setSpacing(4)

        # Search controls row
        search_ctrl = QHBoxLayout()

        self._recurse_chk = QCheckBox("Recurse")
        self._recurse_chk.setChecked(True)
        self._recurse_chk.setToolTip("Searches subdirectories for matching files")
        search_ctrl.addWidget(self._recurse_chk)

        # Filtered / Unfiltered radio buttons
        self._rad_filtered = QRadioButton("Filtered")
        self._rad_unfiltered = QRadioButton("Unfiltered")
        self._rad_filtered.setChecked(True)
        self._rad_filtered.setToolTip("Respect 'Exclude Systems' list in Settings")
        self._rad_unfiltered.setToolTip("Search all systems and directories")
        
        self._search_filter_grp = QButtonGroup(self)
        self._search_filter_grp.addButton(self._rad_filtered)
        self._search_filter_grp.addButton(self._rad_unfiltered)
        self._search_filter_grp.buttonClicked.connect(self._search_roms)
        
        search_ctrl.addWidget(self._rad_filtered)
        search_ctrl.addWidget(self._rad_unfiltered)

        # Search text input
        self._srch_edit = QLineEdit()
        self._srch_edit.setPlaceholderText("Search ROMs...")
        self._srch_edit.setToolTip("Search for a ROM (no wildcards needed)")
        self._srch_edit.textChanged.connect(self._filter_roms)
        search_ctrl.addWidget(self._srch_edit)

        search_btn = QPushButton("Search")
        search_btn.clicked.connect(self._search_roms)
        search_ctrl.addWidget(search_btn)

        sg_layout.addLayout(search_ctrl)

        # ROM list (mirrors SRCHROMLBX / localromp)
        self._rom_list = QListWidget()
        self._rom_list.setSelectionMode(
            QListWidget.SelectionMode.ExtendedSelection)
        self._rom_list.itemDoubleClicked.connect(self._on_rom_double_clicked)
        self._rom_list.itemSelectionChanged.connect(self._on_selection_changed)
        self._rom_list.setToolTip("Search results. Right-click for options.")
        self._rom_list.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu)
        self._rom_list.customContextMenuRequested.connect(
            self._show_rom_context_menu)
        sg_layout.addWidget(self._rom_list)

        root.addWidget(search_group)

        # ── Custom switches row (mirrors CustSwitchs / CUSTMOPTS) ─────
        self._switches_frame = QFrame()
        sw_layout = QHBoxLayout(self._switches_frame)
        sw_layout.setContentsMargins(0, 0, 0, 0)

        self._cust_switch_chk = QCheckBox("Custom Switches")
        self._cust_switch_chk.setChecked(True)
        self._cust_switch_chk.setToolTip("Enables custom options/arguments replacing [CUSTMOPT] and [CUSTMARG]")
        self._cust_switch_chk.toggled.connect(self._toggle_custom_switches)
        sw_layout.addWidget(self._cust_switch_chk)

        self._cust_opts_cbx = QComboBox()
        self._cust_opts_cbx.setEditable(True)
        self._cust_opts_cbx.setMinimumWidth(140)
        self._cust_opts_cbx.setToolTip("Custom options specified after the executable and before the ROM path")
        self._cust_opts_cbx.setEnabled(True)
        sw_layout.addWidget(self._cust_opts_cbx)

        self._cust_args_cbx = QComboBox()
        self._cust_args_cbx.setEditable(True)
        self._cust_args_cbx.setMinimumWidth(120)
        self._cust_args_cbx.setToolTip("Custom launch arguments")
        self._cust_args_cbx.setEnabled(True)
        sw_layout.addWidget(self._cust_args_cbx)

        sw_layout.addStretch()

        self._reset_switches_btn = QToolButton()
        self._reset_switches_btn.setText("R")
        self._reset_switches_btn.setToolTip("Reset switches to asset defaults")
        self._reset_switches_btn.clicked.connect(self._reset_switches)
        sw_layout.addWidget(self._reset_switches_btn)

        root.addWidget(self._switches_frame)

    # ------------------------------------------------------------------
    # Data population
    # ------------------------------------------------------------------

    def refresh_ui(self):
        """Public entry point for global refreshes."""
        self._systems.reload()
        self._assignments.reload()
        self._emus.reload()
        self._populate_systems()
        current_sys = self._system_ddl.currentText()
        if current_sys and current_sys != ":=:System List:=:":
            self._populate_cores(current_sys)
            self._populate_roms(current_sys)

    def _populate_systems(self):
        self._system_ddl.blockSignals(True)
        self._system_ddl.clear()
        self._system_ddl.addItem(":=:System List:=:")

        all_systems = self._systems.all_systems()
        active = []
        inactive = []

        for name in all_systems:
            entry = self._systems._data.get(name)
            paths = entry.rom_path_list if entry else []
            is_active = any(Path(p).exists() for p in paths)
            
            if is_active and paths:
                active.append(name)
            else:
                inactive.append(name)
        
        # Active systems at top with default font color
        for name in active:
            self._system_ddl.addItem(name)
        
        if active and inactive:
            self._system_ddl.insertSeparator(self._system_ddl.count())
            
        # Inactive systems at bottom, grayed out
        for name in inactive:
            idx = self._system_ddl.count()
            self._system_ddl.addItem(name)
            self._system_ddl.setItemData(idx, QBrush(Qt.GlobalColor.gray), Qt.ItemDataRole.ForegroundRole)
            
        self._system_ddl.blockSignals(False)
        self._search_roms() # Update ROM list based on current filters


    def _populate_cores(self, system: str):
        """Fill the core dropdown for the given system."""
        self._core_ddl.blockSignals(True)
        self._core_ddl.clear()

        apps = self._emus._apps_cfg
        apps.reload()

        # 1. Separate installed items to prevent name collisions
        inst_emus = {}
        for name, path in apps.items("EMULATORS"):
            p_str = path.strip('"')
            if p_str and Path(p_str).exists():
                inst_emus[name.lower()] = name
        
        inst_cores = {}
        for name, path in apps.items("CORES"):
            p_str = path.strip('"')
            if p_str and Path(p_str).exists():
                inst_cores[name.lower()] = name

        # 2. Gather candidates with explicit type tracking
        assigned = self._assignments.get_assignment(system)
        sys_entry = self._systems._data.get(system)

        # Build candidates list: (display_name, is_core)
        candidates = []
        
        # User assignments
        for name in reversed(assigned.emulators):
            low = name.lower()
            if low in inst_emus:
                candidates.append((inst_emus[low], False))
            
            # Robust core matching for assignments
            match = None
            for k in [low, low.replace("_libretro", ""), f"{low}_libretro"]:
                if k in inst_cores:
                    match = inst_cores[k]
                    break
            if not match:
                for k, real_name in inst_cores.items():
                    if low in k or k in low:
                        match = real_name
                        break
            if match:
                candidates.append((match, True))

        if sys_entry:
            # Preferred emulator/core (emu_reset)
            if sys_entry.emu_reset:
                low = sys_entry.emu_reset.lower()
                if low in inst_emus: candidates.append((inst_emus[low], False))
                for k in [low, low.replace("_libretro", ""), f"{low}_libretro"]:
                    if k in inst_cores:
                        candidates.append((inst_cores[k], True))
                        break
            
            # Supported emulators
            for name in sys_entry.supported_emus:
                low = name.lower()
                if low in inst_emus:
                    candidates.append((inst_emus[low], False))
            
            # Supported cores
            for name in sys_entry.supported_cores:
                low = name.lower()
                match = None
                for k in [low, low.replace("_libretro", ""), f"{low}_libretro"]:
                    if k in inst_cores:
                        match = inst_cores[k]
                        break
                if not match:
                    for k, real_name in inst_cores.items():
                        if low in k or k in low:
                            match = real_name
                            break
                if match:
                    candidates.append((match, True))

        # 3. Filter duplicates and cleanup tracking maps
        associated_names = []
        seen = set()

        def _remove_inst(name, is_core):
            name_low = name.lower()
            if is_core:
                # Clean up inst_cores more aggressively to handle substring matches
                keys_to_del = [k for k in inst_cores if k == name_low or k in name_low or name_low in k]
                for k in keys_to_del: inst_cores.pop(k, None)
            else:
                inst_emus.pop(name_low, None)

        for name, is_core in candidates:
            key = (name.lower(), is_core)
            if key not in seen:
                associated_names.append((name, is_core))
                seen.add(key)
                _remove_inst(name, is_core)

        # 4. Populate Dropdown: Associated (Top)
        for n, is_core in associated_names:
            self._core_ddl.addItem(n)
            idx = self._core_ddl.count() - 1
            self._core_ddl.setItemData(idx, is_core, Qt.ItemDataRole.UserRole)
            self._apply_bios_color(n, verify_bios(n, system, app_home()), is_core=is_core)

        if associated_names and (inst_emus or inst_cores):
            self._core_ddl.insertSeparator(self._core_ddl.count())

        # 5. Populate Dropdown: Other Installed (Bottom, grayed out)
        for low_name in sorted(inst_emus.keys()):
            n = inst_emus[low_name]
            self._core_ddl.addItem(n)
            idx = self._core_ddl.count() - 1
            self._core_ddl.setItemData(idx, False, Qt.ItemDataRole.UserRole)
            self._core_ddl.setItemData(idx, QBrush(Qt.GlobalColor.gray), Qt.ItemDataRole.ForegroundRole)

        for low_name in sorted(inst_cores.keys()):
            n = inst_cores[low_name]
            self._core_ddl.addItem(n)
            idx = self._core_ddl.count() - 1
            self._core_ddl.setItemData(idx, True, Qt.ItemDataRole.UserRole)
            self._apply_bios_color(n, verify_bios(n, system, app_home()), is_core=True)
            self._core_ddl.setItemData(idx, QBrush(Qt.GlobalColor.gray), Qt.ItemDataRole.ForegroundRole)

        # Select last used emulator if available in user metadata
        if sys_entry:
            last_emu = sys_entry.extra_metadata.get("LAST_EMU")
            if last_emu:
                idx = self._core_ddl.findText(last_emu)
                if idx >= 0:
                    self._core_ddl.setCurrentIndex(idx)

        self._core_ddl.blockSignals(False)

    def _apply_bios_color(self, emu_name: str, status, is_core: bool = False):
        """Apply color coding based on BIOS status."""
        if not status:
            return
        
        idx = self._core_ddl.count() - 1
        
        if is_core:
            # Cores are blue
            self._core_ddl.setItemData(idx, QBrush(Qt.GlobalColor.blue), Qt.ItemDataRole.ForegroundRole)

        if status.errors:
            # Red for hash mismatches/errors
            self._core_ddl.setItemData(idx, QBrush(QColor(255, 100, 100)), Qt.ItemDataRole.BackgroundRole)
        elif status.missing:
            # Yellow/orange for missing BIOS
            self._core_ddl.setItemData(idx, QBrush(QColor(255, 200, 100)), Qt.ItemDataRole.BackgroundRole)
        # Green (default) for present/OK

    def _populate_roms(self, system: str | None = None):
        """Load ROM list. If no system provided, perform a global search."""
        self._rom_list.clear()
        self._all_rom_items.clear()

        search_term = self._srch_edit.text().strip().lower()
        is_filtered = self._rad_filtered.isChecked()
        excluded_str = self._cfg.get("GLOBAL", "exclude_systems", fallback="")
        excluded = [s.strip().lower() for s in excluded_str.split("|") if s.strip()] if is_filtered else []
        
        # If search is global (no specific system), iterate through all known systems
        systems_to_scan = [system] if system else self._systems.all_systems()
        
        recurse = self._recurse_chk.isChecked()
        pattern = "**/*" if recurse else "*"
        found_count = 0 
        
        for sys_name in systems_to_scan:
            if system is None and sys_name.lower() in excluded:
                continue
                
            entry = self._systems._data.get(sys_name)
            if not entry or not entry.rom_paths: continue
            
            valid_paths = [Path(p) for p in entry.rom_path_list if Path(p).exists()]
            
            for base_p in valid_paths:
                for f in base_p.glob(pattern):
                    if not f.is_file(): continue
                    
                    # Filter by search term and extensions
                    if search_term and search_term not in f.name.lower():
                        continue
                        
                    if entry.extensions:
                        ext = f.suffix.lower().lstrip('.')
                        if ext not in [e.lower() for e in entry.extensions]:
                            continue
                    display = str(f.relative_to(base_p)) if recurse else f.name
                    item = QListWidgetItem(f"[{sys_name}] {display}" if not system else display)
                    item.setData(Qt.ItemDataRole.UserRole, str(f))
                    item.setData(Qt.ItemDataRole.UserRole + 1, sys_name) # Store source system
                    self._rom_list.addItem(item)
                    found_count += 1


    def _restore_last(self):
        """Restore last used system and ROM from config."""
        last_sys = self._cfg.get("GLOBAL", "last_system", fallback="")
        if last_sys:
            # Case-insensitive restoration
            last_sys_low = last_sys.lower()
            idx = -1
            for i in range(self._system_ddl.count()):
                if self._system_ddl.itemText(i).lower() == last_sys_low:
                    idx = i; break
            if idx >= 0:
                self._system_ddl.setCurrentIndex(idx)

        last_rom = self._cfg.get("GLOBAL", "last_rom", fallback="")
        if last_rom:
            self._rom_cbx.setCurrentText(last_rom)

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------

    def _on_system_changed(self, system: str):
        if not system or system == ":=:System List:=:":
            self._launch_btn.setEnabled(False)
            return
        self._populate_cores(system)
        self._populate_roms(system)
        self._update_switches(system)
        self._cfg.set("GLOBAL", "last_system", system)

    def _on_rom_double_clicked(self, item: QListWidgetItem):
        self._set_rom_from_item(item)
        self._launch()

    def _on_selection_changed(self):
        items = self._rom_list.selectedItems()
        if items:
            self._set_rom_from_item(items[0])
            self._launch_btn.setEnabled(True)
        else:
            self._launch_btn.setEnabled(False)

    def _on_core_changed(self, emu_name: str):
        """Sync custom switches when the emulator or core selection changes."""
        if not emu_name or "Separator" in emu_name: return
        system = self._system_ddl.currentText()
        if not system or system == ":=:System List:=:":
            return
        self._update_switches(system)

    def _update_switches(self, system: str):
        """Populate Custom Switches boxes with registry data for the system/emu."""
        # Block signals and clear existing items to allow multi-select logic
        self._cust_opts_cbx.blockSignals(True)
        self._cust_args_cbx.blockSignals(True)
        self._cust_opts_cbx.clear()
        self._cust_args_cbx.clear()

        def split_val(v):
            if not v: return []
            # Metadata uses < or | as item delimiters
            if isinstance(v, list):
                return [str(x) for x in v if str(x)]
            return [x for x in re.split(r'[<|\n]', str(v)) if x]

        opts_list = []
        args_list = []

        # 1. Check for system-specific overrides in LaunchParams
        lp = self._launch_params.get(system)
        if lp:
            opts_list.extend(split_val(getattr(lp, 'options', getattr(lp, 'opts', ""))))
            args_list.extend(split_val(getattr(lp, 'arguments', getattr(lp, 'args', ""))))
        
        idx = self._core_ddl.currentIndex()
        is_core = self._core_ddl.itemData(idx, Qt.ItemDataRole.UserRole)
        emu_name = self._core_ddl.currentText()

        # 1.5 Check SystemRegistry for emulator-specific options (Assets or User)
        m_opts, m_args = self._systems.get_emu_metadata(system, emu_name)
        opts_list.extend(m_opts)
        args_list.extend(m_args)

        # 2. Fallback to the selected emulator's global defaults if overrides are empty
        if not opts_list and not args_list and emu_name:
            if not is_core:
                emu_entry = self._emus.get(emu_name)
                if emu_entry:
                    opts_list.extend(split_val(getattr(emu_entry, 'options', getattr(emu_entry, 'opts', ""))))
                    args_list.extend(split_val(getattr(emu_entry, 'arguments', getattr(emu_entry, 'args', ""))))
            else:
                # 3. Check Core Registry if still empty (likely a RetroArch core)
                core_entry = self._cores.get(emu_name)
                if core_entry:
                    opts_list.extend(split_val(core_entry.get("options", "")))
                    args_list.extend(split_val(core_entry.get("arguments", "")))

        # Deduplicate while preserving priority order
        def dedup(seq):
            seen = set()
            return [x for x in seq if not (x in seen or seen.add(x))]

        self._cust_opts_cbx.addItems(dedup(opts_list))
        self._cust_args_cbx.addItems(dedup(args_list))

        # Initialize default selection
        if self._cust_opts_cbx.count() > 0:
            self._cust_opts_cbx.setCurrentIndex(0)
        else:
            self._cust_opts_cbx.setCurrentText("")

        if self._cust_args_cbx.count() > 0:
            self._cust_args_cbx.setCurrentIndex(0)
        else:
            self._cust_args_cbx.setCurrentText("")
        
        self._cust_opts_cbx.blockSignals(False)
        self._cust_args_cbx.blockSignals(False)

    def _set_rom_from_item(self, item: QListWidgetItem):
        path = item.data(Qt.ItemDataRole.UserRole) or item.text()
        sys_source = item.data(Qt.ItemDataRole.UserRole + 1)
        self._rom_cbx.setCurrentText(path)
        if sys_source and sys_source != self._system_ddl.currentText():
            # Case-insensitive system selection
            sys_source_low = sys_source.lower()
            idx = -1
            for i in range(self._system_ddl.count()):
                if self._system_ddl.itemText(i).lower() == sys_source_low:
                    idx = i; break
            if idx >= 0: self._system_ddl.setCurrentIndex(idx)
        self._cfg.set("GLOBAL", "last_rom", path)

    def _filter_roms(self, text: str):
        """Live-filter the ROM list by search text."""
        text = text.lower()
        for i in range(self._rom_list.count()):
            item = self._rom_list.item(i)
            item.setHidden(text not in item.text().lower())

    def _search_roms(self):
        self._populate_roms(None) # Global search

    def _reset_switches(self):
        """Clear manual overrides and revert to asset-defined switches."""
        system = self._system_ddl.currentText()
        if not system or system == ":=:System List:=:":
            return

        # Remove override from LaunchParams to trigger fallback to metadata
        if system in self._launch_params._data:
            del self._launch_params._data[system]
            self._launch_params.save()

        self._update_switches(system)
        self.set_status(f"Reset custom switches for {system}")

    def _browse_rom(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select ROM", "", "All Files (*)")
        if path:
            self._rom_cbx.setCurrentText(path)
            self._launch_btn.setEnabled(True)

    def _edit_system_path(self):
        system = self._system_ddl.currentText()
        if not system or system == ":=:System List:=:":
            return
        current = self._systems.get_path(system) or str(Path.home())
        chosen = QFileDialog.getExistingDirectory(
            self, f"ROM Directory for {system}", current)
        if chosen:
            self._systems.set_path(system, chosen)
            self._systems.save()
            self._populate_roms(system)
            self.set_status(f"Updated ROM path for {system}")

    def _toggle_custom_switches(self, enabled: bool):
        self._cust_opts_cbx.setEnabled(enabled)
        self._cust_args_cbx.setEnabled(enabled)

    def _toggle_mini(self):
        """Collapse/expand the search group (mini-mode)."""
        is_mini = False
        for child in self.findChildren(QGroupBox):
            if child.title() == "SEARCH":
                child.setVisible(not child.isVisible())
                is_mini = not child.isVisible()
                self._mini_btn.setText("+" if is_mini else "−")
                break

        main_win = self.window()
        if main_win and hasattr(main_win, "set_mini_mode"):
            main_win.set_mini_mode(is_mini)

    # ------------------------------------------------------------------
    # Launch
    # ------------------------------------------------------------------

    def _launch(self):
        rom_text = self._rom_cbx.currentText().strip()
        if not rom_text:
            QMessageBox.information(self, "No ROM", "Select a ROM first.")
            return
            
        # Resolve to absolute path to ensure RetroArch and emulators find the file
        rom_path = str(Path(rom_text).absolute())

        system = self._system_ddl.currentText()
        emu_name = self._core_ddl.currentText().strip()

        # Check BIOS prerequisites before launch
        can_launch, warnings = check_launch_prerequisites(emu_name, system)
        if not can_launch:
            response = QMessageBox.StandardButton.No
            if warnings:
                response = QMessageBox.warning(
                    self, "Missing BIOS/Firmware",
                    "The following issues were detected:\n\n" + 
                    "\n".join(warnings) + 
                    "\n\nDo you want to continue anyway?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
                )
            if response == QMessageBox.StandardButton.No:
                return

        apps = self._emus._apps_cfg
        emu_entry = self._emus.get(emu_name)
        lp = self._launch_params.get(system) if system else None
        options = (self._cust_opts_cbx.currentText()
                   if self._cust_switch_chk.isChecked() else "")
        arguments = (self._cust_args_cbx.currentText()
                     if self._cust_switch_chk.isChecked() else "")

        idx = self._core_ddl.currentIndex()
        is_core = self._core_ddl.itemData(idx, Qt.ItemDataRole.UserRole)

        # Priority 1: Check if selection is explicitly a RetroArch core
        core_path = apps.get("CORES", emu_name).strip('"') if is_core else ""
        if is_core and core_path and Path(core_path).exists():
            ra_exe = apps.get("EMULATORS", "retroArch").strip('"') or \
                     apps.get("EMULATORS", "retroarch").strip('"')
            
            if not ra_exe or not Path(ra_exe).exists():
                ra_exe = self._cfg.get("EMULATORS", "retroarch").strip('"')
            
            if not ra_exe or not Path(ra_exe).exists():
                QMessageBox.warning(self, "RetroArch Missing", "RetroArch executable not found. Cannot launch core.")
                return
            
            emu_exe = ra_exe
            emu_dir = Path(ra_exe).parent
            # Ensure core path is absolute and quoted for RetroArch
            options = f'-L "{Path(core_path).absolute()}" {options}'.strip()
        else:
            # Priority 2: Standalone emulator (either detected or local fallback)
            emu_exe = apps.get("EMULATORS", emu_name).strip('"')
            if emu_exe and Path(emu_exe).exists():
                emu_dir = Path(emu_exe).parent
            elif emu_entry and emu_entry.exe:
                emu_dir = app_home() / "Emulators" / emu_entry.name
                emu_exe = str(emu_dir / emu_entry.exe)

        if not emu_exe or not Path(emu_exe).exists():
            QMessageBox.warning(self, "No Emulator", 
                                f"No executable or core found for '{emu_name}'.\n"
                                "Check the Emulators tab.")
            return

        cfg = LaunchConfig(
            emulator_path=emu_exe,
            rom_path=rom_path,
            options=options,
            arguments=arguments,
            include_extension=lp.extract if lp else True,
            include_path=lp.runrom if lp else True,
            working_dir=str(emu_dir),
        )

        # Update system metadata with current configuration for next run
        sys_entry = self._systems._data.get(system)
        if sys_entry:
            sys_entry.extra_metadata["LAST_EMU"] = emu_name
            sys_entry.extra_metadata[f"{emu_name}_opts"] = options
            sys_entry.extra_metadata[f"{emu_name}_args"] = arguments
            self._systems.save()

        launcher = Launcher(cfg)
        self._launch_thread = _LaunchThread(launcher)
        self._launch_thread.finished.connect(self._on_launch_finished)
        self._launch_thread.start()
        self._launch_btn.setEnabled(False)
        
        rom_p = Path(rom_path)
        emu_p = Path(emu_exe)
        cmd_display = f"#{rom_p.parent}/{emu_p.parent}>{emu_p.name} {options} {rom_p.parent}\\\\{rom_p.name} {arguments}".strip()
        self.set_status(cmd_display)

    def _on_launch_finished(self, exit_code: int):
        self._launch_btn.setEnabled(True)
        self.set_status(f"Emulator exited (code {exit_code})")

    # ------------------------------------------------------------------
    # Context menus
    # ------------------------------------------------------------------

    def _show_rom_context_menu(self, pos):
        item = self._rom_list.itemAt(pos)
        menu = QMenu(self)

        run_act = QAction("Run With…", self)
        run_act.triggered.connect(self._launch)
        menu.addAction(run_act)

        menu.addSeparator()

        add_pl_act = QAction("Add to Playlist +", self)
        add_pl_act.setEnabled(item is not None)
        menu.addAction(add_pl_act)

        open_act = QAction("Open in Explorer", self)
        open_act.setEnabled(item is not None)
        open_act.triggered.connect(lambda: self._open_in_explorer(item))
        menu.addAction(open_act)

        menu.addSeparator()

        del_cfg_act = QAction("Delete Game Settings", self)
        del_cfg_act.setEnabled(item is not None)
        menu.addAction(del_cfg_act)

        menu.exec(self._rom_list.mapToGlobal(pos))

    def _show_launch_menu(self):
        """Show the quick-launch / presets menu."""
        menu = QMenu(self)
        
        open_folder_act = QAction("Open ROM Folder", self)
        open_folder_act.triggered.connect(lambda: self._open_in_explorer(None))
        menu.addAction(open_folder_act)

        open_emu_act = QAction("Open Emulator Folder", self)
        open_emu_act.triggered.connect(self._open_emulator_folder)
        menu.addAction(open_emu_act)
        
        menu.addSeparator()
        # Placeholder for presets
        menu.addAction("Clean Temp Files").setEnabled(False)

        menu.exec(self._launch_btn.mapToGlobal(self._launch_btn.rect().bottomLeft()))

    def _open_emulator_folder(self):
        """Open the directory of the currently selected emulator."""
        emu_name = self._core_ddl.currentText()
        if not emu_name:
            return
        entry = self._emus.get(emu_name)
        if entry:
            emu_dir = app_home() / "Emulators" / entry.name
            if emu_dir.exists():
                import os
                os.startfile(emu_dir)

    def _open_in_explorer(self, item: QListWidgetItem | None):
        """Open the file or system directory in the OS file explorer."""
        import os
        if item:
            path = Path(item.data(Qt.ItemDataRole.UserRole) or item.text())
        else:
            system = self._system_ddl.currentText()
            entry = self._systems._data.get(system)
            path = Path(entry.rom_path_list[0]) if entry and entry.rom_path_list and Path(entry.rom_path_list[0]).exists() else Path(".")
            
        if path.exists():
            if path.is_file():
                subprocess.run(['explorer', '/select,', str(path)])
            else:
                os.startfile(path)