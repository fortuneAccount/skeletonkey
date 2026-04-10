"""
data/systems.py

Loads and exposes the system/console list from SystemLocations.set (template)
and SystemLocations.ini (user overrides).

The .set file format is a standard INI [LOCATIONS] section where each key is
a system name and the value is the path to that system's ROM directory.
"""
import fnmatch
import configparser
from pathlib import Path


def _home() -> Path:
    return Path(__file__).resolve().parent.parent.parent


class SystemRegistry:
    """
    Provides a dict-like view of all known systems and their ROM paths.

    Priority: generated/SystemLocations.ini (user) > assets/SystemLocations.set (defaults)
    """

    SECTION = "LOCATIONS"

    def __init__(self, home: Path | None = None):
        self._home = home or _home()
        self._data: dict[str, str] = {}
        self._load()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _load(self):
        parser = configparser.RawConfigParser(strict=False)
        parser.optionxform = str  # preserve case

        # Load defaults from assets/SystemLocations.set
        default_set = self._home / "assets" / "SystemLocations.set"
        if default_set.exists():
            try:
                parser.read(default_set, encoding="utf-8-sig")
            except UnicodeDecodeError:
                parser.read(default_set, encoding="utf-16")

        # Overlay user values from generated/SystemLocations.ini
        user_ini = self._home / "SystemLocations.ini"
        if user_ini.exists():
            try:
                parser.read(user_ini, encoding="utf-8-sig")
            except UnicodeDecodeError:
                parser.read(user_ini, encoding="utf-16")

        if parser.has_section(self.SECTION):
            for key, val in parser.items(self.SECTION):
                self._data[key] = val.strip('"')

    def reload(self):
        self._data.clear()
        self._load()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def detect_systems(self, root_dir: Path):
        """
        Scan root_dir for folders matching patterns in fuzsyslk.set.
        Mirrors the SYSDETECT logic from working.ahk.
        """
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
                
                if not root_dir.exists():
                    continue

                # Scan folders directly under root_dir (mirrors AHK Loop, %dir%\%pattern%, 2)
                for item in root_dir.iterdir():
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

    def all_systems(self) -> list[str]:
        """Return sorted list of all system names."""
        return sorted(self._data.keys())

    def get_path(self, system: str) -> str:
        """Return the ROM directory for *system*, or empty string."""
        return self._data.get(system, "")

    def set_path(self, system: str, path: str):
        """Update the ROM directory for *system* in memory."""
        self._data[system] = path

    def save(self):
        """Persist user overrides to SystemLocations.ini."""
        parser = configparser.RawConfigParser()
        parser.optionxform = str
        parser.add_section(self.SECTION)
        for k, v in self._data.items():
            parser.set(self.SECTION, k, f'"{v}"')
        dest = self._home / "SystemLocations.ini"
        with open(dest, "w", encoding="utf-8") as fh:
            parser.write(fh)

    def __len__(self) -> int:
        return len(self._data)

    def __contains__(self, system: str) -> bool:
        return system in self._data
