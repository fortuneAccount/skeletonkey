"""
ui/tabs/emulators_tab.py

Emulators tab – browse, download and configure emulators.
"""
from pathlib import Path

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QListWidget, QLabel, QPushButton, QLineEdit,
    QGroupBox, QFormLayout, QProgressBar, QMessageBox,
    QComboBox,
)

from core.config import global_config
from core.downloader import DownloadWorker
from data.emulators import EmuRegistry, EmuEntry
from ui.tabs.base_tab import BaseTab
from utils.paths import app_home, bin_dir, resolve_arch


class EmulatorsTab(BaseTab):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._cfg = global_config()
        self._emus = EmuRegistry()
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
        ll.addWidget(QLabel("Emulators"))
        self._emu_list = QListWidget()
        self._emu_list.currentTextChanged.connect(self._on_emu_selected)
        ll.addWidget(self._emu_list)
        splitter.addWidget(left)

        # Right: details + actions
        right = QWidget()
        rl = QVBoxLayout(right)

        info = QGroupBox("Emulator Details")
        form = QFormLayout(info)
        self._name_label = QLabel("")
        self._exe_label = QLabel("")
        self._archive_label = QLabel("")
        form.addRow("Name:", self._name_label)
        form.addRow("Executable:", self._exe_label)
        form.addRow("Archive:", self._archive_label)
        rl.addWidget(info)

        # Install path
        path_row = QHBoxLayout()
        self._install_path = QLineEdit()
        self._install_path.setPlaceholderText("Install directory…")
        path_row.addWidget(self._install_path)
        rl.addLayout(path_row)

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

    def _populate(self):
        self._emu_list.clear()
        for entry in self._emus.emulators():
            self._emu_list.addItem(entry.name)

    def _on_emu_selected(self, name: str):
        entry = self._emus.get(name)
        if not entry:
            return
        self._name_label.setText(entry.name)
        self._exe_label.setText(entry.exe)
        self._archive_label.setText(entry.archive)

        bits = 64 if self._arch_combo.currentIndex() == 0 else 32
        default_path = str(app_home() / "Emulators" / entry.name)
        self._install_path.setText(default_path)

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

        # If archive is a relative path, prepend the configured repo base URL
        if not archive_url.startswith("http"):
            from core.config import Config
            arcorg = Config(Config.ARCORG_FILE)
            base = arcorg.get("REPOSITORIES", "buildBotCore",
                              fallback="https://buildbot.libretro.com")
            archive_url = f"{base}/{archive_url}"

        dest_dir = self._install_path.text() or str(
            app_home() / "Emulators" / entry.name)
        filename = Path(archive_url).name

        aria2c = str(bin_dir() / "aria2c.exe") if (bin_dir() / "aria2c.exe").exists() else ""

        self._active_worker = DownloadWorker(
            url=archive_url,
            target_dir=dest_dir,
            filename=filename,
            aria2c_path=aria2c,
        )
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
