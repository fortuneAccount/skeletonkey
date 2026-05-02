import json
from pathlib import Path
from utils.paths import assets_dir

class CoreRegistry:
    def __init__(self):
        self._path = assets_dir() / "libretro_cores.json"
        self._data = {}
        self._load()

    def _load(self):
        if not self._path.exists():
            return
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                self._data = json.load(f)
        except (json.JSONDecodeError, IOError):
            pass

    def get(self, name: str) -> dict | None:
        """Find core data by name (case-insensitive)."""
        if not name:
            return None
        n = name.lower()
        # Direct match or fuzzy check
        if n in self._data:
            return self._data[n]
        # Check with _libretro suffix
        if f"{n}_libretro" in self._data:
            return self._data[f"{n}_libretro"]
        
        for k, v in self._data.items():
            if k.lower() == n or k.lower().replace("_libretro", "") == n:
                return v
        return None

    def update_from_info(self, info_dir: Path):
        """Update extensions in libretro_cores.json from .info files."""
        if not info_dir.exists():
            return
        
        changed = False
        for core_id, info in self._data.items():
            info_file = info_dir / f"{core_id}.info"
            if info_file.exists():
                text = info_file.read_text(encoding="utf-8", errors="replace")
                for line in text.splitlines():
                    if line.startswith("supported_extensions"):
                        exts = line.partition("=")[2].strip().strip('"').replace(" ", "")
                        if info.get("EMUEXT") != exts:
                            info["EMUEXT"] = exts
                            changed = True
                        break
        if changed:
            with open(self._path, "w", encoding="utf-8") as f:
                json.dump(self._data, f, indent=4)