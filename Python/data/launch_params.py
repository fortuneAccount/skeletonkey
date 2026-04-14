"""
data/launch_params.py

Parses launchparams.set / launchparams.ini.

Each line in [LAUNCHPARAMS] has the format:
    System Name=$|jacketize|extract|explode|runrom|clean

where each field after $ is 0 or 1.
"""
import configparser
from dataclasses import dataclass
from pathlib import Path
from core.config import global_config


def _home() -> Path:
    return Path(__file__).resolve().parent.parent.parent


@dataclass
class LaunchParams:
    system: str
    override: str = "$"     # custom override string (rarely used)
    jacketize: bool = True  # inject per-game emulator config
    extract: bool = True    # extract archive before launch
    explode: bool = False   # extract all files (not just the ROM)
    runrom: bool = True     # pass ROM path to emulator
    clean: bool = False     # delete extracted files after launch


class LaunchParamsRegistry:
    """
    Provides per-system launch parameter lookup.

    Priority: generated/launchparams.ini (user) > assets/launchparams.set (defaults)
    """

    SECTION = "LAUNCHPARAMS"

    def __init__(self, home: Path | None = None):
        self._home = home or global_config().home
        self._data: dict[str, LaunchParams] = {}
        self._load()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _load(self):
        parser = configparser.RawConfigParser(strict=False)
        parser.optionxform = str  # type: ignore[assignment]

        default_set = self._home / "assets" / "launchparams.set"
        if default_set.exists():
            try:
                parser.read(default_set, encoding="utf-8-sig")
            except UnicodeDecodeError:
                parser.read(default_set, encoding="utf-16")

        user_ini = self._home / "launchparams.ini"
        if user_ini.exists():
            try:
                parser.read(user_ini, encoding="utf-8-sig")
            except UnicodeDecodeError:
                parser.read(user_ini, encoding="utf-16")

        if not parser.has_section(self.SECTION):
            return

        for system, raw in parser.items(self.SECTION):
            lp = self._parse(system, raw.strip('"'))
            self._data[system] = lp

    @staticmethod
    def _parse(system: str, raw: str) -> LaunchParams:
        parts = raw.split("|")

        def _bool(idx: int) -> bool:
            return parts[idx].strip() == "1" if idx < len(parts) else False

        return LaunchParams(
            system=system,
            override=parts[0] if parts else "$",
            jacketize=_bool(1),
            extract=_bool(2),
            explode=_bool(3),
            runrom=_bool(4),
            clean=_bool(5),
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get(self, system: str) -> LaunchParams:
        """Return params for *system*, falling back to safe defaults."""
        return self._data.get(system, LaunchParams(system=system))

    def set(self, params: LaunchParams):
        self._data[params.system] = params

    def save(self):
        parser = configparser.RawConfigParser()
        parser.optionxform = str  # type: ignore[assignment]
        parser.add_section(self.SECTION)
        for system, lp in self._data.items():
            val = "|".join([
                lp.override,
                "1" if lp.jacketize else "0",
                "1" if lp.extract else "0",
                "1" if lp.explode else "0",
                "1" if lp.runrom else "0",
                "1" if lp.clean else "0",
            ])
            parser.set(self.SECTION, system, f'"{val}"')
        dest = self._home / "launchparams.ini"
        dest.parent.mkdir(parents=True, exist_ok=True)
        with open(dest, "w", encoding="utf-8") as fh:
            parser.write(fh)

    def all_systems(self) -> list[str]:
        return sorted(self._data.keys())
