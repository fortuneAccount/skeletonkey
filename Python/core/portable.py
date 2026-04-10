"""
core/portable.py

Portable mode utilities.
Replaces PortableUtil.ahk.

Handles path migration when the application is moved to a new drive/directory.
"""
import os
import shutil
from pathlib import Path

from core.config import Config, global_config

# Config file extensions that may contain absolute paths
_CFG_EXTENSIONS = {".ini", ".cfg", ".config", ".conf", ".xml", ".settings", ".opt"}

# Core config files that live in the app home directory
_CORE_CFG_FILES = [
    "ovr.ini", "hashdb.ini", "Assignments.ini",
    "AppParams.ini", "Settings.ini", "config.cfg",
]


class PortableUtil:
    """
    Migrates all stored paths from an old root to a new root.

    Equivalent to the InjPortable / SeekRep / TglRep logic in PortableUtil.ahk.
    """

    def __init__(self, home: Path | None = None):
        self._home = home or Path(__file__).resolve().parent.parent
        self._settings = global_config()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def migrate(
        self,
        old_prefix: str,
        new_prefix: str,
        also_migrate_playlists: bool = True,
    ) -> list[str]:
        """
        Replace *old_prefix* with *new_prefix* in all config and playlist files.

        Returns a list of files that were modified.
        """
        modified: list[str] = []

        # Core config files
        for name in _CORE_CFG_FILES:
            path = self._home / name
            if path.exists():
                if self._replace_in_file(path, old_prefix, new_prefix):
                    modified.append(str(path))

        # All config files under cfg/
        cfg_dir = self._home / "cfg"
        if cfg_dir.exists():
            for f in cfg_dir.rglob("*"):
                if f.suffix.lower() in _CFG_EXTENSIONS:
                    if self._replace_in_file(f, old_prefix, new_prefix):
                        modified.append(str(f))

        # Playlists
        if also_migrate_playlists:
            systems_dir = self._settings.get("GLOBAL", "systems_directory", "")
            if systems_dir:
                for lpl in Path(systems_dir).rglob("*.lpl"):
                    if self._replace_in_file(lpl, old_prefix, new_prefix):
                        modified.append(str(lpl))

        return modified

    def update_systems_directory(self, new_path: str):
        self._settings.set("GLOBAL", "systems_directory", new_path)
        self._settings.save()

    def update_cache_directory(self, new_path: str):
        self._settings.set("OPTIONS", "temp_location", new_path)
        self._settings.save()

    def create_desktop_shortcut(self, target_exe: str, icon_path: str = ""):
        """Create a desktop shortcut (Windows only)."""
        if os.name != "nt":
            return
        try:
            import winshell  # type: ignore
            desktop = winshell.desktop()
            lnk_path = os.path.join(desktop, "skeletonKey.lnk")
            with winshell.shortcut(lnk_path) as link:
                link.path = target_exe
                link.working_directory = str(Path(target_exe).parent)
                if icon_path:
                    link.icon_location = (icon_path, 0)
        except ImportError:
            pass  # winshell not available; skip silently

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    @staticmethod
    def _replace_in_file(path: Path, old: str, new: str) -> bool:
        """Replace all occurrences of *old* with *new* in *path* in-place."""
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
            if old not in text:
                return False
            backup = path.with_suffix(path.suffix + ".bak")
            shutil.copy2(path, backup)
            path.write_text(text.replace(old, new), encoding="utf-8")
            return True
        except Exception:
            return False
