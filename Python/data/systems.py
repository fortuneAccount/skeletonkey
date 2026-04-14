"""
data/systems.py

Loads and exposes the system/console list from SystemLocations.set (template)
and SystemLocations.ini (user overrides).

The .set file format is a standard INI [LOCATIONS] section where each key is
a system name and the value is the path to that system's ROM directory.
"""
import os
import string
import fnmatch
import configparser
import json
from typing import Any
from pathlib import Path
from core.config import global_config


def _home() -> Path:
    return Path(__file__).resolve().parent.parent.parent


class SystemRegistry:
    """
    Provides a dict-like view of all known systems and their ROM paths.

    Priority: generated/SystemLocations.ini (user) > assets/SystemLocations.set (defaults)
    """

    SECTION = "LOCATIONS"

    def __init__(self, home: Path | None = None):
        self._home = home or global_config().home
        self._data: dict[str, Any] = {}
        self._load()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _get_all_drives(self) -> list[Path]:
        """Returns a list of all accessible drive root paths."""
        drives = []
        if os.name == 'nt':
            for d in string.ascii_uppercase:
                path = Path(f"{d}:/")
                if path.exists():
                    drives.append(path)
        return drives

    def _load(self):
        """Load unified systems.json with fallback migration from legacy INI/SET files."""
        path = self._home / "systems.json"
        
        if path.exists():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    self._data = json.load(f)
                    return
            except Exception:
                self._data = {}

        # Migration Logic: If JSON doesn't exist, import from legacy files
        parser = configparser.RawConfigParser(strict=False)
        parser.optionxform = str  # type: ignore[assignment]  # preserve case

        default_set = self._home / "assets" / "SystemLocations.set"
        if default_set.exists():
            try:
                parser.read(default_set, encoding="utf-8-sig")
            except UnicodeDecodeError:
                parser.read(default_set, encoding="utf-16")

        user_ini = self._home / "SystemLocations.ini"
        if user_ini.exists():
            try:
                parser.read(user_ini, encoding="utf-8-sig")
            except UnicodeDecodeError:
                parser.read(user_ini, encoding="utf-16")

        if parser.has_section(self.SECTION):
            for key, val in parser.items(self.SECTION):
                # Structure data for the new JSON paradigm
                self._data[key] = {"rom_paths": val.strip('"')}
            
            # Save the new JSON format immediately to complete migration
            self.save()

    def reload(self):
        self._data.clear()
        self._load()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def discover_primary_dirs(self) -> tuple[list[str], list[str]]:
        """
        Scan drive roots for top-level folders that likely contain ROMs or Emulators.
        Appends found paths to Settings.ini.
        Returns: (found_systems, found_emus)
        """
        drives = self._get_all_drives()
        sys_roots = ["Console", "* ROMs", "Emulated*", "Systems", "*No-Intro*", "*TOSEC*", "*Redump"]
        emu_roots = ["Emulators", "Emu", "Emulation", "ROM programs", "ROM apps", "Frontends", "Utils"]
        
        found_sys = []
        found_emu = []
        
        for d in drives:
            try:
                for item in d.iterdir():
                    if not item.is_dir(): continue
                    name = item.name.lower()
                    # Check ROM roots
                    for p in sys_roots:
                        if fnmatch.fnmatch(name, p.lower()):
                            found_sys.append(str(item))
                            break
                    # Check Emu roots
                    for p in emu_roots:
                        if fnmatch.fnmatch(name, p.lower()):
                            found_emu.append(str(item))
                            break
            except (PermissionError, OSError):
                continue
        
        cfg = global_config()
        def _append_paths(key, new_paths):
            existing = cfg.get("GLOBAL", key, fallback="")
            parts = [p.strip() for p in existing.split("|") if p.strip()]
            for p in new_paths:
                if p not in parts: parts.append(p)
            cfg.set("GLOBAL", key, "|".join(parts))

        if found_sys: _append_paths("systems_directory", found_sys)
        if found_emu: _append_paths("emulators_directory", found_emu)
        cfg.save()
        return found_sys, found_emu

    def detect_systems(self, root_dir: Path | None = None):
        """
        Scan established directories or drive roots for folders matching patterns in fuzsyslk.set.
        Mirrors the SYSDETECT logic from working.ahk.
        """
        targets = [root_dir] if root_dir else self._get_targets_for_detection()
        
        fuz_path = self._home / "assets" / "fuzsyslk.set"
        if not fuz_path.exists():
            return

        # Patterns are in format: pattern1|pattern2>Internal Name
        with open(fuz_path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line or ">" not in line:
                    continue
                
                patterns_raw, internal_name = line.split(">", 1)
                patterns = [p.strip() for p in patterns_raw.split("|") if p.strip()]

                for target in targets:
                    if not target or not target.exists():
                        continue

                    try:
                        # Scan folders directly under target
                        for item in target.iterdir():
                            if not item.is_dir():
                                continue
                            
                            for p in patterns:
                                if fnmatch.fnmatch(item.name.lower(), p.lower()):
                                    current = self.get_path(internal_name)
                                    # Append if not already in the list (mirrors |%fik% logic)
                                    if str(item) not in current:
                                        new_path = f"{item}|{current}" if current else str(item)
                                        self.set_path(internal_name, new_path)
                                    break
                    except (PermissionError, OSError):
                        continue

    def _get_targets_for_detection(self) -> list[Path]:
        """Returns list of paths to scan. Priority: Settings.ini dirs, then drive roots."""
        cfg = global_config()
        raw = cfg.get("GLOBAL", "systems_directory", fallback="")
        paths = [Path(p.strip()) for p in raw.split("|") if p.strip() and Path(p.strip()).exists()]
        
        # Add drive roots as fallbacks/primary search for exact matches later
        for d in self._get_all_drives():
            if d not in paths:
                paths.append(d)
        return paths

    def exact_match_scan(self, category: str) -> list[str]:
        """Search drive roots for exact matches of known systems."""
        drives = self._get_all_drives()
        found = []
        if category == "Systems":
            names = self.all_systems()
            for d in drives:
                try:
                    for item in d.iterdir():
                        if item.is_dir() and any(n.lower() == item.name.lower() for n in names):
                            current = self.get_path(item.name)
                            if str(item) not in current:
                                new_path = f"{item}|{current}" if current else str(item)
                                self.set_path(item.name, new_path)
                                found.append(f"{item.name} -> {item}")
                except (PermissionError, OSError):
                    continue
            self.save()
        return found

    def all_systems(self) -> list[str]:
        """Return sorted list of all system names."""
        return sorted(self._data.keys())

    def get_path(self, system: str) -> str:
        """Return the pipe-delimited ROM directories for *system*."""
        return self._data.get(system, {}).get("rom_paths", "")

    def set_path(self, system: str, path: str):
        """Update the ROM directory for *system* in memory."""
        if system not in self._data: self._data[system] = {}
        self._data[system]["rom_paths"] = path

    def save(self):
        """Persist unified systems data to JSON."""
        dest = self._home / "systems.json"
        with open(dest, "w", encoding="utf-8") as fh:
            json.dump(self._data, fh, indent=4)

    def __len__(self) -> int:
        return len(self._data)

    def __contains__(self, system: str) -> bool:
        return system in self._data
