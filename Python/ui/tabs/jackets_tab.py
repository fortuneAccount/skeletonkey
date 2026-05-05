"""
ui/tabs/jackets_tab.py

Jackets tab – advanced directory management, jacketizing and individuation.
"""
import os
import re
import json
import time
import shutil
import hashlib
import concurrent.futures
from pathlib import Path

from PyQt6.QtCore import Qt, QThread, pyqtSignal, QModelIndex, QSortFilterProxyModel, QDir
from PyQt6.QtGui import QAction, QFileSystemModel
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QSplitter, QTreeView,
    QLabel, QPushButton, QCheckBox, QGroupBox, QFormLayout,
    QFileDialog, QMessageBox, QHeaderView, QMenu
)

from core.config import global_config
from core.task_manager import TaskManager
from ui.tabs.base_tab import BaseTab
from utils.paths import temp_dir, app_root

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

    def cancel(self): self._is_cancelled = True

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
            if self._is_cancelled: break
            self.progress.emit(int((i / total) * 50))
            
            title = None
            if self.use_dat:
                h = hashlib.md5()
                with rom_file.open("rb") as f:
                    for chunk in iter(lambda: f.read(8192), b""): h.update(chunk)
                md5_val = h.hexdigest().lower()
                if md5_val in self.dat_data: title = self.dat_data[md5_val]

            if not title and self.use_filename:
                title = self._get_clean_name(rom_file.stem)

            if title:
                key = self._get_alpha_key(self._get_clean_name(title))
                if key not in groups:
                    folder_name = self._get_clean_name(title)
                    if self.inverted: folder_name = self._get_inverted_name(folder_name)
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
            with open(log_path, "w") as f: json.dump({"moves": move_history}, f, indent=4)
        self.finished.emit()

class JacketsTab(BaseTab):
    def __init__(self, systems, tasks, parent=None):
        super().__init__(parent)
        self._systems = systems
        self._tasks = tasks
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
        self.list_view = QTreeView() # Used as listview with columns
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
        self._jacket_btn.clicked.connect(self._on_jacketize)
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
        # Returns paths of checked items or selected items in list view
        paths = []
        # Check states first
        for path, state in self.list_model.checkStates.items():
            if state == Qt.CheckState.Checked:
                paths.append(path)
        # If none checked, use selection
        if not paths:
            for index in self.list_view.selectionModel().selectedRows():
                p = self.list_model.filePath(index)
                if os.path.isfile(p):
                    paths.append(p)
        return paths

    def _show_context_menu(self, pos):
        indexes = self.tree_view.selectionModel().selectedRows()
        if not indexes: return

        is_all_folders = all(self.tree_model.isDir(i) for i in indexes)
        is_any_file = any(not self.tree_model.isDir(i) for i in indexes)
        
        menu = QMenu()
        
        if is_all_folders:
            menu.addAction("Refresh").triggered.connect(lambda: self.tree_model.refresh())
            menu.addAction("Get Dats").triggered.connect(self._on_load_dat)
        elif not is_any_file:
             pass # Handled by the folder check above
        else:
            # File operations
            menu.addAction("Cut")
            menu.addAction("Copy")
            menu.addAction("Paste")
            menu.addAction("Delete")
            menu.addSeparator()
            menu.addAction("Match Hash")
            menu.addAction("Jacketize").triggered.connect(self._on_jacketize)
            menu.addAction("Individuate")

        menu.exec(self.tree_view.mapToGlobal(pos))

    def _on_load_dat(self):
        path, _ = QFileDialog.getOpenFileName(self, "Select Dat File", "", "Dat Files (*.dat *.xml)")
        if path:
            try:
                tree = ET.parse(path)
                root = tree.getroot()
                for game in root.findall('game'):
                    name = game.get('name')
                    for rom in game.findall('rom'):
                        md5 = rom.get('md5')
                        if md5: self._dat_data[md5.lower()] = name
                self.set_status(f"Loaded Dat: {len(self._dat_data)} entries")
            except: pass

    def _on_jacketize(self):
        files = self._get_selected_files()
        if not files:
            QMessageBox.information(self, "Selection", "No files selected.")
            return
            
        rom_dir = os.path.dirname(files[0])
        worker = JacketizeWorker(
            rom_dir, files,
            self._use_dat_chk.isChecked(),
            self._use_filename_chk.isChecked(),
            self._inverted_title_chk.isChecked(),
            self._include_all_chk.isChecked(),
            self._dat_data,
            ["zip", "7z", "iso", "bin"] # Example extensions
        )
        self._tasks.start_task("jacketize_process", worker)
