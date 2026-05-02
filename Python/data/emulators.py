
import json
import logging
from dataclasses import dataclass, field, asdict
from pathlib import Path
from core.config import Config, global_config
from utils.paths import app_root, assets_dir
from data.cores import CoreRegistry

logger = logging.getLogger(__name__)

@dataclass
class EmuEntry:
    name: str
    archive: str = ""       # relative download path  e.g. "mame/mame-[ARCH].7z"
    exe: str = ""           # executable name          e.g. "mame.exe"
    configs: list[str] = field(default_factory=list)   # config file patterns
    save_states: list[str] = field(default_factory=list)
    save_data: list[str] = field(default_factory=list)
    bios_path: str = ""     # BIOSPTH from emulators.json
    firmware: str = ""      # FIRMWARE string (name:hash|...)
    extensions: list[str] = field(default_factory=list) # Supported ROM extensions
    required_files: list[str] = field(default_factory=list) # Bios/Firmware requirements
    category: str = "emulator"  # emulator | frontend | utility | keymapper
    is_custom: bool = False
    pre_cfg: str = ""
    post_cfg: str = ""
    options: str = ""
    arguments: str = ""


class EmuRegistry:
    def __init__(self, home: Path | None = None):
        self._app_root = app_root()
        self._home = home or global_config().home
        self._apps_cfg = Config(Config.APPS_FILE)
        self._entries: dict[str, EmuEntry] = {}
        self._cores = CoreRegistry()
        self._custom_path = self._home / "custom_emulators.json"
        self._load()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _load(self):
        src_json = assets_dir() / "emulators.json"

        if src_json.exists():
            try:
                with open(src_json, "r", encoding="utf-8") as f:
                    emu_data = json.load(f)
                    for name, info in emu_data.items():
                        def _parse_list(key):
                            val = info.get(key)
                            if isinstance(val, list):
                                return [str(v).strip() for v in val]
                            if isinstance(val, str) and val:
                                return [p.strip() for p in val.split("|") if p.strip()]
                            return []

                        def _parse_exts():
                            val = info.get("EMUEXT")
                            if isinstance(val, list):
                                return [str(v).strip().lower() for v in val]
                            if isinstance(val, str) and val:
                                # Handle legacy string formatting with mixed delimiters
                                cleaned = val.replace('"', '').replace("|", ",")
                                return [p.strip().lower() for p in cleaned.split(",") if p.strip()]
                            return []

                        self._entries[name.lower()] = EmuEntry(
                            name=name,
                            archive=info.get("URLPTH", ""),
                            exe=info.get("EXENAM", ""),
                            configs=_parse_list("CFGPTH"),
                            save_states=_parse_list("STATEPTH"),
                            save_data=_parse_list("MEMPTH"),
                            bios_path=info.get("BIOSPTH", ""),
                            firmware=info.get("FIRMWARE", ""),
                            extensions=_parse_exts(),
                            category=info.get("category", "emulator"),
                            pre_cfg=info.get("RJPRECFG", ""),
                            post_cfg=info.get("RJPOSTCFG", ""),
                            options=info.get("options", ""),
                            arguments=info.get("arguments", "")
                        )
            except (json.JSONDecodeError, OSError):
                pass

        self._load_custom()

    def _load_custom(self):
        """Load user-defined emulators from the separate custom JSON."""
        if not self._custom_path.exists():
            return
        try:
            with open(self._custom_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                for name, info in data.items():
                    entry = EmuEntry(
                        name=name,
                        archive=info.get("archive", ""),
                        exe=info.get("exe", ""),
                        bios_path=info.get("bios_path", ""),
                        firmware=info.get("firmware", ""),
                        extensions=info.get("extensions", []),
                        required_files=info.get("required_files", []),
                        category=info.get("category", "emulator"),
                        is_custom=True,
                        options=info.get("options", ""),
                        arguments=info.get("arguments", "")
                    )
                    self._entries[name.lower()] = entry
        except Exception as e:
            logger.error(f"Error loading emulator entries: {e}")

    def reload(self):
        self._apps_cfg.reload()
        self._entries.clear()
        self._load()

    def add_custom(self, entry: EmuEntry):
        """Add or update a custom emulator entry and persist it."""
        entry.is_custom = True
        self._entries[entry.name.lower()] = entry
        self.save_custom()

    def delete_custom(self, name: str):
        """Remove a custom emulator from the registry and disk."""
        if name.lower() in self._entries:
            del self._entries[name.lower()]
            self.save_custom()

    def save_custom(self):
        """Persist all entries flagged as custom to the user directory."""
        custom_data = {e.name: asdict(e) for e in self._entries.values() if e.is_custom}
        self._custom_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._custom_path, "w", encoding="utf-8") as f:
            json.dump(custom_data, f, indent=4)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get(self, name: str) -> EmuEntry | None:
        return self._entries.get(name.lower())

    def by_category(self, category: str) -> list[EmuEntry]:
        cat_low = category.lower()
        return [e for e in self._entries.values() if e.category.lower() == cat_low]

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
            # Case-insensitive lookup in apps configuration
            all_apps = self._apps_cfg.items(section)
            exe_path_str = next((v for k, v in all_apps if k.lower() == entry.name.lower()), None)
            
            if exe_path_str and Path(exe_path_str.strip('"')).exists():
                installed.append(entry)
        return installed

    def __len__(self) -> int:
        return len(self._entries)
