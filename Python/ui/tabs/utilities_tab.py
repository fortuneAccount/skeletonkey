"""
ui/tabs/utilities_tab.py

Utilities tab – download and manage utility tools
(Daemon Tools, DS4Windows, DirectX runtimes, etc.).
"""
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QListWidget, QLabel, QPushButton, QLineEdit,
    QGroupBox, QFormLayout, QProgressBar, QMessageBox,
)

from core.config import global_config
from core.downloader import DownloadWorker
from data.emulators import EmuRegistry
from ui.tabs.base_tab import BaseTab
from utils.paths import app_home, bin_dir, resolve_arch


class UtilitiesTab(BaseTab):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._cfg = global_config()
        self._emus = EmuRegistry()
        self._active_worker: DownloadWorker | None = None
        self._build_ui()
        self._populate()

    def _build_ui(self):
        root = QHBoxLayout(self)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        root.addWidget(splitter)

        left = QWidget()
        ll = QVBoxLayout(left)
        ll.addWidget(QLabel("Utilities"))
        self._util_list = QListWidget()
        self._util_list.currentTextChanged.connect(self._on_util_selected)
        ll.addWidget(self._util_list)
        splitter.addWidget(left)

        right = QWidget()
        rl = QVBoxLayout(right)

        info = QGroupBox("Utility Details")
        form = QFormLayout(info)
        self._name_label = QLabel("")
        self._exe_label = QLabel("")
        form.addRow("Name:", self._name_label)
        form.addRow("Executable:", self._exe_label)
        rl.addWidget(info)

        self._install_path = QLineEdit()
        self._install_path.setPlaceholderText("Install directory…")
        rl.addWidget(self._install_path)

        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        rl.addWidget(self._progress)

        btn_row = QHBoxLayout()
        self._download_btn = QPushButton("Download / Install")
        self._download_btn.clicked.connect(self._download)
        self._run_btn = QPushButton("Run")
        self._run_btn.clicked.connect(self._run)
        btn_row.addWidget(self._download_btn)
        btn_row.addWidget(self._run_btn)
        rl.addLayout(btn_row)
        rl.addStretch()

        splitter.addWidget(right)
        splitter.setSizes([280, 720])

    def _populate(self):
        self._util_list.clear()
        for entry in self._emus.utilities():
            self._util_list.addItem(entry.name)
        for entry in self._emus.keymappers():
            self._util_list.addItem(entry.name)

    def _on_util_selected(self, name: str):
        entry = self._emus.get(name)
        if not entry:
            return
        self._name_label.setText(entry.name)
        self._exe_label.setText(entry.exe)
        self._install_path.setText(str(app_home() / "Utilities" / entry.name))

    def _download(self):
        name = self._util_list.currentItem()
        if not name:
            return
        entry = self._emus.get(name.text())
        if not entry or not entry.archive:
            QMessageBox.information(self, "No Archive",
                                    "No download archive defined.")
            return
        archive_url = resolve_arch(entry.archive, 64)
        if not archive_url.startswith("http"):
            from core.config import Config
            arcorg = Config(Config.ARCORG_FILE)
            base = arcorg.get("REPOSITORIES", "buildBotCore",
                              fallback="https://buildbot.libretro.com")
            archive_url = f"{base}/{archive_url}"

        dest_dir = self._install_path.text()
        filename = Path(archive_url).name
        aria2c = str(bin_dir() / "aria2c.exe") if (bin_dir() / "aria2c.exe").exists() else ""

        self._active_worker = DownloadWorker(
            url=archive_url, target_dir=dest_dir,
            filename=filename, aria2c_path=aria2c)
        self._active_worker.progress.connect(self._progress.setValue)
        self._active_worker.finished.connect(self._on_download_finished)
        self._active_worker.start()
        self._download_btn.setEnabled(False)

    def _on_download_finished(self, success: bool):
        self._download_btn.setEnabled(True)
        self.set_status(
            "Download complete." if success else "Download failed.")

    def _run(self):
        name = self._util_list.currentItem()
        if not name:
            return
        entry = self._emus.get(name.text())
        if not entry or not entry.exe:
            return
        import subprocess
        exe = Path(self._install_path.text()) / entry.exe
        if exe.exists():
            subprocess.Popen([str(exe)], cwd=str(exe.parent))
        else:
            QMessageBox.warning(self, "Not Found",
                                f"{exe} not found. Download it first.")
