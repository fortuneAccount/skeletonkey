"""
ui/tabs/artwork_tab.py

Artwork tab – scrape and manage game artwork / metadata.
Mirrors the scraping / asset management functionality from working.ahk.
"""
from pathlib import Path

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QListWidget, QLabel, QPushButton, QLineEdit,
    QGroupBox, QFormLayout, QProgressBar, QComboBox,
    QCheckBox, QMessageBox, QFileDialog,
)

from core.config import global_config
from data.systems import SystemRegistry
from ui.tabs.base_tab import BaseTab


# Supported metadata sources (mirrors mediaordert in sets.ahk)
METADATA_SOURCES = [
    "theGamesDB",
    "OpenVGDB",
    "ScreenScraper",
    "arcadeitalia",
    "mamedb",
]

# Artwork types (mirrors metaimages in sets.ahk)
ARTWORK_TYPES = [
    "BoxArt", "3DBoxart", "Marquee", "Label", "Cart",
    "Backdrop", "Logo", "Video", "Snapshot",
]


class ArtworkTab(BaseTab):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._cfg = global_config()
        self._systems = SystemRegistry()
        self._build_ui()
        self._populate_systems()

    def _build_ui(self):
        root = QHBoxLayout(self)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        root.addWidget(splitter)

        # Left: system selector
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.addWidget(QLabel("Systems"))
        self._system_list = QListWidget()
        ll.addWidget(self._system_list)
        splitter.addWidget(left)

        # Right: scrape options
        right = QWidget()
        rl = QVBoxLayout(right)

        opts = QGroupBox("Scrape Options")
        form = QFormLayout(opts)

        self._source_combo = QComboBox()
        self._source_combo.addItems(METADATA_SOURCES)
        form.addRow("Metadata Source:", self._source_combo)

        self._art_type_combo = QComboBox()
        self._art_type_combo.addItems(ARTWORK_TYPES)
        form.addRow("Artwork Type:", self._art_type_combo)

        self._output_dir = QLineEdit()
        out_row = QHBoxLayout()
        out_row.addWidget(self._output_dir)
        browse_btn = QPushButton("Browse…")
        browse_btn.clicked.connect(self._browse_output)
        out_row.addWidget(browse_btn)
        form.addRow("Output Directory:", out_row)

        self._overwrite_chk = QCheckBox("Overwrite existing")
        form.addRow("", self._overwrite_chk)

        rl.addWidget(opts)

        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        rl.addWidget(self._progress)

        self._status_label = QLabel("")
        rl.addWidget(self._status_label)

        btn_row = QHBoxLayout()
        self._scrape_btn = QPushButton("Scrape Selected System")
        self._scrape_btn.clicked.connect(self._scrape)
        btn_row.addWidget(self._scrape_btn)
        rl.addLayout(btn_row)
        rl.addStretch()

        splitter.addWidget(right)
        splitter.setSizes([280, 720])

    def _populate_systems(self):
        self._system_list.clear()
        for name in self._systems.all_systems():
            self._system_list.addItem(name)

    def _browse_output(self):
        chosen = QFileDialog.getExistingDirectory(
            self, "Select Output Directory",
            self._output_dir.text() or str(Path.home()))
        if chosen:
            self._output_dir.setText(chosen)

    def _scrape(self):
        system_item = self._system_list.currentItem()
        if not system_item:
            QMessageBox.information(self, "No System", "Select a system first.")
            return
        system = system_item.text()
        source = self._source_combo.currentText()
        art_type = self._art_type_combo.currentText()
        output = self._output_dir.text()

        if not output:
            QMessageBox.warning(self, "No Output", "Set an output directory.")
            return

        # Placeholder: actual scraper integration goes here.
        # The original used sselph/scraper.exe via subprocess.
        # A future implementation can call the scraper binary or a Python
        # scraping library and emit progress via signals.
        self._status_label.setText(
            f"Scraping {system} from {source} ({art_type})…  [not yet implemented]")
        self.set_status(
            f"Scraping {system} – {source} – {art_type}")
