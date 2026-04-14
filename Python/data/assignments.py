"""
data/assignments.py

Consolidated JSON database for system-emulator associations.
Replaces the legacy Assignments.ini and Assignments.set files.

Each system entry stores:
  - assigned_emulator: The chosen executable or core
  - extensions: List of supported ROM extensions for this system
  - required_files: List of BIOS or firmware files needed
"""
import json
from pathlib import Path
from core.config import global_config
from data.systems import SystemRegistry


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
        # Now wraps SystemRegistry to access unified data
        self._systems = SystemRegistry(home)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_assignment(self, system: str) -> str:
        """Return the emulator/core assigned to *system* from unified JSON."""
        sys_data = self._systems._data.get(system, {})
        return sys_data.get("assigned_emulator", "")

    def set_override(self, system: str, emulator: str):
        """Set a user override for *system*."""
        if system not in self._systems._data: self._systems._data[system] = {}
        self._systems._data[system]["assigned_emulator"] = emulator

    def clear_override(self, system: str):
        if system in self._systems._data:
            self._systems._data[system]["assigned_emulator"] = ""

    def save(self):
        """Save through the unified system registry."""
        self._systems.save()

    def all_systems(self) -> list[str]:
        return self._systems.all_systems()
