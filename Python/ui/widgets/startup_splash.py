"""
ui/widgets/startup_splash.py

Splash screen for initial run activity logging.
"""
from pathlib import Path
from PyQt6.QtWidgets import QSplashScreen, QLabel, QGraphicsOpacityEffect, QProgressBar, QApplication
from PyQt6.QtGui import QPixmap, QFont, QFontDatabase
from PyQt6.QtCore import Qt, QPropertyAnimation, QEasingCurve
from utils.paths import app_root, img_dir

class StartupSplashScreen(QSplashScreen):
    def __init__(self):
        splash_img = img_dir() / "splash.png"
        pixmap = QPixmap(str(splash_img)) if splash_img.exists() else QPixmap(600, 250)
        super().__init__(pixmap)
        
        self.setWindowFlags(Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        
        # Load Opticon Font
        font_path = app_root() / "site" / "Opticon.ttf"
        font_family = "Arial"
        if font_path.exists():
            font_id = QFontDatabase.addApplicationFont(str(font_path))
            if font_id != -1:
                families = QFontDatabase.applicationFontFamilies(font_id)
                if families:
                    font_family = families[0]

        self._font = QFont(font_family, 10)
            
        self._label = QLabel(self)
        self._label.setFont(self._font)
        self._label.setStyleSheet("color: black; background: transparent;")
        self._label.setFixedWidth(pixmap.width())
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._label.move(0, pixmap.height() - 65)

        # Progress bar setup positioned below the text
        self._progress = QProgressBar(self)
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._progress.setFixedHeight(4)
        self._progress.setFixedWidth(pixmap.width() - 100)
        self._progress.move(50, pixmap.height() - 30)
        self._progress.setTextVisible(False)
        self._progress.setStyleSheet("""
            QProgressBar {
                border: 1px solid black;
                background: transparent;
            }
            QProgressBar::chunk {
                background-color: black;
            }
        """)

        # Setup animation for smooth transitions
        self._opacity_effect = QGraphicsOpacityEffect(self._label)
        self._label.setGraphicsEffect(self._opacity_effect)
        self._animation = QPropertyAnimation(self._opacity_effect, b"opacity")
        self._animation.setDuration(250)
        self._animation.setStartValue(0.0)
        self._animation.setEndValue(1.0)
        self._animation.setEasingCurve(QEasingCurve.Type.OutQuad)

        # Setup animation for progress bar
        self._progress_animation = QPropertyAnimation(self._progress, b"value")
        self._progress_animation.setDuration(200) # milliseconds
        self._progress_animation.setEasingCurve(QEasingCurve.Type.OutCubic)

    def update_log(self, message: str):
        """Update the splash message with the latest log entry."""
        self._label.setText(message.strip())
        self._animation.stop()
        self._animation.start()

    def update_progress(self, value: int):
        """Update the progress bar value."""
        self._progress_animation.stop()
        self._progress_animation.setStartValue(self._progress.value())
        self._progress_animation.setEndValue(value)
        self._progress_animation.start()
        QApplication.processEvents() # Ensure UI updates during animation