"""
core/config.py

Centralised INI-based settings manager.
Replaces the AHK gets.ahk / sets.ahk pattern of IniRead / IniWrite calls.

All paths are resolved relative to the application home directory so the
app remains portable.
"""
import json
import os
from pathlib import Path


def _home() -> Path:
    """Return the application root directory (parent of the Python folder)."""
    return Path(__file__).resolve().parent.parent.parent

def _get_config_root() -> Path:
    """Determine config location based on portable status."""
    home = _home()
    # Enable portable mode by default on first run if no config exists
    portable_flag = home / "portable.txt"
    if not portable_flag.exists() and not (Path(os.environ.get("APPDATA", "")) / "skeletonkey").exists():
        portable_flag.write_text(f"{home}\ntrue", encoding="utf-8")

    if (home / "portable.txt").exists():
        return home
    appdata = Path(os.environ.get("APPDATA", str(home))) / "skeletonkey"
    appdata.mkdir(parents=True, exist_ok=True)
    return appdata


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
        self._home = home or _get_config_root()
        self._path = self._home / filename.replace(".ini", ".json")
        self._data = {}
        self._load()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load(self):
        """Load JSON data with legacy INI fallback if necessary."""
        if not self._path.exists():
            return
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                self._data = json.load(f)
        except (json.JSONDecodeError, IOError):
            self._data = {}

    def reload(self):
        self._data = {}
        self._load()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get(self, section: str, key: str, fallback: str = "") -> str:
        """Read a value; returns *fallback* when section/key is absent."""
        # Handle default application overrides for the new paradigm
        if section == "GLOBAL":
            if key == "Logging" and "GLOBAL" not in self._data: return "1"
            if key == "first_run" and "GLOBAL" not in self._data: return "1"

        val = self._data.get(section, {}).get(key, fallback)
        return str(val).strip('"')

    def set(self, section: str, key: str, value: str):
        """Write a value into memory (call save() to persist)."""
        if section not in self._data:
            self._data[section] = {}
        self._data[section][key] = value

    def save(self):
        """Persist all in-memory changes back to disk."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._path, "w", encoding="utf-8") as fh:
            json.dump(self._data, fh, indent=4)

    def sections(self) -> list[str]:
        return list(self._data.keys())

    def items(self, section: str) -> list[tuple[str, str]]:
        """Return all key/value pairs in *section* (empty list if missing)."""
        section_data = self._data.get(section, {})
        return [(k, str(v).strip('"')) for k, v in section_data.items()]

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
