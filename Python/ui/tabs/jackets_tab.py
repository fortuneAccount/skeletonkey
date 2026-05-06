"""
ui/tabs/jackets_tab.py

Jackets tab – advanced template-driven file operations with selections, scripts, and options.
Includes jacketizing, tag replacement, and application configuration workflows.
"""

import os
import re
import json
import time
import shutil
import hashlib
from pathlib import Path

from PyQt6.QtCore import Qt, QThread, pyqtSignal, QDir
from PyQt6.QtGui import QFileSystemModel
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QCheckBox, QGroupBox, QFormLayout, QFileDialog, QMessageBox,
    QComboBox, QLineEdit, QTabWidget, QInputDialog, QSizePolicy,
    QSplitter, QTreeView, QListWidget, QListWidgetItem, QMenu
)

from core.task_manager import TaskManager
from ui.tabs.base_tab import BaseTab
from data.json_store import JSONStore
from utils.paths import temp_dir

COMMON_EXTENSIONS = [
    "*.*", "*.cfg", "*.ini", "*.json", "*.xml", "*.txt",
    "*.bat", "*.cmd", "*.ps1", "*.sh", "*.zip", "*.7z",
    "*.iso", "*.jpg", "*.png", "*.gif"
]

COMMON_TAGS = [
    "TAG_1", "TAG_2", "TAG_3", "TAG_4", "TAG_5",
    "PATH", "FILENAME", "DATE"
]

COMMON_VARIABLES = [
    "CURRENT_DATE", "CURRENT_TIME", "USER_NAME",
    "SOURCE_PATH", "DEST_PATH"
]

NODE_ORDER_DEFAULT = [
    'pre-windowing', 'pre-display', 'pre-mapper', 'pre-audio',
    'custom_#X', 'custom_#Y', 'post-mapper', 'post-display', 'post-windowing'
]

class JacketTemplateStore(JSONStore):
    def __init__(self):
        super().__init__("jackets_presets")


