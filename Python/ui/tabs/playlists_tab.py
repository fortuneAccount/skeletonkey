"""
ui/tabs/playlists_tab.py

Playlists tab – create, edit and manage game playlists (.lpl format).
"""
import json
from pathlib import Path

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QListWidget, QListWidgetItem, QLabel, QPushButton,
    QFileDialog, QInputDialog, QMessageBox, QLineEdit,
    QGroupBox, QFormLayout,
)
from PyQt6.QtCore import Qt

from core.config import global_config
from ui.tabs.base_tab import BaseTab


class PlaylistsTab(BaseTab):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._cfg = global_config()
        self._current_playlist: Path | None = None
        self._build_ui()

    def _build_ui(self):
        root = QHBoxLayout(self)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        root.addWidget(splitter)

        # Left: playlist files
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.addWidget(QLabel("Playlists"))
        self._playlist_list = QListWidget()
        self._playlist_list.currentTextChanged.connect(self._on_playlist_selected)
        ll.addWidget(self._playlist_list)

        pl_btns = QHBoxLayout()
        new_btn = QPushButton("New")
        new_btn.clicked.connect(self._new_playlist)
        open_btn = QPushButton("Open…")
        open_btn.clicked.connect(self._open_playlist)
        pl_btns.addWidget(new_btn)
        pl_btns.addWidget(open_btn)
        ll.addLayout(pl_btns)
        splitter.addWidget(left)

        # Right: playlist entries
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.addWidget(QLabel("Entries"))
        self._entry_list = QListWidget()
        rl.addWidget(self._entry_list)

        entry_btns = QHBoxLayout()
        add_btn = QPushButton("Add ROMs…")
        add_btn.clicked.connect(self._add_roms)
        remove_btn = QPushButton("Remove")
        remove_btn.clicked.connect(self._remove_entry)
        save_btn = QPushButton("Save")
        save_btn.clicked.connect(self._save_playlist)
        entry_btns.addWidget(add_btn)
        entry_btns.addWidget(remove_btn)
        entry_btns.addWidget(save_btn)
        rl.addLayout(entry_btns)

        splitter.addWidget(right)
        splitter.setSizes([280, 720])

        self._scan_playlists()

    # ------------------------------------------------------------------
    # Data
    # ------------------------------------------------------------------

    def _scan_playlists(self):
        self._playlist_list.clear()
        systems_dir = self._cfg.get("GLOBAL", "systems_directory", fallback="")
        if not systems_dir:
            return
        for lpl in sorted(Path(systems_dir).rglob("*.lpl")):
            self._playlist_list.addItem(str(lpl))

    def _on_playlist_selected(self, path_str: str):
        if not path_str:
            return
        self._current_playlist = Path(path_str)
        self._load_playlist(self._current_playlist)

    def _load_playlist(self, path: Path):
        self._entry_list.clear()
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            for item in data.get("items", []):
                label = item.get("label") or item.get("path", "")
                self._entry_list.addItem(label)
        except Exception:
            # Plain-text playlist fallback
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    self._entry_list.addItem(line.strip())

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _new_playlist(self):
        name, ok = QInputDialog.getText(self, "New Playlist", "Playlist name:")
        if not ok or not name:
            return
        systems_dir = self._cfg.get("GLOBAL", "systems_directory", fallback="")
        if not systems_dir:
            QMessageBox.warning(self, "No Systems Dir",
                                "Set a systems directory in Settings first.")
            return
        path = Path(systems_dir) / "playlists" / f"{name}.lpl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"version": "1.5", "items": []}, indent=2),
                        encoding="utf-8")
        self._scan_playlists()

    def _open_playlist(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open Playlist", "", "Playlists (*.lpl);;All Files (*)")
        if path:
            self._current_playlist = Path(path)
            self._load_playlist(self._current_playlist)

    def _add_roms(self):
        files, _ = QFileDialog.getOpenFileNames(
            self, "Add ROMs", "", "All Files (*)")
        for f in files:
            self._entry_list.addItem(f)

    def _remove_entry(self):
        row = self._entry_list.currentRow()
        if row >= 0:
            self._entry_list.takeItem(row)

    def _save_playlist(self):
        if not self._current_playlist:
            return
        items = []
        for i in range(self._entry_list.count()):
            text = self._entry_list.item(i).text()
            items.append({"path": text, "label": Path(text).stem})
        data = {"version": "1.5", "items": items}
        self._current_playlist.write_text(
            json.dumps(data, indent=2), encoding="utf-8")
        self.set_status(f"Saved {self._current_playlist.name}")
