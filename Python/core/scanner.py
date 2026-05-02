from data.systems import SystemEntry
import logging
import string
import fnmatch
import platform
import json
from pathlib import Path
from core.config import global_config
from data.systems import SystemRegistry
from utils.paths import assets_dir

class SystemScanner:
    """Service to handle drive discovery and system folder detection."""

    def __init__(self, registry: SystemRegistry):
        self._registry = registry
        self._app_root = registry._app_root

    def discover_primary_dirs(self) -> tuple[list[str], list[str]]:
        """
        Scan drive roots for top-level folders that likely contain ROMs or Emulators.
        Appends found paths to Settings.json.
        """
        drives = self._get_all_drives()
        sys_roots = ["Console", "* ROMs", "Emulated*", "Systems", "*No-Intro*", "*TOSEC*", "*Redump"]
        emu_roots = ["Emulators", "Emu", "Emulation", "ROM programs", "ROM apps", "Frontends", "Utils"]
        
        found_sys = []
        found_emu = []
        
        for d in drives:
            try:
                self._scan_dir(d, sys_roots, emu_roots, found_sys, found_emu)
                for sub in d.iterdir():
                    if sub.is_dir() and sub.name.lower() in ["games", "emulation", "roms"]:
                        self._scan_dir(sub, sys_roots, emu_roots, found_sys, found_emu)
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

    def detect_systems(self, root_dir: Path | None = None, log_callback=None):
        """Scan directories for folders matching fuzzy patterns."""
        def _log(msg):
            if log_callback: log_callback(msg)
            else: logging.debug(msg)

        targets = [root_dir] if root_dir else self._get_targets_for_detection()
        fuz_path = assets_dir() / "fuzsyslk.json"
        if not fuz_path.exists():
            _log(f"Detection aborted. Pattern file missing: {fuz_path}")
            return

        pattern_list = []
        try:
            with open(fuz_path, "r", encoding="utf-8") as f:
                patterns_data = json.load(f)
            for entry in patterns_data:
                full_name = entry.get("name")
                terms_raw = entry.get("search_terms", [])
                if isinstance(terms_raw, str):
                    terms_raw = [terms_raw]
                
                patterns = []
                for term in terms_raw:
                    patterns.extend([p.strip().lower() for p in term.split("|") if p.strip()])

                patterns.append(full_name.lower())
                pattern_list.append((full_name, patterns))
        except Exception as e:
            _log(f"Error parsing fuzsyslk.json: {e}")
            return

        for target in targets:
            if not target or not target.exists(): continue
            _log(f"Scanning master directory: {target}")
            try:
                folders = [f for f in target.iterdir() if f.is_dir()]
                while folders:
                    any_match_this_pass = False
                    
                    # Pass 1: Exact matches (Highest Priority)
                    for full_name, patterns in pattern_list:
                        matched_folder = None
                        for folder in folders:
                            if folder.name.lower() == full_name.lower():
                                matched_folder = folder
                                break
                        
                        if matched_folder:
                            _log(f"Found Exact Match! Folder '{matched_folder.name}' matches '{full_name}'")
                            self._assign_folder_to_system(full_name, matched_folder)
                            folders.remove(matched_folder)
                            any_match_this_pass = True

                    # Pass 2: Fuzzy matches (Only if exact name wasn't found in this pass)
                    for full_name, patterns in pattern_list:
                        matched_folder = None
                        for folder in folders:
                            if any(fnmatch.fnmatch(folder.name.lower(), p) for p in patterns):
                                matched_folder = folder
                                break
                        
                        if matched_folder:
                            _log(f"Found Fuzzy Match! Folder '{matched_folder.name}' matches '{full_name}'")
                            self._assign_folder_to_system(full_name, matched_folder)
                            folders.remove(matched_folder)
                            any_match_this_pass = True

                    if not any_match_this_pass:
                        break
            except (PermissionError, OSError): continue

    def exact_match_scan(self, category: str) -> list[str]:
        """Search drive roots for exact folder name matches against known systems."""
        drives = self._get_all_drives()
        found = []
        if category == "Systems":
            names = self._registry.all_systems()
            for d in drives:
                try:
                    for item in d.iterdir():
                        if item.is_dir() and any(n.lower() == item.name.lower() for n in names):
                            self._assign_folder_to_system(item.name, item)
                            found.append(f"{item.name} -> {item}")
                except (PermissionError, OSError): continue
            self._registry.save()
        return found

    def _assign_folder_to_system(self, system_name: str, folder: Path):
        """Helper to append a folder path to a system's registry entry."""
        # Ensure system exists in registry; bootstrapping if both JSONs were empty/corrupt
        if system_name not in self._registry._data:
            self._registry._data[system_name] = SystemEntry(name=system_name)

        entry = self._registry._data[system_name]
        
        # Normalize the path to prevent duplicates like 'C:/Roms' vs 'C:\Roms\'
        try:
            norm_path = str(folder.resolve())
        except Exception:
            norm_path = str(folder)

        if norm_path not in entry.rom_path_list:
            current = entry.rom_paths
            # Append new paths to the end using the pipe delimiter
            new_path = f"{current}|{norm_path}" if current else norm_path
            self._registry.set_path(system_name, new_path)

    def _scan_dir(self, path: Path, sys_p: list, emu_p: list, found_s: list, found_e: list):
        try:
            for item in path.iterdir():
                if not item.is_dir(): continue
                name = item.name.lower()
                for p in sys_p:
                    if fnmatch.fnmatch(name, p.lower()) and str(item) not in found_s:
                        found_s.append(str(item))
                        break
                for p in emu_p:
                    if fnmatch.fnmatch(name, p.lower()) and str(item) not in found_e:
                        found_e.append(str(item))
                        break
        except (PermissionError, OSError): pass

    def _get_all_drives(self) -> list[Path]:
        drives = []
        if platform.system() == "Windows":
            for d in string.ascii_uppercase:
                drive = Path(f"{d}:/")
                if drive.exists():
                    drives.append(drive)
        else:
            drives.append(Path("/"))
        return drives

    def _get_targets_for_detection(self) -> list[Path]:
        cfg = global_config()
        raw = cfg.get("GLOBAL", "systems_directory", fallback="")
        paths = [Path(p.strip()) for p in raw.split("|") if p.strip() and Path(p.strip()).exists()]
        for d in self._get_all_drives():
            if d not in paths:
                paths.append(d)
        return paths