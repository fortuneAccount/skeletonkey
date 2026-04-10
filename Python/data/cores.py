"""
data/cores.py

Loads RetroArch core information from .info files.
Format: display_name|core_name|system_name|supported_extensions|authors|license|permissions
"""
import configparser
import json
from dataclasses import dataclass, field
from pathlib import Path


def _home() -> Path:
    return Path(__file__).resolve().parent.parent


@dataclass
class CoreInfo:
    """Parsed core .info file data."""
    display_name: str = ""
    core_name: str = ""
    systemname: str = ""
    supported_extensions: str = ""
    authors: str = ""
    license: str = ""
    permissions: list[str] = field(default_factory=list)
    required_files: list[str] = field(default_factory=list)
    database: str = ""


class CoreRegistry:
    """Loads core info from .info files found in RetroArch."""

    def __init__(self, retroarch_path: Path | None = None):
        self._home = _home()
        self._ra_path = retroarch_path
        self._cores: dict[str, CoreInfo] = {}
        self._load()

    def _load(self):
        if not self._ra_path or not self._ra_path.exists():
            return

        info_dir = self._ra_path / "info"
        if not info_dir.exists():
            return

        for info_file in info_dir.glob("*.info"):
            core = self._parse_info_file(info_file)
            if core:
                key = core.core_name.lower().replace("_libretro", "")
                self._cores[key] = core

    def _parse_info_file(self, path: Path) -> CoreInfo | None:
        try:
            content = path.read_text(encoding="utf-8")
        except Exception:
            return None

        core = CoreInfo(core_name=path.stem)

        for line in content.splitlines():
            line = line.strip()
            if "=" in line:
                key, val = line.split("=", 1)
                val = val.strip().strip('"')

                if key == "display_name":
                    core.display_name = val
                elif key == "systemname":
                    core.systemname = val
                elif key == "supported_extensions":
                    core.supported_extensions = val
                elif key == "authors":
                    core.authors = val
                elif key == "license":
                    core.license = val
                elif key == "permissions":
                    core.permissions = [p.strip() for p in val.split("|") if p]
                elif key == "database":
                    core.database = val
                elif key == "required_files":
                    core.required_files = [f.strip() for f in val.split("|") if f]

        return core

    def get(self, name: str) -> CoreInfo | None:
        name_lower = name.lower().replace("_libretro", "")
        return self._cores.get(name_lower)

    def all_cores(self) -> list[CoreInfo]:
        return list(self._cores.values())

    def find_for_system(self, system: str) -> list[CoreInfo]:
        system_lower = system.lower()
        return [
            c for c in self._cores.values()
            if system_lower in c.systemname.lower() or system_lower in c.database.lower()
        ]


def find_retroarch() -> Path | None:
    """Attempt to locate RetroArch installation."""
    app_home = _home() / "Emulators"

    common_names = ["retroarch", "RetroArch", "retroarch.exe"]
    common_paths = [
        Path("C:/Program Files/RetroArch"),
        Path("C:/Program Files (x86)/RetroArch"),
        app_home / "RetroArch",
        app_home / "retroarch",
    ]

    for base in common_paths:
        for name in common_names:
            exe = base / name
            if exe.exists():
                return exe.parent

    for base in app_home.iterdir():
        if base.is_dir():
            for name in common_names:
                exe = base / name
                if exe.exists():
                    return exe.parent

    return None