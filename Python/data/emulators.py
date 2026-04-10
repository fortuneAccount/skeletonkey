"""
data/emulators.py

Parses EmuParts.set which defines emulators, frontends, utilities and
keymappers in the format:

    (This is NOT a standard INI format; each line is parsed directly.)
    name<archive_path<exe<config_files<save_states<save_data

Each section ([EMULATORS], [FRONTENDS], [UTILITIES], [KEYMAPPERS]) is
parsed into typed dataclasses.
"""
import configparser
from dataclasses import dataclass, field
from pathlib import Path
from core.config import Config


def _home() -> Path:
    return Path(__file__).resolve().parent.parent.parent


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


class EmuRegistry:
    """
    Loads EmuParts.set and provides lookup by name or category.

    The file is NOT a standard INI – each line under a section header is
    a pipe-delimited record, not a key=value pair.  We parse it manually.
    """

    _SECTION_MAP = {
        "[EMULATORS]": "emulator",
        "[FRONTENDS]": "frontend",
        "[UTILITIES]": "utility",
        "[KEYMAPPERS]": "keymapper",
    }

    def __init__(self, home: Path | None = None):
        self._home = home or _home()
        self._apps_cfg = Config(Config.APPS_FILE, home=self._home)
        self._entries: dict[str, EmuEntry] = {}
        self._load()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _load(self):
        src = self._home / "assets" / "EmuParts.set"
        if not src.exists():
            return

        current_category = "emulator"
         # Resilient loading for AHK-generated files
        try:
            content = src.read_text(encoding="utf-8-sig")
        except UnicodeDecodeError:
            content = src.read_text(encoding="utf-16")

        for raw_line in content.splitlines():
            line = raw_line.strip()
            if not line or line.startswith(";"):
                continue
            if line in self._SECTION_MAP:
                current_category = self._SECTION_MAP[line]
                continue
            entry = self._parse_line(line, current_category)
            if entry:
                self._entries[entry.name.lower()] = entry

    def reload(self):
        self._entries.clear()
        self._apps_cfg.reload()
        self._load()

    @staticmethod
    def _parse_line(line: str, category: str) -> EmuEntry | None:
        parts = line.split("<")
        if not parts:
            return None
        name = parts[0].strip()
        if not name:
            return None

        def _get(idx: int) -> str:
            return parts[idx].strip() if idx < len(parts) else ""

        def _split_pipe(s: str) -> list[str]:
            return [x for x in s.split("|") if x]

        return EmuEntry(
            name=name,
            archive=_get(1),
            exe=_get(2),
            configs=_split_pipe(_get(3)),
            save_states=_split_pipe(_get(4)),
            save_data=_split_pipe(_get(5)),
            extensions=_split_pipe(_get(6)),
            required_files=_split_pipe(_get(7)),
            category=category,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get(self, name: str) -> EmuEntry | None:
        return self._entries.get(name.lower())

    def by_category(self, category: str) -> list[EmuEntry]:
        return [e for e in self._entries.values() if e.category == category]

    def emulators(self) -> list[EmuEntry]:
        return self.by_category("emulator")

    def frontends(self) -> list[EmuEntry]:
        return self.by_category("frontend")

    def utilities(self) -> list[EmuEntry]:
        return self.by_category("utility")

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
            if exe_path_str and Path(exe_path_str).exists():
                installed.append(entry)
        return installed

    def __len__(self) -> int:
        return len(self._entries)
