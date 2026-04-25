"""
data/launch_params.py

Provides per-system launch parameter lookup from JSON.
"""
import json
from dataclasses import dataclass, asdict
from pathlib import Path
from core.config import global_config
from utils.paths import app_root


@dataclass
class LaunchParams:
    system: str
    override: str = "$"     # custom override string (rarely used)
    extract: bool = True    # extract archive before launch
    explode: bool = False   # extract all files (not just the ROM)
    runrom: bool = True     # pass ROM path to emulator
    clean: bool = False     # delete extracted files after launch


class LaunchParamsRegistry:
    """
    Provides per-system launch parameter lookup.
    """

    def __init__(self, home: Path | None = None):
        self._home = home or global_config().home
        self._data: dict[str, LaunchParams] = {}
        self._load()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _load(self):
        """Load user launch parameters from JSON."""
        path = self._home / "launchparams.json"
        if path.exists():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    raw_data = json.load(f)
                    for name, fields in raw_data.items():
                        self._data[name] = LaunchParams(**fields)
                return
            except Exception: pass

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get(self, system: str) -> LaunchParams:
        """Return params for *system*, falling back to safe defaults."""
        return self._data.get(system, LaunchParams(system=system))

    def set(self, params: LaunchParams):
        self._data[params.system] = params

    def save(self):
        """Save user launch parameters to JSON."""
        dest = self._home / "launchparams.json"
        serializable = {name: asdict(lp) for name, lp in self._data.items()}
        with open(dest, "w", encoding="utf-8") as f:
            json.dump(serializable, f, indent=4)

    def all_systems(self) -> list[str]:
        return sorted(self._data.keys())
