"""
ui/tabs/emulators_tab.py

Emulators tab – browse, download and configure emulators.
"""
import shutil
import hashlib
from pathlib import Path
import re

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QListWidget, QListWidgetItem, QLabel, QPushButton, QLineEdit,
    QGroupBox, QFormLayout, QProgressBar, QMessageBox, QMenu,
    QComboBox, QPlainTextEdit, QFileDialog, QToolButton
)

from core.config import global_config
from core.downloader import DownloadWorker
from data.emulators import EmuRegistry, EmuEntry
from data.systems import SystemRegistry
from ui.tabs.base_tab import BaseTab
from core.task_manager import TaskManager
from ui.tabs.settings_tab import _PathCombo
from utils.paths import app_home, bin_dir, resolve_arch, check_paths_exist, app_root
from PyQt6.QtGui import QColor, QBrush

def _get_file_hash(path: Path) -> str:
    """Calculate MD5 hash of a file."""
    if not path.exists():
        return ""
    h = hashlib.md5()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()

class EmulatorsTab(BaseTab):
    def __init__(self, systems: SystemRegistry, emus: EmuRegistry, tasks: TaskManager, parent=None):
        super().__init__(parent)
        self._cfg = global_config()
        self._emus = emus
        self._systems = systems
        self._tasks = tasks
        self._is_fallback_active = False
        self._romjacket_repo = "https://github.com/romjacket/romjacket-emulators/raw/master"
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
        self._clear_search_btn.setToolTip("Clear current search")
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
        self._reset_btn = QPushButton("Reset Defaults")
        self._reset_btn.clicked.connect(self._reset_emu_defaults)

        act_row.addWidget(self._save_btn)
        act_row.addWidget(self._audit_btn)
        act_row.addWidget(self._reset_btn)
        rl.addLayout(act_row)

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
        splitter.setSizes([200, 600])

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
        """Fill the emulator list and apply status-based coloring."""
        self._emu_list.clear()
        self._emu_list.addItem("Add Custom")
        for entry in self._emus.emulators():
            self._emu_list.addItem(entry.name)
        self._update_item_styles()

    def _update_item_styles(self):
        """Apply Yellow (Downloaded) or Green (Installed) colors to the list items."""
        download_store = app_root() / "downloaded"
        apps = self._emus._apps_cfg
        apps.reload()

        for i in range(self._emu_list.count()):
            item = self._emu_list.item(i)
            name = item.text()
            if name == "Add Custom": continue
            
            entry = self._emus.get(name)
            if not entry: continue

            # 1. Check if installed (registered in apps.json and file exists)
            installed_path = apps.get("EMULATORS", name)
            is_installed = installed_path and Path(installed_path.strip('"')).exists()

            # 2. Check if downloaded (archive exists in store)
            archive_url = resolve_arch(entry.archive, 64)
            is_downloaded = (download_store / Path(archive_url).name).exists()

            if is_installed:
                item.setForeground(QBrush(QColor(40, 167, 69))) # Green
            elif is_downloaded:
                item.setForeground(QBrush(QColor(255, 193, 7))) # Yellow
            else:
                item.setForeground(QBrush(Qt.GlobalColor.white))

    def _save_emu_path(self):
        """Persist the manually entered or browsed executable path to apps.json."""
        name = self._name_edit.text().strip()
        path = self._exe_path_edit.text().strip()
        if not name:
            return
        
        # Helper to join all combo items with delimiter to preserve multiple choices
        def get_combo_val(cb):
            items = [cb.itemText(i) for i in range(cb.count())]
            curr = cb.currentText()
            # Ensure the current text is at the top of the saved choices
            if curr and curr in items:
                items.remove(curr)
                items.insert(0, curr)
            elif curr:
                items.insert(0, curr)
            return "<".join([i for i in items if i.strip()])
        
        # Use registry helper for persistence
        entry = EmuEntry(
            name=name,
            exe=self._exe_label_edit.text(),
            archive=self._archive_edit.text(),
            extensions=[x.strip() for x in self._ext_edit.text().split(",") if x.strip()],
            required_files=[x.strip() for x in self._req_files_edit.toPlainText().split("\n") if x.strip()],
            options=get_combo_val(self._opts_combo),
            arguments=get_combo_val(self._args_combo)
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

    def _reset_emu_defaults(self):
        """Revert options and arguments to the original asset metadata."""
        name = self._name_edit.text().strip()
        if not name: return
        
        from utils.paths import assets_dir
        import json
        src_json = assets_dir() / "emulators.json"
        if not src_json.exists(): return
        
        try:
            with open(src_json, "r", encoding="utf-8") as f:
                emu_data = json.load(f)
                asset_info = emu_data.get(name)
                if not asset_info:
                    QMessageBox.information(self, "Reset", f"No asset defaults found for '{name}'.")
                    return
                
                def split_val(v):
                    if not v: return []
                    if isinstance(v, list):
                        return [str(x) for x in v if str(x)]
                    return [x for x in re.split(r'[<|\n]', str(v)) if x]

                # Update UI Combos
                self._opts_combo.blockSignals(True)
                self._opts_combo.clear()
                self._opts_combo.addItems(split_val(asset_info.get("options", "")))
                self._opts_combo.blockSignals(False)

                self._args_combo.blockSignals(True)
                self._args_combo.clear()
                self._args_combo.addItems(split_val(asset_info.get("arguments", "")))
                self._args_combo.blockSignals(False)
                
                self.set_status(f"Restored asset defaults for {name}")
        except Exception as e:
            self.set_status(f"Reset failed: {e}")

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

        def split_val(v):
            if not v: return []
            if isinstance(v, list):
                return [str(x) for x in v if str(x)]
            return [x for x in re.split(r'[<|\n]', str(v)) if x]

        # Strictly map 'options' and 'arguments' into selectable combobox items
        self._opts_combo.blockSignals(True)
        self._opts_combo.clear()
        opts = split_val(getattr(entry, 'options', ""))
        self._opts_combo.addItems(opts)
        if opts:
            self._opts_combo.setCurrentIndex(0)
        self._opts_combo.blockSignals(False)

        self._args_combo.blockSignals(True)
        self._args_combo.clear()
        args = split_val(getattr(entry, 'arguments', ""))
        self._args_combo.addItems(args)
        if args:
            self._args_combo.setCurrentIndex(0)
        self._args_combo.blockSignals(False)

        self._ext_edit.setText(", ".join(entry.extensions))
        
        reqs = []
        if entry.bios_path: reqs.append(f"BIOS Path: {entry.bios_path}")
        if entry.firmware: reqs.append(f"Firmware: {entry.firmware}")
        if entry.required_files: reqs.extend(entry.required_files)
        self._req_files_edit.setPlainText("\n".join(reqs))

        # Populate detected location
        detected = self._emus._apps_cfg.get("EMULATORS", entry.name)
        self._exe_path_edit.setText(detected if detected else "")

        emu_dirs = [p.strip() for p in self._cfg.get("GLOBAL", "emulators_directory", fallback="").split("|") if p.strip()]
        base = Path(emu_dirs[0]) if emu_dirs else app_home() / "Emulators"
        self._install_path.setText(str(base / entry.name))

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _download(self, url_override: str = None):
        name = self._emu_list.currentItem() if not url_override else self._name_edit.text()
        if not name:
            return
        entry = self._emus.get(name.text())
        if not entry or not entry.archive:
            QMessageBox.information(self, "No Archive",
                                    "No download archive defined for this emulator.")
            return

        archive_url = url_override if url_override else resolve_arch(entry.archive, 64)

        filename = Path(archive_url).name
        download_store = app_root() / "downloaded"
        cached_file = download_store / filename

        if cached_file.exists():
            self.set_status(f"Using cached archive: {filename}")
            self._on_download_finished(True, cached_path=cached_file)
            return

        dest_dir = self._install_path.text() or str(
            app_home() / "Emulators" / entry.name)
        
        # Prepend base URL if the archive path is relative and not an override
        if not archive_url.startswith("http") and not url_override:
            archive_url = f"https://www.google.com/search?q={entry.name}+portable+download" # Placeholder logic for 'preferred'
        worker = DownloadWorker(url=archive_url, target_dir=dest_dir, filename=filename)
        worker.progress.connect(self._progress.setValue)
        worker.speed.connect(self._speed_label.setText)
        worker.finished.connect(self._on_download_finished)
        
        self._tasks.start_task(f"download_{entry.name}", worker)

        self._download_btn.setEnabled(False)
        self._cancel_btn.setEnabled(True)
        self.set_status(f"Downloading {entry.name}…")

    def _cancel_download(self):
        self._tasks.cancel_task(f"download_{self._name_edit.text()}")
        self._download_btn.setEnabled(True)
        self._cancel_btn.setEnabled(False)

    def _on_download_finished(self, success: bool, cached_path: Path = None):
        self._download_btn.setEnabled(True)
        self._cancel_btn.setEnabled(False)

        if not success and not self._is_fallback_active:
            # Attempt fallback to romjacket repository
            item = self._emu_list.currentItem()
            if item:
                entry = self._emus.get(item.text())
                if entry:
                    rel_path = resolve_arch(entry.archive, 64)
                    fallback_url = f"{self._romjacket_repo}/{rel_path}"
                    self.set_status(f"Primary download failed. Trying RomJacket fallback...")
                    self._is_fallback_active = True
                    self._download(url_override=fallback_url)
                    return

        self._is_fallback_active = False # Reset state

        if success:
            self._progress.setValue(100)
            self._speed_label.setText("Finished")
            # Auto-extract
            item = self._emu_list.currentItem()
            if item:
                name = item.text()
                entry = self._emus.get(name)
                if entry:
                    dest_dir = Path(self._install_path.text())
                    archive_url = resolve_arch(entry.archive, 64)
                    
                    archive_path = cached_path or (dest_dir / Path(archive_url).name)
                    
                    from utils.archive import extract
                    if archive_path.exists() and extract(str(archive_path), str(dest_dir)):
                        # 1. Register path to apps.json
                        exe_path = dest_dir / entry.exe
                        if not exe_path.exists():
                            # Search subdirectories if exe isn't at the root of the extract folder
                            found_exes = list(dest_dir.rglob(entry.exe))
                            if found_exes:
                                exe_path = found_exes[0]

                        if exe_path.exists():
                            self._emus._apps_cfg.set("EMULATORS", name, f'"{exe_path}"')
                            self._emus._apps_cfg.save()

                        # 2. Move compressed binary to 'downloaded' folder in root
                        download_store = app_root() / "downloaded"
                        download_store.mkdir(exist_ok=True)
                        storage_path = download_store / archive_path.name
                        
                        if not cached_path:
                            try:
                                # Rotation: .7z -> .7z.bak
                                if storage_path.exists():
                                    if _get_file_hash(archive_path) == _get_file_hash(storage_path):
                                        archive_path.unlink()
                                        self.set_status(f"Installed {name} (archive is unchanged).")
                                        return
                                
                                bak_path = Path(str(storage_path) + ".bak")
                                if storage_path.exists():
                                    if bak_path.exists():
                                        bak_path.unlink()
                                    storage_path.rename(bak_path)
                                
                                shutil.move(str(archive_path), str(storage_path))
                                self.set_status(f"Installed {name} and archived installer.")
                            except Exception as e:
                                self.set_status(f"Installed {name}, but archive rotation failed: {e}")
                        else:
                            self.set_status(f"Installed {name} from cache.")
                        
                        self._update_item_styles()
                    else:
                        self.set_status(f"Extraction failed for {name}: {archive_path}")
        else:
            self.set_status("Download failed.")
