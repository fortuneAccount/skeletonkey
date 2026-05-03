"""
data/systems.py

Loads and exposes the system/console metadata from Systems.json (assets)
and user path overrides from syscfg.json (root).
"""
import json
import fnmatch
import logging
import platform
import string
import re
from typing import Any
from pathlib import Path
from dataclasses import dataclass, field, asdict
from core.config import global_config
from utils.paths import app_root, assets_dir


@dataclass
class SystemEntry:
    """Typed representation of a system/console and its associated paths/metadata."""
    name: str
    rom_paths: list[str] = field(default_factory=list)
    platform: str = ""
    extensions: list[str] = field(default_factory=list)
    supported_emus: list[str] = field(default_factory=list)
    supported_cores: list[str] = field(default_factory=list)
    emu_reset: str = ""  # EMUPRESET - preferred emulator
    # Dynamic fields like MAME_EMUOPTS, Altirra_EMUARGS, etc.
    extra_metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def rom_path_list(self) -> list[str]:
        """Return the ROM paths as a cleaned list."""
        return self.rom_paths


class SystemRegistry:
    """
    Provides a dict-like view of all known systems and their ROM paths.
    """

    def __init__(self, home: Path | None = None):
        self._user_home = home or global_config().home
        self._systems_config_dir = self._user_home / "systems"
        self._systems_config_dir.mkdir(parents=True, exist_ok=True)
        
        self._app_root = app_root()
        self._data: dict[str, SystemEntry] = {}

        self._load_master_list()
        self._load()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _load_master_list(self):
        """Load the master list of supported systems from the new Systems.json asset."""
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
                    
                    def _get_list(key_list):
                        """Handle both lists and delimited strings in metadata."""
                        for k in key_list:
                            val = info.get(k)
                            if isinstance(val, list):
                                return [str(x).strip() for x in val]
                            if isinstance(val, str) and val:
                                sep = "|" if "|" in val else ","
                                return [x.strip() for x in val.split(sep) if x.strip()]
                        return []
                        
                    extensions = _get_list(["extensions", "RJROMXT", "ROMXT"])
                    
                    extra = {}
                    for k, v in info.items():
                        k_up = k.upper()
                        if k_up.endswith("OPTS") or k_up.endswith("ARGS") or k_up == "LAST_EMU":
                            extra[k] = v

                    self._data[name] = SystemEntry(
                        name=name,
                        rom_paths=[],
                        platform=info.get("platform", info.get("SHORTNM", "")),
                        extensions=extensions,
                        supported_emus=_get_list(["supported_emus", "SUPEMU"]),
                        supported_cores=_get_list(["supported_cores", "SUPCORE"]),
                        emu_reset=info.get("EMUPRESET", ""),
                        extra_metadata=extra
                    )
        except Exception as e:
            logging.error(f"Error loading system entry from master list: {e}")

    def _load(self):
        """
                Load segmented system JSON files from the configs/systems directory.
        """
        if not self._systems_config_dir.exists():
            return

            # Load every .json file in the systems config directory
        for config_file in self._systems_config_dir.glob("*.json"):
            try:
                with open(config_file, "r", encoding="utf-8") as f:
                    info = json.load(f)
                    name = config_file.stem  # Filename is the system name
                    
                    raw_paths = info.get("rom_paths", [])
                    def _parse_paths(val):
                        if isinstance(val, list): return [str(x).strip() for x in val]
                        if isinstance(val, str): return [x.strip() for x in val.split('|') if x.strip()]
                        return []

                    path_list = _parse_paths(raw_paths)
                    extra = {}
                    for k, v in info.items():
                        if k not in ["rom_paths", "extensions", "platform"]:
                            for k, v in info.items():
                                k_up = k.upper()
                                if k_up.endswith("OPTS") or k_up.endswith("ARGS") or k_up == "LAST_EMU":
                                    extra[k] = v

                    if name in self._data:
                        self._data[name].rom_paths = path_list
                        self._data[name].extra_metadata.update(extra)
                    else:
                        self._data[name] = SystemEntry(name=name, rom_paths=path_list, extra_metadata=extra)
            except Exception as e:
                logging.error(f"Failed to load segmented config {config_file.name}: {e}")

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
        return "|".join(entry.rom_paths) if entry else ""

    def set_path(self, system: str, path: str | list[str]):
        """Update the ROM directory for *system* in memory."""
        if system not in self._data:
            self._data[system] = SystemEntry(name=system)
        if isinstance(path, str):
            self._data[system].rom_paths = [p.strip() for p in path.split('|') if p.strip()]
        else:
            self._data[system].rom_paths = path

    def save(self):
        """Save each modified system into its own individual JSON file."""
        self._systems_config_dir.mkdir(parents=True, exist_ok=True)
        for name, entry in self._data.items():
            if entry.rom_paths or entry.extra_metadata or entry.extensions:
                data = { "rom_paths": entry.rom_paths, "extensions": entry.extensions, "platform": entry.platform }
                data.update(entry.extra_metadata)
                
                dest = self._systems_config_dir / f"{name}.json"
                with open(dest, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=4)
    def __len__(self) -> int:
        return len(self._data)

    def __contains__(self, system: str) -> bool:
        return system in self._data

    def get_emu_metadata(self, system: str, emu_name: str) -> tuple[list[str], list[str]]:
        """
        Retrieve options and arguments for a system/emulator combination.
        Handles fuzzy matching (e.g. MAMEOPTS for 'mame-x64') and core names.
        """
        entry = self._data.get(system)
        if not entry or not emu_name:
            return [], []

        def split_val(v):
            if not v: return []
            if isinstance(v, list):
                return [str(x) for x in v if str(x)]
            return [x for x in re.split(r'[<|\n]', str(v)) if x]

        # Derive a base name for matching (e.g. 'mame_libretro' -> 'mame', 'mame64' -> 'mame')
        clean_emu = emu_name.lower().replace("_libretro", "")
        clean_emu = re.sub(r"[\s\.\-_)]?(?:x64|x86|64|32|win|amd|sse|avx).*", "", clean_emu)

        opts = []
        args = []

        # 1. Exact matches for user-saved LAST_EMU specific keys
        for k, v in entry.extra_metadata.items():
            k_low = k.lower()
            if k_low == f"{emu_name}_opts".lower():
                opts.extend(split_val(v))
            elif k_low == f"{emu_name}_args".lower():
                args.extend(split_val(v))

        # 2. Fuzzy matches for asset-defined keys (e.g. MAMEOPTS)
        for k, v in entry.extra_metadata.items():
            k_low = k.lower()
            # Skip if already added as an exact match
            if k_low == f"{emu_name}_opts".lower() or k_low == f"{emu_name}_args".lower():
                continue
            
            if clean_emu in k_low or k_low in ["emuopts", "emuargs"]:
                if "opts" in k_low:
                    opts.extend(split_val(v))
                elif "args" in k_low:
                    args.extend(split_val(v))
        
        return opts, args
