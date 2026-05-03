"""
utils/paths.py

Path resolution helpers used across the application.
Mirrors the home / source / binhome pattern from working.ahk.

Directory structure:
  skeletonkey/          - Root (portable mode)
    assets/             - master JSON data and templates
    generated/         - User-generated configs, overrides, INI files
    Python/             - Python source code
    img/                - Icons and images
    bin/                - Executables and tools
    rj/                 - RetroLauncher configs (emulators, joysticks)
"""

import os
import sys
from pathlib import Path


def app_root() -> Path:
    """
    Return the application root directory.

    When running from source the root is two levels above this file.
    When running as a frozen executable (PyInstaller etc.) it is the
    directory containing the executable.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    
    base = Path(__file__).resolve().parent.parent

    # Mirrors AHK logic: if we are inside src/bin/binaries, the home is the parent
    if base.name in ("Python", "bin", "binaries"):
        return base.parent
    return base


def app_home() -> Path:
    """Alias for app_root() to maintain backward compatibility."""
    return app_root()


def config_home() -> Path:
    """Determine writable config location based on portable status."""
    # Establishing 'configs' folder in the root for all user configuration data.
    path = app_root() / "configs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def assets_dir() -> Path:
    """Return the assets directory containing master JSON templates."""
    return app_root() / "assets"


def generated_dir() -> Path:
    """Return the generated directory for user configs and overrides."""
    return app_home() / "generated"


def src_dir() -> Path:
    """Legacy alias - returns assets directory."""
    return assets_dir()


def bin_dir() -> Path:
    return app_home() / "bin"


def img_dir() -> Path:
    return app_home() / "img"


def rj_dir() -> Path:
    return app_home() / "rj"


def emu_cfgs_dir() -> Path:
    return rj_dir() / "emuCfgs"


def joy_cfgs_dir() -> Path:
    return rj_dir() / "joyCfgs"


def resolve_arch(path: str, bits: int = 64) -> str:
    """
    Replace the [ARCH] placeholder used in emulator archive strings.

    e.g. "mame-[ARCH].7z" → "mame-x64.7z"  (bits=64)
         "mame-[ARCH].7z" → "mame-x86.7z"  (bits=32)
    """
    arch = "x64" if bits == 64 else "x86"
    return path.replace("[ARCH]", arch)


def system_drive() -> str:
    """Return the root drive letter on Windows, or '/' on POSIX."""
    if sys.platform == "win32":
        return os.path.splitdrive(app_home())[0] + "\\"
    return "/"


def find_binary(name: str) -> Path | None:
    """
    Search bin_dir() for *name* (case-insensitive on Windows).
    Returns the full path or None.
    """
    bd = bin_dir()
    if not bd.exists():
        return None
    for f in bd.iterdir():
        if f.name.lower() == name.lower():
            return f
    return None


def check_paths_exist(path_str: str, separator: str = "|") -> bool:
    """Return True if at least one path in the delimited string exists on disk."""
    if not path_str:
        return False
    return any(Path(p.strip()).exists() for p in path_str.split(separator) if p.strip())


def parse_delimited_list(value: str | list, separator: str = "|") -> list[str]:
    """
    Parse a delimited string or return list as-is.
    Efficiently handles pipe-delimited strings throughout the app.
    
    Args:
        value: String like "a|b|c" or already a list
        separator: Delimiter character (default: pipe)
    
    Returns:
        List of stripped, non-empty strings
    """
    if isinstance(value, list):
        return value
    if not isinstance(value, str):
        return []
    return [item.strip() for item in value.split(separator) if item.strip()]


def parse_delimited_paths(value: str | list, separator: str = "|", check_exists: bool = False) -> list[Path]:
    """
    Parse a delimited string of paths.
    
    Args:
        value: String like "/path/a|/path/b" or list of paths
        separator: Delimiter character (default: pipe)
        check_exists: Only return paths that exist
    
    Returns:
        List of Path objects
    """
    items = parse_delimited_list(value, separator)
    paths = [Path(p) for p in items]
    if check_exists:
        paths = [p for p in paths if p.exists()]
    return paths
