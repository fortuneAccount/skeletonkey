"""
data/json_store.py

Unified JSON-based configuration store.
Handles reading/writing JSON config files.
"""
import json
from pathlib import Path
from utils.paths import app_root


def _python_data_dir() -> Path:
    return Path(__file__).resolve().parent


class JSONStore:
    """Read/write JSON config."""

    def __init__(self, json_name: str):
        self._json_path = _python_data_dir() / f"{json_name}.json"

    def load(self) -> dict:
        """Load data from JSON."""
        if self._json_path.exists():
            with open(self._json_path, "r", encoding="utf-8") as f:
                return json.load(f)
        return {}

    def save(self, data: dict):
        """Save data to JSON file."""
        self._json_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._json_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)