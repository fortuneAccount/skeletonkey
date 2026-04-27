"""
data/systems.py

Loads and exposes the system/console metadata from Systems.json (assets)
and user path overrides from systems.json (generated).
"""
import json
import fnmatch
import platform
import string
from typing import Any
from pathlib import Path
from dataclasses import dataclass, field, asdict
from core.config import global_config
from utils.paths import app_root
from utils.paths import app_root, assets_dir


@dataclass
class SystemEntry:
    """Typed representation of a system/console and its associated paths/metadata."""
    name: str
    rom_paths: str = ""
    platform: str = ""
    extensions: list[str] = field(default_factory=list)
    supported_emus: list[str] = field(default_factory=list)
    supported_cores: list[str] = field(default_factory=list)
    emu_reset: str = ""  # EMUPRESET - preferred emulator

    @property
    def rom_path_list(self) -> list[str]:
        """Return the ROM paths as a cleaned list."""
        if not self.rom_paths: return []
        return [p.strip() for p in self.rom_paths.split('|') if p.strip()]


class SystemRegistry:
    """
    Provides a dict-like view of all known systems and their ROM paths.
    """

    def __init__(self, home: Path | None = None):
        self._user_home = home or global_config().home
        self._app_root = app_root()
        self._data: dict[str, SystemEntry] = {}

        self._load_master_list()
        self._load()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _load_master_list(self):
        """Load the master list of supported systems from the new Systems.json asset."""
        master_json = self._app_root / "assets" / "Systems.json"
        master_json = assets_dir() / "Systems.json"
        if not master_json.exists():
            return

        try:
            with open(master_json, "r", encoding="utf-8") as f:
                master_data = json.load(f)
                # Support both flat and nested "systems" keys
                systems_dict = master_data.get("systems", master_data)
                for name, info in systems_dict.items():
                    if not isinstance(info, dict):
                        continue
                        
                    # Handle both comma and pipe delimiters for extensions
                    romxt_raw = info.get("extensions", info.get("RJROMXT", info.get("ROMXT", "")))
                    if "|" in romxt_raw:
                        extensions = [x.strip() for x in romxt_raw.split("|") if x.strip()]
                    else:
                        extensions = [x.strip() for x in romxt_raw.split(",") if x.strip()]
                    
                    self._data[name] = SystemEntry(
                        name=name,
                        rom_paths="",
                        platform=info.get("platform", info.get("SHORTNM", "")),
                        extensions=extensions,
                        supported_emus=[x.strip() for x in info.get("supported_emus", info.get("SUPEMU", "")).split("|") if x.strip()],
                        supported_cores=[x.strip() for x in info.get("supported_cores", info.get("SUPCORE", "")).split("|") if x.strip()],
                        emu_reset=info.get("EMUPRESET", "")
                    )
        except Exception:
            pass

    def _load(self):
        """Load user systems.json with resilient handling for mangled or corrupt entries."""
        path = self._user_home / "systems.json"
        if not path.exists():
            return

        try:
            with open(path, "r", encoding="utf-8") as f:
                user_data = json.load(f)
                systems_dict = user_data.get("systems", user_data)
                for name, info in systems_dict.items():
                    if isinstance(info, str):
                        info = {"rom_paths": info}
                    
                    if name in self._data:
                        self._data[name].rom_paths = info.get("rom_paths", "")
                    else:
                        self._data[name] = SystemEntry(
                            name=name,
                            rom_paths=info.get("rom_paths", "")
                        )
        except (json.JSONDecodeError, KeyError, IOError, AttributeError) as e:
            print(f"DEBUG: Resilient load active. Skipping mangled user file: {e}")
            # If corrupted, we continue with the master list data already in self._data
            return

    def reload(self):
        self._data.clear()
        self._load_master_list()
        self._load()

    # ------------------------------------------------------------------
    # Public Data Management API
    # ------------------------------------------------------------------

    def all_systems(self) -> list[str]:
        """Return sorted list of all system names."""
        # Strictly enforce hyphenated naming convention (Manufacturer - Console)
        return sorted(name for name in self._data.keys() if " - " in name)

    def get_path(self, system: str) -> str:
        """Return the pipe-delimited ROM directories for *system*."""
        entry = self._data.get(system)
        return entry.rom_paths if entry else ""

    def set_path(self, system: str, path: str):
        """Update the ROM directory for *system* in memory."""
        if system not in self._data:
            self._data[system] = SystemEntry(name=system)
        self._data[system].rom_paths = path

    def save(self):
        """Persist unified systems data to JSON."""
        dest = self._user_home / "systems.json"
        dest.parent.mkdir(parents=True, exist_ok=True)
        # Filter to only save systems that have ROM paths defined
        to_save = {s.name: asdict(s) for s in self._data.values() if s.rom_paths}
        with open(dest, "w", encoding="utf-8") as f:
            json.dump(to_save, f, indent=4)

    def __len__(self) -> int:
        return len(self._data)

    def __contains__(self, system: str) -> bool:
        return system in self._data
