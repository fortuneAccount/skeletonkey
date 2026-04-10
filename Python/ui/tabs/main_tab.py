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

    def __init__(self, parent=None):
        super().__init__(parent)
        self._cfg = global_config()
        self._systems = SystemRegistry()
        self._assignments = AssignmentRegistry()
        self._launch_params = LaunchParamsRegistry()
        self._emus = EmuRegistry()
        self._cores = CoreRegistry()
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

        # Playlist / Folder radio toggle
        self._rad_playlist = QRadioButton("Playlist")
        self._rad_folder = QRadioButton("Folder")
        self._rad_folder.setChecked(True)
        src_group = QButtonGroup(self)
        src_group.addButton(self._rad_playlist)
        src_group.addButton(self._rad_folder)
        self._rad_playlist.toggled.connect(self._on_source_toggled)
        row1.addWidget(self._rad_playlist)
        row1.addWidget(self._rad_folder)

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

        self._srch_rad_folder = QRadioButton("Folder")
        self._srch_rad_playlist = QRadioButton("Playlist")
        self._srch_rad_folder.setChecked(True)
        srch_grp = QButtonGroup(self)
        srch_grp.addButton(self._srch_rad_folder)
        srch_grp.addButton(self._srch_rad_playlist)
        search_ctrl.addWidget(self._srch_rad_folder)
        search_ctrl.addWidget(self._srch_rad_playlist)

        # Location dropdown (mirrors SRCHLOCDL)
        self._srch_loc_ddl = QComboBox()
        self._srch_loc_ddl.setMinimumWidth(200)
        self._srch_loc_ddl.setToolTip("Target playlist or system directory to search within")
        search_ctrl.addWidget(self._srch_loc_ddl)

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
        self._cust_switch_chk.setToolTip("Enables custom options/arguments replacing [CUSTMOPT] and [CUSTMARG]")
        self._cust_switch_chk.toggled.connect(self._toggle_custom_switches)
        sw_layout.addWidget(self._cust_switch_chk)

        self._cust_opts_cbx = QComboBox()
        self._cust_opts_cbx.setEditable(True)
        self._cust_opts_cbx.setMinimumWidth(140)
        self._cust_opts_cbx.setToolTip("Custom options specified after the executable and before the ROM path")
        self._cust_opts_cbx.setEnabled(False)
        sw_layout.addWidget(self._cust_opts_cbx)

        self._cust_args_cbx = QComboBox()
        self._cust_args_cbx.setEditable(True)
        self._cust_args_cbx.setMinimumWidth(120)
        self._cust_args_cbx.setToolTip("Custom launch arguments")
        self._cust_args_cbx.setEnabled(False)
        sw_layout.addWidget(self._cust_args_cbx)

        sw_layout.addStretch()

        # Auto-launch checkbox (mirrors AUTOLNCH)
        self._auto_launch_chk = QCheckBox("Auto-Launch")
        self._auto_launch_chk.setChecked(
            self._cfg.get("GLOBAL", "auto_exec", fallback="0") == "1")
        self._auto_launch_chk.setToolTip("Automatically launch ROMs when dropped or selected")
        self._auto_launch_chk.toggled.connect(self._save_auto_launch)
        sw_layout.addWidget(self._auto_launch_chk)

        root.addWidget(self._switches_frame)

    # ------------------------------------------------------------------
    # Data population
    # ------------------------------------------------------------------

    def refresh_ui(self):
        """Public entry point for global refreshes."""
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
            path_raw = self._systems.get_path(name)
            paths = [p.strip() for p in path_raw.split('|') if p.strip()]
            is_active = any(Path(p).exists() for p in paths)
            
            if is_active and path_raw.strip():
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

        # Mirror system list into search location dropdown
        self._srch_loc_ddl.clear()
        self._srch_loc_ddl.addItem(":=:System List:=:")
        for name in self._systems.all_systems():
            # ONLY populate search with identified/active systems
            path_raw = self._systems.get_path(name)
            paths = [p.strip() for p in path_raw.split('|') if p.strip()]
            if any(Path(p).exists() for p in paths):
                self._srch_loc_ddl.addItem(name)

    def _populate_cores(self, system: str):
        """Fill the core dropdown for the given system."""
        self._core_ddl.blockSignals(True)
        self._core_ddl.clear()

        assigned = self._assignments.get_assignment(system)
        if not assigned:
            assigned = ""

        # Handle pipe-delimited assignments - prioritize last (invert)
        # e.g., "CoreA|CoreB" means CoreB is preferred, CoreA is fallback
        pipe_parts = [p.strip() for p in assigned.split('|') if p.strip()]
        associated = list(reversed(pipe_parts)) if pipe_parts else []

        # Get all installed emulators
        installed = [e.name for e in self._emus.get_installed_executables("emulator")]
        
        # Try both "retroArch" (as in apps.ini) and "retroarch"
        ra_path = self._emus._apps_cfg.get("EMULATORS", "retroArch")
        if not ra_path:
            ra_path = self._cfg.get("EMULATORS", "retroarch")
        if ra_path and Path(ra_path).exists():
            cores_dir = Path(ra_path).parent / "cores"
            if cores_dir.exists():
                # Get core names without _libretro.dll suffix
                for f in cores_dir.glob("*_libretro.dll"):
                    core_name = f.stem.replace("_libretro", "")
                    if core_name not in installed:
                        installed.append(core_name)

        installed = list(set(installed))
        
        # Get BIOS status for each emulator to use for color coding
        bios_status_by_emu = {}
        for n in installed:
            status = verify_bios(n, system, app_home())
            bios_status_by_emu[n] = status

        # 1. Associated emulators at the top with default font color
        valid_assoc = [n for n in associated if n in installed]
        for n in valid_assoc:
            self._core_ddl.addItem(n)
            self._apply_bios_color(n, bios_status_by_emu.get(n))
            if n in installed:
                installed.remove(n)
            
        if valid_assoc and installed:
            self._core_ddl.insertSeparator(self._core_ddl.count())

        # 2. Others below in gray (not associated)
        remaining = sorted(installed)
        for n in remaining:
            idx = self._core_ddl.count()
            self._core_ddl.addItem(n)
            self._apply_bios_color(n, bios_status_by_emu.get(n))
            self._core_ddl.setItemData(idx, QBrush(Qt.GlobalColor.gray), Qt.ItemDataRole.ForegroundRole)

        self._core_ddl.blockSignals(False)

    def _apply_bios_color(self, emu_name: str, status):
        """Apply color coding based on BIOS status."""
        if not status:
            return
        
        idx = self._core_ddl.count() - 1
        
        if status.errors:
            # Red for hash mismatches/errors
            self._core_ddl.setItemData(idx, QBrush(QColor(255, 100, 100)), Qt.ItemDataRole.BackgroundRole)
        elif status.missing:
            # Yellow/orange for missing BIOS
            self._core_ddl.setItemData(idx, QBrush(QColor(255, 200, 100)), Qt.ItemDataRole.BackgroundRole)
        # Green (default) for present/OK

    def _populate_roms(self, system: str):
        """Load ROM list for the selected system."""
        self._rom_list.clear()
        self._all_rom_items.clear()
        self._srch_edit.clear()

        rom_dir = self._systems.get_path(system)
        if not rom_dir:
            return

        p = Path(rom_dir)
        if not p.exists():
            self.set_status(f"ROM directory not found: {rom_dir}")
            return

        recurse = self._recurse_chk.isChecked()
        pattern = "**/*" if recurse else "*"
        files = sorted(f for f in p.glob(pattern) if f.is_file())

        for f in files:
            display = str(f.relative_to(p)) if recurse else f.name
            self._all_rom_items.append(str(f))
            item = QListWidgetItem(display)
            item.setData(Qt.ItemDataRole.UserRole, str(f))
            self._rom_list.addItem(item)

        self.set_status(f"{len(files)} ROMs found in {system}")

    def _restore_last(self):
        """Restore last used system and ROM from config."""
        last_sys = self._cfg.get("GLOBAL", "last_system", fallback="")
        if last_sys:
            idx = self._system_ddl.findText(last_sys)
            if idx >= 0:
                self._system_ddl.setCurrentIndex(idx)

        last_rom = self._cfg.get("GLOBAL", "last_rom", fallback="")
        if last_rom:
            self._rom_cbx.setCurrentText(last_rom)

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------

    def _on_source_toggled(self, checked: bool):
        system = self._system_ddl.currentText()
        if system and system != ":=:System List:=:":
            self._populate_roms(system)

    def _on_system_changed(self, system: str):
        if not system or system == ":=:System List:=:":
            self._launch_btn.setEnabled(False)
            return
        self._populate_cores(system)
        self._populate_roms(system)
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

    def _set_rom_from_item(self, item: QListWidgetItem):
        path = item.data(Qt.ItemDataRole.UserRole) or item.text()
        self._rom_cbx.setCurrentText(path)
        self._cfg.set("GLOBAL", "last_rom", path)

    def _filter_roms(self, text: str):
        """Live-filter the ROM list by search text."""
        text = text.lower()
        for i in range(self._rom_list.count()):
            item = self._rom_list.item(i)
            item.setHidden(text not in item.text().lower())

    def _search_roms(self):
        self._filter_roms(self._srch_edit.text())

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

    def _save_auto_launch(self, checked: bool):
        self._cfg.set("GLOBAL", "auto_exec", "1" if checked else "0")
        self._cfg.save()

    def _toggle_mini(self):
        """Collapse/expand the search group (mini-mode)."""
        grp = self.findChild(QGroupBox, "")
        # Walk children to find the search group
        for child in self.findChildren(QGroupBox):
            if child.title() == "SEARCH":
                child.setVisible(not child.isVisible())
                self._mini_btn.setText("+" if not child.isVisible() else "−")
                break

    # ------------------------------------------------------------------
    # Launch
    # ------------------------------------------------------------------

    def _launch(self):
        rom_path = self._rom_cbx.currentText().strip()
        if not rom_path:
            QMessageBox.information(self, "No ROM", "Select a ROM first.")
            return

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

        emu_entry = self._emus.get(emu_name)
        if not emu_entry or not emu_entry.exe:
            QMessageBox.warning(
                self, "No Emulator",
                f"No executable found for '{emu_name}'.\n"
                "Check the Emulators tab.")
            return

        emu_dir = app_home() / "Emulators" / emu_entry.name
        emu_exe = str(emu_dir / emu_entry.exe)

        lp = self._launch_params.get(system) if system else None
        options = (self._cust_opts_cbx.currentText().strip()
                   if self._cust_switch_chk.isChecked() else "")
        arguments = (self._cust_args_cbx.currentText().strip()
                     if self._cust_switch_chk.isChecked() else "")

        cfg = LaunchConfig(
            emulator_path=emu_exe,
            rom_path=rom_path,
            options=options,
            arguments=arguments,
            include_extension=lp.extract if lp else True,
            include_path=lp.runrom if lp else True,
            working_dir=str(emu_dir),
        )
        launcher = Launcher(cfg)
        self._launch_thread = _LaunchThread(launcher)
        self._launch_thread.finished.connect(self._on_launch_finished)
        self._launch_thread.start()
        self._launch_btn.setEnabled(False)
        self.set_status(f"Launching {Path(rom_path).name} with {emu_name}…")

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

        me
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
            path = Path(self._systems.get_path(system).split('|')[0])
            
        if path.exists():
            if path.is_file():
                subprocess.run(['explorer', '/select,', str(path)])
            else:
                os.startfile(path)