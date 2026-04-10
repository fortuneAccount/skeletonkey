"""
core/downloader.py

Download manager wrapping aria2c (preferred) with a requests fallback.
Replaces the exe_get / met_get / DownloadFile functions in gets.ahk.

Emits Qt signals for progress so the UI can update progress bars without
polling a status file (as the AHK version did).
"""
import os
import subprocess
import threading
from pathlib import Path

from PyQt6.QtCore import QObject, pyqtSignal


class DownloadWorker(QObject):
    """
    Runs a download in a background thread and emits progress signals.

    Signals:
        progress(int)   – 0-100 percent complete
        speed(str)      – human-readable speed string e.g. "1.2 MB/s"
        finished(bool)  – True on success, False on failure
        error(str)      – error message on failure
    """

    progress = pyqtSignal(int)
    speed = pyqtSignal(str)
    finished = pyqtSignal(bool)
    error = pyqtSignal(str)

    def __init__(
        self,
        url: str,
        target_dir: str,
        filename: str,
        aria2c_path: str = "",
        parent=None,
    ):
        super().__init__(parent)
        self.url = url
        self.target_dir = Path(target_dir)
        self.filename = filename
        self.aria2c_path = aria2c_path
        self._cancelled = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self):
        """Kick off the download in a daemon thread."""
        t = threading.Thread(target=self._run, daemon=True)
        t.start()

    def cancel(self):
        self._cancelled = True

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _run(self):
        self.target_dir.mkdir(parents=True, exist_ok=True)
        dest = self.target_dir / self.filename

        if self.aria2c_path and Path(self.aria2c_path).exists():
            ok = self._download_aria2c(dest)
        else:
            ok = self._download_requests(dest)

        self.finished.emit(ok)

    def _download_aria2c(self, dest: Path) -> bool:
        status_file = self.target_dir / f"{self.filename}.status"
        cmd = [
            self.aria2c_path,
            "-x16", "-s16", "-j16", "-k1M",
            "--always-resume=true",
            "--allow-overwrite=true",
            "--check-certificate=false",
            f"--dir={self.target_dir}",
            f"--out={self.filename}",
            self.url,
            f"--log={status_file}",
        ]
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
        )
        for line in proc.stdout:
            if self._cancelled:
                proc.terminate()
                return False
            pct = _parse_aria2c_progress(line)
            if pct is not None:
                self.progress.emit(pct)
            spd = _parse_aria2c_speed(line)
            if spd:
                self.speed.emit(spd)
        proc.wait()
        if status_file.exists():
            status_file.unlink(missing_ok=True)
        return dest.exists() and dest.stat().st_size > 0

    def _download_requests(self, dest: Path) -> bool:
        """Fallback: stream download via requests with progress reporting."""
        try:
            import requests
            with requests.get(self.url, stream=True, timeout=30,
                              verify=False) as r:
                r.raise_for_status()
                total = int(r.headers.get("content-length", 0))
                downloaded = 0
                with open(dest, "wb") as fh:
                    for chunk in r.iter_content(chunk_size=65536):
                        if self._cancelled:
                            return False
                        fh.write(chunk)
                        downloaded += len(chunk)
                        if total:
                            self.progress.emit(int(downloaded / total * 100))
            return dest.exists() and dest.stat().st_size > 0
        except Exception as exc:
            self.error.emit(str(exc))
            return False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_aria2c_progress(line: str) -> int | None:
    """Extract percentage from an aria2c stdout line."""
    if "%" not in line:
        return None
    try:
        idx = line.index("%")
        chunk = line[max(0, idx - 4):idx].strip()
        digits = "".join(c for c in chunk if c.isdigit())
        if digits:
            return min(100, int(digits))
    except Exception:
        pass
    return None


def _parse_aria2c_speed(line: str) -> str:
    """Extract speed string from an aria2c stdout line."""
    for token in line.split():
        if token.endswith("/s") and any(c.isdigit() for c in token):
            return token
    return ""