class CheckableFileSystemModel(QFileSystemModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.checkStates = {}

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.CheckStateRole and index.column() == 0:
            return self.checkStates.get(self.filePath(index), Qt.CheckState.Unchecked)
        return super().data(index, role)

    def setData(self, index, value, role=Qt.ItemDataRole.EditRole):
        if role == Qt.ItemDataRole.CheckStateRole and index.column() == 0:
            self.checkStates[self.filePath(index)] = value
            self.dataChanged.emit(index, index)
            return True
        return super().setData(index, value, role)

    def flags(self, index):
        flags = super().flags(index)
        if index.column() == 0:
            flags |= Qt.ItemFlag.ItemIsUserCheckable
        return flags


class JacketizeWorker(QThread):
    progress = pyqtSignal(int)
    status = pyqtSignal(str)
    finished = pyqtSignal()

    def __init__(self, rom_dir, file_paths, use_dat, use_filename, inverted, include_all, dat_data, extensions):
        super().__init__()
        self.rom_dir = Path(rom_dir)
        self.file_paths = [Path(p) for p in file_paths]
        self.use_dat = use_dat
        self.use_filename = use_filename
        self.inverted = inverted
        self.include_all = include_all
        self.dat_data = dat_data
        self.extensions = [e.lower() for e in extensions]
        self._is_cancelled = False

    def cancel(self):
        self._is_cancelled = True

    def _get_clean_name(self, name):
        name = re.sub(r'\(.*?\)|\[.*?\]', '', name)
        return re.sub(r'[<>:"/\\|?*]', '-', name).strip()

    def _get_alpha_key(self, name):
        return re.sub(r'[^a-z0-9]', '', name.lower())

    def _get_inverted_name(self, name):
        lower = name.lower()
        for prefix in ["the ", "a ", "an "]:
            if lower.startswith(prefix):
                return name[len(prefix):].strip() + ", " + name[:len(prefix)].strip()
        return name

    def run(self):
        move_history = []
        groups = {}
        total = len(self.file_paths)

        for i, rom_file in enumerate(self.file_paths):
            if self._is_cancelled:
                break
            self.progress.emit(int((i / total) * 50))

            title = None
            if self.use_dat:
                h = hashlib.md5()
                with rom_file.open("rb") as f:
                    for chunk in iter(lambda: f.read(8192), b""):
                        h.update(chunk)
                md5_val = h.hexdigest().lower()
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

        for key, data in groups.items():
            dest_dir = self.rom_dir / data["name"]
            dest_dir.mkdir(exist_ok=True)
            for rom_file in data["roms"]:
                target = dest_dir / rom_file.name
                shutil.move(str(rom_file), str(target))
                move_history.append({"src": str(rom_file), "dst": str(target)})
                if self.include_all:
                    rom_alpha = self._get_alpha_key(self._get_clean_name(rom_file.stem))
                    for adj in rom_file.parent.iterdir():
                        if adj.is_file() and adj.suffix.lower().lstrip('.') not in self.extensions:
                            if self._get_alpha_key(self._get_clean_name(adj.stem)) == rom_alpha:
                                adj_target = dest_dir / adj.name
                                shutil.move(str(adj), str(adj_target))
                                move_history.append({"src": str(adj), "dst": str(adj_target)})

        if move_history:
            log_path = temp_dir() / f"jacketize_log_{int(time.time())}.json"
            with open(log_path, "w") as f:
                json.dump({"moves": move_history}, f, indent=4)
        self.finished.emit()


class FileActionWorker(QThread):
    progress = pyqtSignal(int)
    status = pyqtSignal(str)
    finished = pyqtSignal(int)

    def __init__(self, panel_name: str, config: dict):
        super().__init__()
        self.panel_name = panel_name
        self.config = config
        self._is_cancelled = False

    def cancel(self):
        self._is_cancelled = True

    def _normalize_extensions(self, text: str) -> list[str]:
        items = [item.strip().lower() for item in text.replace(';', ',').split(',') if item.strip()]
        return [item if item.startswith('*.') else f'*.{item.lstrip("*.")}' for item in items]

    def _expand_path(self, raw_path: str) -> list[Path]:
        raw = raw_path.strip().strip('"')
        if not raw:
            return []
        path = Path(raw)
        if any(ch in raw for ch in ('*', '?')):
            base = path.parent if path.parent.exists() else Path('.')
            return list(base.glob(path.name))
        if path.exists():
            return [path]
        return []

    def _parse_destinations(self, raw_destination: str) -> list[Path]:
        parts = [item.strip().strip('"') for item in re.split(r'\s+(?=(?:[^\"]*\"[^\"]*\")*[^\"]*$)', raw_destination) if item.strip()]
        return [Path(part) for part in parts if part]

    def _match_extension(self, path: Path, patterns: list[str]) -> bool:
        if not path.is_file():
            return False
        if not patterns or patterns == ['*.*']:
            return True
        suffix = f'*.{path.suffix.lstrip(".").lower()}'
        return any(pattern == '*.*' or pattern == suffix for pattern in patterns)

    def _filter_entries(self, paths: list[Path]) -> list[Path]:
        results = []
        patterns = self._normalize_extensions(self.config.get('extensions', '*.*'))
        for path in paths:
            if path.is_dir():
                if self.config.get('recursive', False):
                    for child in path.rglob('*'):
                        if child.is_file() and self._match_extension(child, patterns):
                            results.append(child)
                else:
                    for child in path.iterdir():
                        if child.is_file() and self._match_extension(child, patterns):
                            results.append(child)
            elif path.is_file():
                if self._match_extension(path, patterns):
                    results.append(path)
        return results

    def _load_file(self, path: Path) -> str:
        try:
            return path.read_text(encoding='utf-8', errors='ignore')
        except Exception:
            return ''

    def _replace_tags(self, text: str, tag_name: str, value: str) -> str:
        token = f'{{${tag_name}}}'
        return text.replace(token, value)

    def _find_common_folder(self, paths: list[Path]) -> str | None:
        if not paths:
            return None
        reference = [part for part in paths[0].parts if part != paths[0].anchor]
        for candidate in reversed(reference):
            if all(candidate in path.parts for path in paths[1:]):
                return candidate
        return None

    def _resolve_destinations(self, sources: list[Path], destination_root: str) -> dict[Path, Path]:
        mapping = {}
        if not destination_root:
            return mapping
        destinations = self._parse_destinations(destination_root)
        if len(destinations) == len(sources):
            return dict(zip(sources, destinations))
        if len(destinations) == 1 and destinations[0].is_dir():
            dest_root = destinations[0]
            common = self._find_common_folder(sources)
            for source in sources:
                if common and common in source.parts:
                    index = list(source.parts).index(common)
                    rel = Path(*source.parts[index + 1:])
                else:
                    rel = source.name
                mapping[source] = dest_root / rel
        return mapping

    def run(self):
        self.status.emit(f"Starting {self.panel_name} operation...")
        raw_sources = [item.strip().strip('"') for item in self.config.get('sources', []) if item.strip()]
        source_paths = []
        for raw in raw_sources:
            source_paths.extend(self._expand_path(raw))
        if self._is_cancelled:
            self.finished.emit(0)
            return

        file_entries = self._filter_entries(source_paths)
        if self.config.get('exclude_enabled', False):
            exclude_files = self.config.get('exclude_type') == 'file'
            exclude_folders = self.config.get('exclude_type') == 'folder'
            filtered = []
            for candidate in file_entries:
                if exclude_files and candidate.is_file():
                    continue
                if exclude_folders and candidate.is_dir():
                    continue
                filtered.append(candidate)
            file_entries = filtered

        tag_name = self.config.get('tag_name', 'TAG_1')
        tag_value = self.config.get('tag_value', '')
        force = self.config.get('force_overwrite', False)
        update_only = self.config.get('update_only', False)
        create_only = self.config.get('create_only', False)
        copy_move = self.config.get('copy_move_enabled', False)
        move_mode = self.config.get('move_mode', 'copy') == 'move'
        destination_root = self.config.get('destination', '')
        dest_mapping = self._resolve_destinations(file_entries, destination_root) if copy_move else {}

        processed = 0
        total = len(file_entries)
        for idx, source in enumerate(file_entries, start=1):
            if self._is_cancelled:
                break
            content = self._load_file(source)
            if not content:
                continue
            replaced = self._replace_tags(content, tag_name, tag_value)
            target = dest_mapping.get(source, source if not copy_move else Path(destination_root).expanduser())
            if copy_move and not dest_mapping:
                target = Path(destination_root).expanduser() / source.name
            target_exists = target.exists()
            if update_only and not target_exists:
                continue
            if create_only and target_exists:
                continue
            if target_exists and not force:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(replaced, encoding='utf-8')
            if copy_move and move_mode and target != source:
                try:
                    source.unlink()
                except Exception:
                    pass
            processed += 1
            self.progress.emit(int((idx / max(total, 1)) * 100))

        self.status.emit(f"{self.panel_name} finished: {processed} files processed.")
        self.finished.emit(processed)


class SelectionsPanel(QWidget):
    """Selections tab for Jacketizing workflow."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self._dat_data = {}
        self._build_ui()

    def _build_ui(self):
        layout = QHBoxLayout(self)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        layout.addWidget(splitter)

        # Left: Tree and List
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)

        self.tree_model = QFileSystemModel()
        self.tree_model.setRootPath("")
        self.tree_model.setFilter(QDir.Filter.AllDirs | QDir.Filter.NoDotAndDotDot)

        self.tree_view = QTreeView()
        self.tree_view.setModel(self.tree_model)
        self.tree_view.setSelectionMode(QTreeView.SelectionMode.ExtendedSelection)
        self.tree_view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree_view.customContextMenuRequested.connect(self._show_context_menu)
        self.tree_view.clicked.connect(self._on_tree_clicked)

        self.list_model = CheckableFileSystemModel()
        self.list_view = QTreeView()
        self.list_view.setModel(self.list_model)
        self.list_view.setSelectionMode(QTreeView.SelectionMode.ExtendedSelection)

        left_layout.addWidget(QLabel("Directory Tree"))
        left_layout.addWidget(self.tree_view)
        left_layout.addWidget(QLabel("File Selection"))
        left_layout.addWidget(self.list_view)

        splitter.addWidget(left_widget)

        # Right: Config
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)

        config_group = QGroupBox("Library Options")
        form = QFormLayout(config_group)

        self._use_dat_chk = QCheckBox("Use Dat Matching")
        self._use_filename_chk = QCheckBox("Use Filename Cleaning")
        self._inverted_title_chk = QCheckBox("Inverted Title Format")
        self._include_all_chk = QCheckBox("Include All ROM-file matches")
        self._recursive_chk = QCheckBox("Recursive Search")

        form.addRow(self._use_dat_chk)
        form.addRow(self._use_filename_chk)
        form.addRow(self._inverted_title_chk)
        form.addRow(self._include_all_chk)
        form.addRow(self._recursive_chk)

        right_layout.addWidget(config_group)

        btn_layout = QVBoxLayout()
        self._jacket_btn = QPushButton("Jacketize Selected")
        self._indiv_btn = QPushButton("Individuate Selected")
        self._cancel_btn = QPushButton("Cancel Operation")
        self._cancel_btn.setEnabled(False)

        btn_layout.addWidget(self._jacket_btn)
        btn_layout.addWidget(self._indiv_btn)
        btn_layout.addWidget(self._cancel_btn)
        right_layout.addLayout(btn_layout)
        right_layout.addStretch()

        splitter.addWidget(right_widget)
        splitter.setSizes([400, 200])

    def _on_tree_clicked(self, index):
        path = self.tree_model.filePath(index)
        self.list_view.setRootIndex(self.list_model.setRootPath(path))

    def _get_selected_files(self):
        paths = []
        for path, state in self.list_model.checkStates.items():
            if state == Qt.CheckState.Checked:
                paths.append(path)
        if not paths:
            for index in self.list_view.selectionModel().selectedRows():
                p = self.list_model.filePath(index)
                if os.path.isfile(p):
                    paths.append(p)
        return paths

    def _show_context_menu(self, pos):
        indexes = self.tree_view.selectionModel().selectedRows()
        if not indexes:
            return
        menu = QMenu()
        menu.addAction("Refresh").triggered.connect(lambda: self.tree_model.refresh())
        menu.addAction("Get Dats").triggered.connect(self._on_load_dat)
        menu.exec(self.tree_view.mapToGlobal(pos))

    def _on_load_dat(self):
        path, _ = QFileDialog.getOpenFileName(self, "Select Dat File", "", "Dat Files (*.dat *.xml)")
        if path:
            QMessageBox.information(self, "Dat Loaded", f"Dat file loaded: {path}")



class ScriptsPanel(QWidget):
    """Scripts tab with preset templates and tag replacement."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self._preset_store = JacketTemplateStore()
        self._presets = self._preset_store.load()
        self._source_paths: list[str] = []
        self._destination_root = ''
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        # Preset Templates Section at top
        preset_group = QGroupBox("Preset Templates")
        preset_layout = QHBoxLayout(preset_group)

        self._preset_combo = QComboBox()
        self._preset_combo.setEditable(False)
        self._load_preset_btn = QPushButton("Load")
        self._save_preset_btn = QPushButton("Save")
        self._delete_preset_btn = QPushButton("Delete")

        preset_layout.addWidget(QLabel("Templates:"))
        preset_layout.addWidget(self._preset_combo)
        preset_layout.addWidget(self._load_preset_btn)
        preset_layout.addWidget(self._save_preset_btn)
        preset_layout.addWidget(self._delete_preset_btn)

        layout.addWidget(preset_group)

        # Tag Replacement Section
        tag_group = QGroupBox("Tag Replacement")
        tag_layout = QFormLayout(tag_group)

        self._source_edit = QLineEdit()
        browse_row = QHBoxLayout()
        browse_row.addWidget(self._source_edit)
        browse_files_btn = QPushButton("Select Files")
        browse_dirs_btn = QPushButton("Select Folders")
        browse_row.addWidget(browse_files_btn)
        browse_row.addWidget(browse_dirs_btn)
        tag_layout.addRow("Files/Folders:", browse_row)

        self._tag_combo = QComboBox()
        self._tag_combo.addItems(COMMON_TAGS)
        self._value_combo = QComboBox()
        self._value_combo.setEditable(True)
        self._value_combo.addItems(COMMON_VARIABLES)
        tag_row = QHBoxLayout()
        tag_row.addWidget(self._tag_combo)
        tag_row.addWidget(self._value_combo)
        tag_layout.addRow("Replace Tag:", tag_row)

        self._extension_combo = QComboBox()
        self._extension_combo.setEditable(True)
        self._extension_combo.addItems(COMMON_EXTENSIONS)
        tag_layout.addRow("Extensions:", self._extension_combo)

        self._recursive_chk = QCheckBox("Recursive Search")
        tag_layout.addRow(self._recursive_chk)

        self._run_btn = QPushButton("Run Script")
        tag_layout.addRow(self._run_btn)

        layout.addWidget(tag_group)
        layout.addStretch()

    def get_state(self) -> dict:
        return {
            'sources': self._source_paths,
            'tag_name': self._tag_combo.currentText(),
            'tag_value': self._value_combo.currentText(),
            'extensions': self._extension_combo.currentText(),
            'recursive': self._recursive_chk.isChecked(),
        }

    def set_state(self, state: dict):
        self._source_paths = state.get('sources', [])
        self._tag_combo.setCurrentText(state.get('tag_name', 'TAG_1'))
        self._value_combo.setCurrentText(state.get('tag_value', ''))
        self._extension_combo.setCurrentText(state.get('extensions', '*.*'))
        self._recursive_chk.setChecked(state.get('recursive', False))


class OptionsPanel(QWidget):
    """Options tab for application configuration and node ordering."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()

    def _build_ui(self):
        layout = QHBoxLayout(self)

        # Left: Application Configuration
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)

        apps = [
            ("Keyboard Mapper", "keyboard"),
            ("Display Application", "display"),
            ("Windowing Application", "windowing"),
            ("Audio Application", "audio"),
        ]

        self._app_widgets = {}

        for app_name, app_id in apps:
            app_group = QGroupBox(app_name)
            app_form = QFormLayout(app_group)

            # Executable selection
            exe_layout = QHBoxLayout()
            exe_edit = QLineEdit()
            exe_btn = QPushButton("Browse")
            exe_layout.addWidget(exe_edit)
            exe_layout.addWidget(exe_btn)
            app_form.addRow("Executable:", exe_layout)

            # Config files
            for i in range(3):
                cfg_layout = QHBoxLayout()
                cfg_combo = QComboBox()
                cfg_combo.setEditable(True)
                cfg_btn = QPushButton("Browse")
                cfg_layout.addWidget(cfg_combo)
                cfg_layout.addWidget(cfg_btn)
                app_form.addRow(f"Config {i+1}:", cfg_layout)

            # File selection/propagation options
            opt_layout = QVBoxLayout()
            copy_radio = QCheckBox("Copy")
            move_radio = QCheckBox("Move")
            create_chk = QCheckBox("Create New")
            overwrite_chk = QCheckBox("Overwrite")
            opt_layout.addWidget(copy_radio)
            opt_layout.addWidget(move_radio)
            opt_layout.addWidget(create_chk)
            opt_layout.addWidget(overwrite_chk)
            app_form.addRow("Options:", opt_layout)

            # Run/wait checkbox
            runwait_chk = QCheckBox("Run and wait")
            app_form.addRow(runwait_chk)

            self._app_widgets[app_id] = {
                'exe_edit': exe_edit,
                'exe_btn': exe_btn,
                'copy_radio': copy_radio,
                'move_radio': move_radio,
                'create_chk': create_chk,
                'overwrite_chk': overwrite_chk,
                'runwait_chk': runwait_chk,
            }

            left_layout.addWidget(app_group)

        left_layout.addStretch()
        layout.addWidget(left_widget, 2)

        # Right: Node Ordering
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)

        right_layout.addWidget(QLabel("Node Execution Order"))

        self._node_list = QListWidget()
        for node in NODE_ORDER_DEFAULT:
            item = QListWidgetItem(node)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            self._node_list.addItem(item)

        right_layout.addWidget(self._node_list)

        node_btn_layout = QHBoxLayout()
        up_btn = QPushButton("Move Up")
        down_btn = QPushButton("Move Down")
        add_btn = QPushButton("Add Custom")
        remove_btn = QPushButton("Remove")
        node_btn_layout.addWidget(up_btn)
        node_btn_layout.addWidget(down_btn)
        node_btn_layout.addWidget(add_btn)
        node_btn_layout.addWidget(remove_btn)
        right_layout.addLayout(node_btn_layout)

        layout.addWidget(right_widget, 1)


class JacketsTab(BaseTab):
    def __init__(self, systems, tasks, parent=None):
        super().__init__(parent)
        self._systems = systems
        self._tasks = tasks
        self._preset_store = JacketTemplateStore()
        self._presets = self._preset_store.load()
        self._build_ui()

    def _build_ui(self):
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)

        # Tab widget for the three panels
        self._operation_tabs = QTabWidget()

        self._selections_panel = SelectionsPanel(self)
        self._scripts_panel = ScriptsPanel(self)
        self._options_panel = OptionsPanel(self)

        self._operation_tabs.addTab(self._selections_panel, "Selections")
        self._operation_tabs.addTab(self._scripts_panel, "Scripts")
        self._operation_tabs.addTab(self._options_panel, "Options")

        root_layout.addWidget(self._operation_tabs, 1)

        # Operation Status Footer (detached from tabs)
        status_box = QGroupBox("Operation Status")
        status_layout = QHBoxLayout(status_box)
        self._status_label = QLabel("Ready")
        self._progress_label = QLabel("")
        status_layout.addWidget(self._status_label)
        status_layout.addStretch()
        status_layout.addWidget(self._progress_label)
        root_layout.addWidget(status_box)

        # Wire up buttons
        self._selections_panel._jacket_btn.clicked.connect(self._on_selections_action)
        self._scripts_panel._run_btn.clicked.connect(self._on_scripts_action)

    def _on_selections_action(self):
        files = self._selections_panel._get_selected_files()
        if not files:
            QMessageBox.information(self, "Selection", "No files selected.")
            return
        rom_dir = os.path.dirname(files[0])
        worker = JacketizeWorker(
            rom_dir, files,
            self._selections_panel._use_dat_chk.isChecked(),
            self._selections_panel._use_filename_chk.isChecked(),
            self._selections_panel._inverted_title_chk.isChecked(),
            self._selections_panel._include_all_chk.isChecked(),
            self._selections_panel._dat_data,
            ["zip", "7z", "iso", "bin"]
        )
        worker.progress.connect(lambda v: self._progress_label.setText(f"{v}%"))
        worker.status.connect(lambda s: self._status_label.setText(s))
        worker.finished.connect(lambda: self._progress_label.setText(""))
        self._tasks.start_task("jacketize_process", worker)
        self.set_status("Jacketizing...")

    def _on_scripts_action(self):
        config = self._scripts_panel.get_state()
        if not config['sources']:
            QMessageBox.information(self, "No Sources", "Select at least one file or folder.")
            return
        worker = FileActionWorker("Script", config)
        worker.progress.connect(lambda v: self._progress_label.setText(f"{v}%"))
        worker.status.connect(lambda s: self._status_label.setText(s))
        worker.finished.connect(lambda: self._progress_label.setText(""))
        self._tasks.start_task("script_process", worker)
        self.set_status("Running script...")

