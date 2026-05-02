"""
utils/archive.py

Archive extraction wrapper (7z, zip, rar).
Uses the bundled 7za.exe on Windows or the system 7z on other platforms.
"""
import logging
import subprocess
import zipfile
from pathlib import Path

from utils.paths import find_binary, bin_dir

logger = logging.getLogger(__name__)


def _seven_zip_exe() -> str:
    """Locate 7za / 7z executable."""
    for name in ("7za.exe", "7z.exe", "7za", "7z"):
        found = find_binary(name)
        if found:
            return str(found)
    # Fall back to PATH
    return "7z"


def extract(archive_path: str, dest_dir: str, overwrite: bool = True) -> bool:
    """
    Extract *archive_path* into *dest_dir*.

    Supports .7z, .zip, .rar and any format 7z handles.
    Returns True on success.
    """
    src = Path(archive_path)
    dst = Path(dest_dir)
    dst.mkdir(parents=True, exist_ok=True)

    if src.suffix.lower() == ".zip":
        return _extract_zip(src, dst)
    return _extract_7z(src, dst, overwrite)


def _extract_zip(src: Path, dst: Path) -> bool:
    try:
        with zipfile.ZipFile(src, "r") as zf:
            zf.extractall(dst)
        logger.debug(f"Successfully extracted ZIP: {src} -> {dst}")
        return True
    except Exception as e:
        logger.error(f"Failed to extract ZIP {src}: {e}")
        return False


def _extract_7z(src: Path, dst: Path, overwrite: bool) -> bool:
    exe = _seven_zip_exe()
    flags = ["-y"] if overwrite else []
    cmd = [exe, "x", *flags, str(src), f"-o{dst}"]
    result = subprocess.run(cmd, capture_output=True)
    if result.returncode == 0:
        logger.debug(f"Successfully extracted 7z: {src} -> {dst}")
        return True
    else:
        logger.error(f"Failed to extract 7z {src}: {result.stderr.decode() if result.stderr else 'unknown error'}")
        return False


def compress(source_dir: str, archive_path: str) -> bool:
    """Create a .7z archive from *source_dir*."""
    exe = _seven_zip_exe()
    cmd = [exe, "a", "-y", archive_path, str(Path(source_dir) / "*")]
    result = subprocess.run(cmd, capture_output=True)
    if result.returncode == 0:
        logger.debug(f"Successfully compressed: {source_dir} -> {archive_path}")
        return True
    else:
        logger.error(f"Failed to compress {source_dir}: {result.stderr.decode() if result.stderr else 'unknown error'}")
        return False
