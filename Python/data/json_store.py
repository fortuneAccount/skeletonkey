"""
data/json_store.py

Unified JSON-based configuration store.
Handles reading/writing JSON config files with fallback to .set files.
"""
import json
import configparser
from pathlib import Path
from typing import Any


def _home() -> Path:
    return Path(__file__).resolve().parent.parent.parent


def _python_data_dir() -> Path:
    return Path(__file__).resolve().parent


class JSONStore:
    """Read/write JSON config with automatic .set file fallback for migration."""

    def __init__(self, json_name: str, set_name: str | None = None):
        self._json_path = _python_data_dir() / f"{json_name}.json"
        self._set_path = _home() / "assets" / set_name if set_name else None

    def load(self, convert_cb=None) -> dict:
        """Load data from JSON, or convert from .set if JSON doesn't exist."""
        if self._json_path.exists():
            with open(self._json_path, "r", encoding="utf-8") as f:
                return json.load(f)

        if self._set_path and self._set_path.exists() and convert_cb:
            data = convert_cb(self._set_path)
            self.save(data)
            return data

        return {}

    def save(self, data: dict):
        """Save data to JSON file."""
        self._json_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._json_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)


def parse_set_file(path: Path, section: str) -> dict:
    """Parse a standard .set INI file into a dict."""
    parser = configparser.RawConfigParser(strict=False)
    parser.optionxform = str
    try:
        parser.read(path, encoding="utf-8-sig")
    except UnicodeDecodeError:
        parser.read(path, encoding="utf-16")

    if parser.has_section(section):
        return dict(parser.items(section))
    return {}


def parse_pipe_line_set(path: Path, section: str) -> list[dict]:
    """Parse a pipe-delimited .set file (like EmuParts.set) into list of dicts."""
    parser = configparser.RawConfigParser(strict=False)
    parser.optionxform = str
    try:
        parser.read(path, encoding="utf-8-sig")
    except UnicodeDecodeError:
        parser.read(path, encoding="utf-16")

    results = []
    if parser.has_section(section):
        for key, val in parser.items(section):
            parts = val.split("<")
            results.append({
                "name": key,
                "archive": parts[1].strip() if len(parts) > 1 else "",
                "exe": parts[2].strip() if len(parts) > 2 else "",
                "configs": [x for x in parts[3].split("|") if x] if len(parts) > 3 else [],
                "save_states": [x for x in parts[4].split("|") if x] if len(parts) > 4 else [],
                "save_data": [x for x in parts[5].split("|") if x] if len(parts) > 5 else [],
                "extensions": [x for x in parts[6].split("|") if x] if len(parts) > 6 else [],
                "required_files": [x for x in parts[7].split("|") if x] if len(parts) > 7 else [],
            })
    return results