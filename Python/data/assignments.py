"""
data/assignments.py

Consolidated JSON database for system-emulator associations.
Replaces the legacy Assignments.ini and Assignments.set files.

Each system entry stores:
  - assigned_emulator: The chosen executable or core
  - extensions: List of supported ROM extensions for this system
  - required_files: List of BIOS or firmware files needed
"""
import configparser
import json
from pathlib import Path


def _home() -> Path:
    return Path(__file__).resolve().parent.parent.parent


class AssignmentRegistry:
    """
    Maps system names to their assigned emulator or RetroArch core.

    get_assignment(system) returns the user override if set, otherwise
    the default core from [ASSIGNMENTS].
    """

    SEC_OVERRIDES = "OVERRIDES"
    SEC_ASSIGNMENTS = "ASSIGNMENTS"

    def __init__(self, home: Path | None = None):
        self._home = home or _home()
        self._overrides: dict[str, str] = {}
        self._defaults: dict[str, str] = {}
        self._load()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _load(self):
        parser = configparser.RawConfigParser(strict=False)
        parser.optionxform = str

        default_set = self._home / "assets" / "Assignments.set"
        if default_set.exists():
            try:
                parser.read(default_set, encoding="utf-8-sig")
            except UnicodeDecodeError:
                parser.read(default_set, encoding="utf-16")

        user_ini = self._home / "generated" / "Assignments.ini"
        if user_ini.exists():
            try:
                parser.read(user_ini, encoding="utf-8-sig")
            except UnicodeDecodeError:
                parser.read(user_ini, encoding="utf-16")

        if parser.has_section(self.SEC_OVERRIDES):
            for k, v in parser.items(self.SEC_OVERRIDES):
                self._overrides[k] = v.strip('"')

        if parser.has_section(self.SEC_ASSIGNMENTS):
            for k, v in parser.items(self.SEC_ASSIGNMENTS):
                self._defaults[k] = v.strip('"')

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_assignment(self, system: str) -> str:
        """
        Return the emulator/core assigned to *system*.
        User override takes priority over the default core.
        """
        override = self._overrides.get(system, "")
        if override:
            return override
        return self._defaults.get(system, "")

    def set_override(self, system: str, emulator: str):
        """Set a user override for *system*."""
        self._overrides[system] = emulator

    def clear_override(self, system: str):
        self._overrides.pop(system, None)

    def save(self):
        """Persist user overrides to Assignments.ini."""
        parser = configparser.RawConfigParser()
        parser.optionxform = str
        parser.add_section(self.SEC_OVERRIDES)
        for k, v in self._overrides.items():
            parser.set(self.SEC_OVERRIDES, k, f'"{v}"')
        dest = self._home / "generated" / "Assignments.ini"
        dest.parent.mkdir(parents=True, exist_ok=True)
        with open(dest, "w", encoding="utf-8") as fh:
            parser.write(fh)

    def all_systems(self) -> list[str]:
        keys = set(self._overrides) | set(self._defaults)
        return sorted(keys)
