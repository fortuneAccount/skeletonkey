"""
data/emulators.py

Parses EmuParts.set which defines emulators, frontends, utilities and
keymappers in the format:

    (This is NOT a standard INI format; each line is parsed directly.)
    name<archive_path<exe<config_files<save_states<save_data

Each section ([EMULATORS], [FRONTENDS], [UTILITIES], [KEYMAPPERS]) is
parsed into typed dataclasses.
"""
import json
from dataclasses import dataclass, field
from pathlib import Path
from core.config import Config
from utils.paths import app_root

@dataclass
class EmuEntry:
    name: str
    archive: str = ""       # relative download path  e.g. "mame/mame-[ARCH].7z"
    exe: str = ""           # executable name          e.g. "mame.exe"
    configs: list[str] = field(default_factory=list)   # config file patterns
    save_states: list[str] = field(default_factory=list)
    save_data: list[str] = field(default_factory=list)
    extensions: list[str] = field(default_factory=list) # Supported ROM extensions
    required_files: list[str] = field(default_factory=list) # Bios/Firmware requirements
    category: str = "emulator"  # emulator | frontend | utility | keymapper
    pre_cfg: str = ""
    post_cfg: str = ""


class EmuRegistry:
    """
    Loads EmuParts.set and provides lookup by name or category.

    The file is NOT a standard INI – each line under a section header is
    a pipe-delimited record, not a key=value pair.  We parse it manually.
    """

    def __init__(self, home: Path | None = None):
        self._app_root = app_root()
        self._apps_cfg = Config(Config.APPS_FILE)
        self._entries: dict[str, EmuEntry] = {}
        self._load()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _load(self):
        src_json = self._app_root / "assets" / "emulators.json"

        if src_json.exists():
            try:
                with open(src_json, "r", encoding="utf-8") as f:
                    emu_data = json.load(f)
                    for name, info in emu_data.items():
                        self._entries[name.lower()] = EmuEntry(
                            name=name,
                            archive=info.get("URLPTH", ""),
                            exe=info.get("EXENAM", ""),
                            configs=info.get("CFGPTH", "").split("|"),
                            save_states=info.get("SaveStates", "").split("|"),
                            save_data=info.get("SaveData", "").split("|"),
                            extensions=info.get("EMUEXT", "").replace('"', '').split(","),
                            category=info.get("category", "emulator"),
                            pre_cfg=info.get("RJPRECFG", ""),
                            post_cfg=info.get("RJPOSTCFG", "")
                        )
            except (json.JSONDecodeError, OSError):
                pass

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get(self, name: str) -> EmuEntry | None:
        return self._entries.get(name.lower())

    def by_category(self, category: str) -> list[EmuEntry]:
        return [e for e in self._entries.values() if e.category == category]

    def emulators(self) -> list[EmuEntry]:
        return self.by_category("emulator")

    def keymappers(self) -> list[EmuEntry]:
        return self.by_category("keymapper")

    def all_names(self) -> list[str]:
        return sorted(self._entries.keys())

    def get_installed_executables(self, category: str = "emulator") -> list[EmuEntry]:
        """
        Return EmuEntry objects for which an executable is actually installed
        and found on disk, as recorded in apps.ini.
        """
        installed = []
        for entry in self.by_category(category):
            section = "KEYMAPPERS" if category == "keymapper" else category.upper() + "S"
            exe_path_str = self._apps_cfg.get(section, entry.name)
            if exe_path_str and Path(exe_path_str.strip('"')).exists():
                installed.append(entry)
        return installed

    def __len__(self) -> int:
        return len(self._entries)
