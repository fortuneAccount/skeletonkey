"""
core/config.py

Centralised  AHK gets.ahk / sets.ahk pattern of IniRead / IniWrite calls.

All paths are resolved relative to the application home directory so the
app remains portable.
"""
import json
import logging
import os
import tempfile
from pathlib import Path


from utils.paths import config_home

logger = logging.getLogger(__name__)

class Config:
    """
    Manager for JSON-based settings files.

    Usage:
        cfg = Config()
        value = cfg.get("GLOBAL", "systems_directory", fallback="")
        cfg.set("GLOBAL", "systems_directory", "/path/to/roms")
        cfg.save()
    """

    SETTINGS_FILE = "Settings.json"
    ASSIGNMENTS_FILE = "Assignments.json"
    APPS_FILE = "apps.json"
    LAUNCHPARAMS_FILE = "launchparams.json"

    def __init__(self, filename: str = SETTINGS_FILE, home: Path | None = None):
        self._home = home or config_home()
        self._path = self._home / filename
        self._data = {}
        self._load()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load(self):
        """Load settings from the JSON file."""
        if not self._path.exists():
            return
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                self._data = json.load(f)
                logger.debug(f"Loaded config from {self._path}")
        except json.JSONDecodeError as e:
            logger.error(f"JSON decode error in {self._path}: {e}")
            self._data = {}
        except IOError as e:
            logger.error(f"IO error reading {self._path}: {e}")
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
        # Atomic save: write to temp file first to prevent 0-byte corruption on crash
        fd, temp_path = tempfile.mkstemp(dir=self._path.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as fh:
                json.dump(self._data, fh, indent=4)
            os.replace(temp_path, self._path)
        except Exception as e:
            if os.path.exists(temp_path):
                os.remove(temp_path)
            logger.error(f"Failed to save config {self._path}: {e}")

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


def setup_logging(cfg: Config):
    """Configure application logging based on config settings."""
    log_level_str = cfg.get("GLOBAL", "Logging", fallback="1")
    log_level = logging.INFO if log_level_str == "1" else logging.DEBUG
    
    # Create logger
    logger = logging.getLogger()
    logger.setLevel(log_level)
    
    # Remove existing handlers
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)
    
    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(log_level)
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)
    
    # File handler
    log_file = cfg.home / "skeletonkey.log"
    file_handler = logging.FileHandler(log_file)
    file_handler.setLevel(log_level)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)


# ---------------------------------------------------------------------------
# Module-level convenience: a singleton for Settings.ini
# ---------------------------------------------------------------------------
_global_cfg: Config | None = None


def global_config() -> Config:
    """Return the application-wide Settings.json config (lazy singleton)."""
    global _global_cfg
    if _global_cfg is None:
        _global_cfg = Config(Config.SETTINGS_FILE)
    return _global_cfg
