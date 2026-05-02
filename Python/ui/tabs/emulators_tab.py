"""
ui/tabs/emulators_tab.py

Emulators tab – browse, download and configure emulators.
"""
from pathlib import Path

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QListWidget, QLabel, QPushButton, QLineEdit,
    QGroupBox, QFormLayout, QProgressBar, QMessageBox, QMenu,
    QComboBox, QPlainTextEdit, QFileDialog, QToolButton
)

from core.config import global_config
from core.downloader import DownloadWorker
from data.emulators import EmuRegistry, EmuEntry
from data.systems import SystemRegistry
from ui.tabs.base_tab import BaseTab
from ui.tabs.settings_tab import _PathCombo
from utils.paths import app_home, bin_dir, resolve_arch, check_paths_exist, app_root


class EmulatorsTab(BaseTab):
    def __init__(self, systems: SystemRegistry, emus: EmuRegistry, parent=None):
        super().__init__(parent)
        self._cfg = global_config()
        self._emus = emus
        self._systems = systems
        self._active_worker: DownloadWorker | None = None
        self._build_ui()
        self._populate()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _build_ui(self):
        root = QHBoxLayout(self)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        root.addWidget(splitter)

        # Left: emulator list
        left = QWidget()
        ll = QVBoxLayout(left)
        
        search_layout = QHBoxLayout()
        self._detect_btn = QPushButton("Detect")
        self._detect_btn.setFixedWidth(50)
        self._detect_btn.setToolTip("Scan drives for installed emulators")
        self._detect_btn.clicked.connect(self._on_detect_clicked)
        
        self._emu_search = QLineEdit()
        self._emu_search.setPlaceholderText("Filter emulators...")
        self._emu_search.textChanged.connect(self._filter_emus)

        self._clear_search_btn = QToolButton()
        self._clear_search_btn.setText("x")
        self._clear_search_btn.clicked.connect(self._clear_search)

        self._filter_detected_btn = QToolButton()
        self._filter_detected_btn.setText("Y")
        self._filter_detected_btn.setCheckable(True)
        self._filter_detected_btn.setToolTip("Show detected emulators only")
        self._filter_detected_btn.clicked.connect(self._on_filter_detected_toggled)

        self._filter_missing_preset_btn = QToolButton()
        self._filter_missing_preset_btn.setText("N")
        self._filter_missing_preset_btn.setCheckable(True)
        self._filter_missing_preset_btn.setToolTip("Show emulators missing for detected systems (EMUPRESET)")
        self._filter_missing_preset_btn.clicked.connect(self._on_filter_missing_preset_toggled)

        search_layout.addWidget(self._detect_btn)
        search_layout.addWidget(self._emu_search)
        search_layout.addWidget(self._clear_search_btn)
        search_layout.addWidget(self._filter_detected_btn)
        search_layout.addWidget(self._filter_missing_preset_btn)
        ll.addLayout(search_layout)

        self._emu_list = QListWidget()
        self._emu_list.currentItemChanged.connect(lambda cur, prev: self._on_emu_selected(cur.text() if cur else ""))
        self._emu_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._emu_list.customContextMenuRequested.connect(self._show_context_menu)
        
        ll.addWidget(self._emu_list)
        splitter.addWidget(left)

        # Right: details + actions
        right = QWidget()
        rl = QVBoxLayout(right)

        info = QGroupBox("Emulator Details")
        form = QFormLayout(info)
        self._name_edit = QLineEdit()
        self._exe_label_edit = QLineEdit() # Internal filename
        self._archive_edit = QLineEdit()
        form.addRow("Name:", self._name_edit)
        form.addRow("Internal EXE:", self._exe_label_edit)
        form.addRow("Archive/Repo URL:", self._archive_edit)
        rl.addWidget(info)

        # New Management Fields
        self._exe_path_edit = QLineEdit()
        self._exe_path_edit.setPlaceholderText("Full path to executable...")
        exe_row = QHBoxLayout()
        exe_row.addWidget(self._exe_path_edit)
        exe_browse = QPushButton("Browse...")
        exe_browse.clicked.connect(self._browse_exe)
        exe_row.addWidget(exe_browse)
        form.addRow("Executable Path:", exe_row)

        self._install_path = QLineEdit()
        self._install_path.setPlaceholderText("Installation directory...")
        form.addRow("Install Dir:", self._install_path)

        self._config_path_combo = _PathCombo("Configs")
        form.addRow("Config Paths:", self._config_path_combo)

        self._opts_combo = QComboBox()
        self._opts_combo.setEditable(True)
        form.addRow("Default Options:", self._opts_combo)

        self._args_combo = QComboBox()
        self._args_combo.setEditable(True)
        form.addRow("Default Arguments:", self._args_combo)

        self._ext_edit = QLineEdit()
        form.addRow("Supported Ext:", self._ext_edit)

        self._req_files_edit = QPlainTextEdit()
        self._req_files_edit.setMaximumHeight(60)
        form.addRow("Requirements:", self._req_files_edit)

        # Action Buttons
        act_row = QHBoxLayout()
        self._save_btn = QPushButton("Save Assignment")
        self._save_btn.clicked.connect(self._save_emu_path)
        self._audit_btn = QPushButton("Audit BIOSes")
        self._audit_btn.clicked.connect(self._audit_bioses)

        act_row.addWidget(self._save_btn)
        act_row.addWidget(self._audit_btn)
        rl.addLayout(act_row)

        # Arch selector
        arch_row = QHBoxLayout()
        arch_row.addWidget(QLabel("Architecture:"))
        self._arch_combo = QComboBox()
        self._arch_combo.addItems(["64-bit", "32-bit"])
        arch_row.addWidget(self._arch_combo)
        arch_row.addStretch()
        rl.addLayout(arch_row)

        # Progress
        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        rl.addWidget(self._progress)

        self._speed_label = QLabel("")
        rl.addWidget(self._speed_label)

        # Buttons
        btn_row = QHBoxLayout()
        self._download_btn = QPushButton("Download / Install")
        self._download_btn.clicked.connect(self._download)
        self._cancel_btn = QPushButton("Cancel")
        self._cancel_btn.clicked.connect(self._cancel_download)
        self._cancel_btn.setEnabled(False)
        btn_row.addWidget(self._download_btn)
        btn_row.addWidget(self._cancel_btn)
        rl.addLayout(btn_row)
        rl.addStretch()

        splitter.addWidget(right)
        splitter.setSizes([280, 720])

    # ------------------------------------------------------------------
    # Data
    # ------------------------------------------------------------------

    def refresh_ui(self):
        """Reload registry data and refresh the list (called after detection)."""
        self._emus.reload()
        self._systems.reload()
        self._cfg.reload()
        self._populate()

    def select_emulator(self, name: str):
        """Programmatically select an emulator in the list, clearing filters first."""
        self._clear_search()
        self._filter_detected_btn.setChecked(False)
        self._filter_missing_preset_btn.setChecked(False)
        self._apply_filters()

        items = self._emu_list.findItems(name, Qt.MatchFlag.MatchExactly)
        if items:
            self._emu_list.setCurrentItem(items[0])
            self._emu_list.scrollToItem(items[0])

    def _show_context_menu(self, pos):
        item = self._emu_list.itemAt(pos)
        if not item or item.text() == "Add Custom": return
        
        menu = QMenu(self)
        del_act = menu.addAction(f"Delete '{item.text()}'")
        del_act.triggered.connect(lambda: self._delete_emulator(item.text()))
        menu.exec(self._emu_list.mapToGlobal(pos))

    def _delete_emulator(self, name: str):
        ans = QMessageBox.question(self, "Confirm Delete", f"Remove emulator '{name}' and its assignments?")
        if ans == QMessageBox.StandardButton.Yes:
            self._emus.delete_custom(name)
            self._emus._apps_cfg.set("EMULATORS", name, "")
            self._emus._apps_cfg.save()
            self._populate()
            self.set_status(f"Deleted {name}")
    
    def _clear_search(self):
        self._emu_search.clear()
        self._filter_emus("")

    def _on_filter_detected_toggled(self, checked: bool):
        self._filter_missing_preset_btn.setChecked(False)
        self._apply_filters()

    def _on_filter_missing_preset_toggled(self, checked: bool):
        self._filter_detected_btn.setChecked(False)
        self._apply_filters()

    def _filter_emus(self, text: str):
        self._apply_filters()

    def _apply_filters(self):
        """Unified filtering logic for Search, Detected (Y), and Missing Presets (N)."""
        search_text = self._emu_search.text().lower()
        show_detected_only = self._filter_detected_btn.isChecked()
        show_missing_presets = self._filter_missing_preset_btn.isChecked()

        missing_presets = set()
        if show_missing_presets:
            for sys_name in self._systems.all_systems():
                entry = self._systems._data.get(sys_name)
                if not entry: continue
                # Check if user has ROMs for this system
                if any(Path(p).exists() for p in entry.rom_path_list):
                    preset = entry.emu_reset
                    if preset and not self._emus._apps_cfg.get("EMULATORS", preset):
                        missing_presets.add(preset.lower())

        for i in range(self._emu_list.count()):
            item = self._emu_list.item(i)
            name = item.text()
            name_low = name.lower()
            
            hidden = False
            if search_text and search_text not in name_low:
                hidden = True
            elif show_detected_only and not self._emus._apps_cfg.get("EMULATORS", name):
                hidden = True
            elif show_missing_presets and name_low not in missing_presets:
                hidden = True
            
            item.setHidden(hidden)

    def _audit_bioses(self):
        """Verify existence and MD5 hashes of BIOS/Firmware requirements."""
        name = self._name_edit.text().strip()
        if not name: return
        
        from core.launcher import verify_bios
        status = verify_bios(name, "Audit-Mode", app_home())
        
        results = []
        if status.present:
            results.append(f"Found {len(status.present)} valid files.")
        if status.missing:
            results.append(f"MISSING: " + ", ".join(r.name for r in status.missing))
        if status.errors:
            results.extend(status.errors)
            
        if not results:
            QMessageBox.information(self, "Audit", "No specific BIOS/Firmware requirements found for this entry.")
        else:
            QMessageBox.information(self, f"BIOS Audit: {name}", "\n".join(results))

    def _populate(self):
        self._emu_list.clear()
        self._emu_list.addItem("Add Custom")
        for entry in self._emus.emulators():
            self._emu_list.addItem(entry.name)

    def _save_emu_path(self):
        """Persist the manually entered or browsed executable path to apps.json."""
        name = self._name_edit.text().strip()
        path = self._exe_path_edit.text().strip()
        if not name:
            return
        
        # Use registry helper for persistence
        entry = EmuEntry(
            name=name,
            exe=self._exe_label_edit.text(),
            archive=self._archive_edit.text(),
            extensions=[x.strip() for x in self._ext_edit.text().split(",") if x.strip()],
            required_files=[x.strip() for x in self._req_files_edit.toPlainText().split("\n") if x.strip()],
            options=self._opts_combo.currentText(),
            arguments=self._args_combo.currentText()
        )
        self._emus.add_custom(entry)

        self._emus._apps_cfg.set("EMULATORS", name, f'"{path}"')
        self._emus._apps_cfg.save()
        self.set_status(f"Saved path for {name}")

    def _browse_exe(self):
        current = self._exe_path_edit.text() or str(Path.home())
        chosen, _ = QFileDialog.getOpenFileName(self, "Select Executable", current, "Executables (*.exe);;All Files (*)")
        if chosen:
            self._exe_path_edit.setText(chosen)

    def _on_detect_clicked(self):
        main_win = self.window()
        if hasattr(main_win, "_systems_tab"):
            main_win._systems_tab._start_detection_process(is_first_run=False)

    def _on_emu_selected(self, name: str):
        is_custom = (name == "Add Custom")
        self._name_edit.setText("" if is_custom else name)
        self._name_edit.setReadOnly(not is_custom)
        self._exe_label_edit.setReadOnly(not is_custom)
        self._archive_edit.setReadOnly(not is_custom)
        self._req_files_edit.setReadOnly(not is_custom)

        if is_custom:
            self._exe_label_edit.clear()
            self._archive_edit.clear()
            self._exe_path_edit.clear()
            self._ext_edit.clear()
            self._req_files_edit.clear()
            return
            
        self._ext_edit.setReadOnly(False) # Always editable per request
        entry = self._emus.get(name)
        if not entry: return
        
        # Populate Metadata
        self._exe_label_edit.setText(entry.exe)
        self._archive_edit.setText(entry.archive)
        self._config_path_combo.set_paths(entry.configs)
        # Strictly map 'options' and 'arguments' from assets/emulators.json
        self._opts_combo.setCurrentText(str(getattr(entry, 'options', "")))
        self._args_combo.setCurrentText(str(getattr(entry, 'arguments', "")))
        self._ext_edit.setText(", ".join(entry.extensions))
        
        reqs = []
        if entry.bios_path: reqs.append(f"BIOS Path: {entry.bios_path}")
        if entry.firmware: reqs.append(f"Firmware: {entry.firmware}")
        if entry.required_files: reqs.extend(entry.required_files)
        self._req_files_edit.setPlainText("\n".join(reqs))

        # Populate detected location
        detected = self._emus._apps_cfg.get("EMULATORS", entry.name)
        self._exe_path_edit.setText(detected if detected else "")

        bits = 64 if self._arch_combo.currentIndex() == 0 else 32
        
        emu_dirs = [p.strip() for p in self._cfg.get("GLOBAL", "emulators_directory", fallback="").split("|") if p.strip()]
        base = Path(emu_dirs[0]) if emu_dirs else app_home() / "Emulators"
        self._install_path.setText(str(base / entry.name))

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _download(self):
        name = self._emu_list.currentItem()
        if not name:
            return
        entry = self._emus.get(name.text())
        if not entry or not entry.archive:
            QMessageBox.information(self, "No Archive",
                                    "No download archive defined for this emulator.")
            return

        bits = 64 if self._arch_combo.currentIndex() == 0 else 32
        archive_url = resolve_arch(entry.archive, bits)

        dest_dir = self._install_path.text() or str(
            app_home() / "Emulators" / entry.name)
        filename = Path(archive_url).name

        self._active_worker = DownloadWorker(
            url=archive_url, target_dir=dest_dir, filename=filename)
        self._active_worker.progress.connect(self._progress.setValue)
        self._active_worker.speed.connect(self._speed_label.setText)
        self._active_worker.finished.connect(self._on_download_finished)
        self._active_worker.start()

        self._download_btn.setEnabled(False)
        self._cancel_btn.setEnabled(True)
        self.set_status(f"Downloading {entry.name}…")

    def _cancel_download(self):
        if self._active_worker:
            self._active_worker.cancel()
        self._download_btn.setEnabled(True)
        self._cancel_btn.setEnabled(False)

    def _on_download_finished(self, success: bool):
        self._download_btn.setEnabled(True)
        self._cancel_btn.setEnabled(False)
        if success:
            # Auto-extract
            name = self._emu_list.currentItem()
            if name:
                entry = self._emus.get(name.text())
                if entry:
                    dest = self._install_path.text()
                    archive = str(Path(dest) / Path(entry.archive).name)
                    from utils.archive import extract
                    extract(archive, dest)
            self.set_status("Download complete.")
        else:
            self.set_status("Download failed.")
