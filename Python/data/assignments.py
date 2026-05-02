import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from core.config import global_config
from utils.paths import app_root

logger = logging.getLogger(__name__)

@dataclass
class AssignmentEntry:
    """Represents emulator assignments for a system."""
    system: str
    emulators: list[str] = field(default_factory=list)

    @property
    def primary(self) -> str:
        """The most recently assigned (priority) emulator."""
        return self.emulators[-1] if self.emulators else ""

    def __str__(self) -> str:
        """Legacy pipe-delimited string representation."""
        return "|".join(self.emulators)

class AssignmentRegistry:
    """Manages mappings between systems and their assigned emulators."""

    def __init__(self, home: Path | None = None):
        self._home = home or global_config().home
        self._data: dict[str, AssignmentEntry] = {}
        self._load()

    def _load(self):
        path = self._home / "Assignments.json"
        if not path.exists():
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                raw = json.load(f)
                for sys_name, emus in raw.items():
                    # Handle both list and legacy pipe-string formats
                    emu_list = emus if isinstance(emus, list) else [e.strip() for e in emus.split("|") if e.strip()]
                    self._data[sys_name] = AssignmentEntry(system=sys_name, emulators=emu_list)
        except Exception as e:
            logger.error(f"Failed to load assignments: {e}")

    def reload(self):
        self._data.clear()
        self._load()

    def get_assignment(self, system: str) -> AssignmentEntry:
        """Return the assignment entry for a system, or an empty one if none exists."""
        if not system or system == ":=:System List:=:":
            return AssignmentEntry(system="")
        return self._data.get(system, AssignmentEntry(system=system))

    def set_override(self, system: str, emulator_pipe_or_list):
        """Set or update the emulator list for a system."""
        if isinstance(emulator_pipe_or_list, str):
            emu_list = [e.strip() for e in emulator_pipe_or_list.split("|") if e.strip()]
        else:
            emu_list = emulator_pipe_or_list
        self._data[system] = AssignmentEntry(system=system, emulators=emu_list)

    def clear_override(self, system: str):
        if system in self._data:
            del self._data[system]

    def save(self):
        path = self._home / "Assignments.json"
        out = {k: v.emulators for k, v in self._data.items()}
        with open(path, "w", encoding="utf-8") as f:
            json.dump(out, f, indent=4)