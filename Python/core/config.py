"""
core/config.py

Centralised INI-based settings manager.
Replaces the AHK gets.ahk / sets.ahk pattern of IniRead / IniWrite calls.

All paths are resolved relative to the application home directory so the
app remains portable.
"""
import configparser
import os
from pathlib import Path


def _home() -> Path:
    """Return the application root directory (three levels up from this file)."""
    return Path(__file__).resolve().parent.parent.parent


class Config:
    """
    Thin wrapper around configparser that mirrors the AHK IniRead/IniWrite API.

    Usage:
        cfg = Config()
        value = cfg.get("GLOBAL", "systems_directory", fallback="")
        cfg.set("GLOBAL", "systems_directory", "/path/to/roms")
        cfg.save()
    """

    # Canonical INI files used by the application
    # User-modified configs go in generated/ directory
    SETTINGS_FILE = "Settings.ini"
    ASSIGNMENTS_FILE = "Assignments.ini"
    EMUCFG_FILE = "emucfgpresets.ini"
    SYSLOC_FILE = "SystemLocations.ini"
    APPS_FILE = "apps.ini"
    LAUNCHPARAMS_FILE = "launchparams.ini"
    LKUP_FILE = "lkup.ini"
    SYSINT_FILE = "sysint.ini"
    ARCORG_FILE = "arcorg.ini"

    def __init__(self, filename: str = SETTINGS_FILE, home: Path | None = None):
        self._home = home or _home()
        # Place user configs in generated/ directory
        generated_dir = self._home / "generated"
        generated_dir.mkdir(parents=True, exist_ok=True)
        self._path = generated_dir / filename
        self._parser = configparser.RawConfigParser()
        self._parser.optionxform = str  # preserve key case
        self._load()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load(self):
        if self._path.exists():
            self._read_with_fallback(self._path)

    def _read_with_fallback(self, path: Path):
        """Handles AHK-specific encodings (UTF-16 BOM, UTF-8-SIG)."""
        encodings = ["utf-8-sig", "utf-16", "cp1252"]
        for enc in encodings:
            try:
                self._parser.read(path, encoding=enc)
                return
            except (UnicodeDecodeError, configparser.Error):
                continue

    def reload(self):
        self._parser.clear()
        self._load()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get(self, section: str, key: str, fallback: str = "") -> str:
        """Read a value; returns *fallback* when section/key is absent."""
        try:
            raw = self._parser.get(section, key)
            # Strip surrounding quotes that AHK IniWrite sometimes adds
            return raw.strip('"')
        except (configparser.NoSectionError, configparser.NoOptionError):
            return fallback

    def set(self, section: str, key: str, value: str):
        """Write a value into memory (call save() to persist)."""
        if not self._parser.has_section(section):
            self._parser.add_section(section)
        self._parser.set(section, key, value)

    def save(self):
        """Persist all in-memory changes back to disk."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._path, "w", encoding="utf-8") as fh:
            self._parser.write(fh)

    def sections(self) -> list[str]:
        return self._parser.sections()

    def items(self, section: str) -> list[tuple[str, str]]:
        """Return all key/value pairs in *section* (empty list if missing)."""
        try:
            return [(k, v.strip('"')) for k, v in self._parser.items(section)]
        except configparser.NoSectionError:
            return []

    @property
    def path(self) -> Path:
        return self._path

    @property
    def home(self) -> Path:
        return self._home


# ---------------------------------------------------------------------------
# Module-level convenience: a singleton for Settings.ini
# ---------------------------------------------------------------------------
_global_cfg: Config | None = None


def global_config() -> Config:
    """Return the application-wide Settings.ini config (lazy singleton)."""
    global _global_cfg
    if _global_cfg is None:
        _global_cfg = Config(Config.SETTINGS_FILE)
    return _global_cfg
