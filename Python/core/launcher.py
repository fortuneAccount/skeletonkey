"""
core/launcher.py

ROM / emulator launch logic.
Replaces emuexe.ahk and BSL.ahk.

Cross-platform note: subprocess.Popen is used instead of AHK's Run/RunWait
so the same code works on Windows, Linux and macOS.  Platform-specific
behaviour (e.g. process suspend/resume) is gated behind sys.platform checks.
"""
import hashlib
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import NamedTuple


class BiosRequirement(NamedTuple):
    """Single BIOS file requirement."""
    path: str
    name: str
    hash: str


class BiosStatus(NamedTuple):
    """BIOS verification result."""
    missing: list[BiosRequirement]
    present: list[BiosRequirement]
    errors: list[str]


def _home() -> Path:
    return Path(__file__).resolve().parent.parent.parent


@dataclass
class LaunchConfig:
    """All parameters needed to launch a single ROM."""
    emulator_path: str          # full path to emulator executable
    rom_path: str               # full path to ROM file
    options: str = ""           # emulator option flags
    arguments: str = ""         # extra arguments appended after ROM path
    use_quotes: bool = True     # wrap ROM path in quotes
    include_extension: bool = True
    include_path: bool = True
    working_dir: str = ""       # cwd for the emulator process
    pre_configs: list[str] = field(default_factory=list)   # files to stage before launch
    post_configs: list[str] = field(default_factory=list)  # files to restore after launch
    keymapper_path: str = ""    # optional antimicro / xpadder path
    keymapper_profile: str = "" # controller profile file


class Launcher:
    """
    Builds and executes the command line for a ROM launch.

    Mirrors the logic in emuexe.ahk:
      - optional keymapper start
      - pre-launch config staging
      - RunWait emulator
      - post-launch config restore
    """

    def __init__(self, cfg: LaunchConfig):
        self.cfg = cfg
        self._proc: subprocess.Popen | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def _resolve_rom_path(self, path_str: str) -> str:
        """
        Resolves a multi-path constructed string into a single existing file.
        Handles strings like 'C:/Roms|D:/Roms/game.zip' by checking each base dir.
        """
        if "|" not in path_str:
            return path_str

        full_p = Path(path_str)
        filename = full_p.name

        # Extract the delimited directories. 
        # Path("A|B") / "file" results in "A|B\file"
        dirs_raw = str(full_p.parent).split("|")
        
        for d in dirs_raw:
            candidate = Path(d.strip()) / filename
            if candidate.exists():
                return str(candidate)

        return path_str

    def launch(self) -> int:
        """
        Execute the full launch sequence.
        Returns the emulator process exit code.
        """
        self.cfg.rom_path = self._resolve_rom_path(self.cfg.rom_path)

        self._start_keymapper()
        self._stage_pre_configs()

        cmd = self._build_command()
        cwd = self.cfg.working_dir or str(Path(self.cfg.emulator_path).parent)

        self._proc = subprocess.Popen(cmd, cwd=cwd, shell=False)
        exit_code = self._proc.wait()

        self._restore_post_configs()
        self._stop_keymapper()
        return exit_code

    def terminate(self):
        """Kill the running emulator process if still alive."""
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_command(self) -> list[str]:
        rom = self.cfg.rom_path

        if not self.cfg.include_path:
            rom = Path(rom).name
        if not self.cfg.include_extension:
            rom = Path(rom).stem

        parts = [self.cfg.emulator_path]
        if self.cfg.options:
            parts += self.cfg.options.split()
        if self.cfg.use_quotes:
            parts.append(rom)
        else:
            parts.append(rom)
        if self.cfg.arguments:
            parts += self.cfg.arguments.split()
        return parts

    def _stage_pre_configs(self):
        """Copy per-game config files into the emulator directory before launch."""
        rom_stem = Path(self.cfg.rom_path).stem
        emu_dir = Path(self.cfg.emulator_path).parent
        for pattern in self.cfg.pre_configs:
            if not pattern:
                continue
            src = emu_dir / pattern.replace("[ROMNAME]", rom_stem)
            if src.exists():
                import shutil
                shutil.copy2(src, emu_dir / src.name.replace(f"{rom_stem}_", ""))

    def _restore_post_configs(self):
        """Copy emulator config files back to per-game slots after launch."""
        rom_stem = Path(self.cfg.rom_path).stem
        emu_dir = Path(self.cfg.emulator_path).parent
        for pattern in self.cfg.post_configs:
            if not pattern:
                continue
            src = emu_dir / pattern
            if src.exists():
                import shutil
                dest = emu_dir / f"{rom_stem}_{src.name}"
                shutil.copy2(src, dest)

    def _start_keymapper(self):
        if not self.cfg.keymapper_path:
            return
        args = [self.cfg.keymapper_path, "--hidden"]
        if self.cfg.keymapper_profile:
            args += ["--profile", self.cfg.keymapper_profile]
        subprocess.Popen(args)

    def _stop_keymapper(self):
        if not self.cfg.keymapper_path:
            return
        km_name = Path(self.cfg.keymapper_path).name
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/f", "/im", km_name],
                           capture_output=True)
        else:
            subprocess.run(["pkill", "-f", km_name], capture_output=True)


# ---------------------------------------------------------------------------
# Frontend suspend / resume helpers  (replaces BSL.ahk Process_Suspend logic)
# ---------------------------------------------------------------------------

KNOWN_FRONTENDS = [
    "emulationstation", "retrofe", "hyperspin", "launchbox",
    "playniteui", "kodi", "xbmc", "steam", "cabrio",
]


def suspend_frontends():
    """Suspend known frontend processes before launching a game."""
    if sys.platform != "win32":
        return  # SIGSTOP equivalent not implemented for non-Windows yet
    import ctypes
    for name in KNOWN_FRONTENDS:
        _win32_suspend_process_by_name(name + ".exe")


def resume_frontends():
    """Resume previously suspended frontend processes."""
    if sys.platform != "win32":
        return
    for name in KNOWN_FRONTENDS:
        _win32_resume_process_by_name(name + ".exe")


def _win32_suspend_process_by_name(exe_name: str):
    try:
        import ctypes
        import ctypes.wintypes
        TH32CS_SNAPPROCESS = 0x00000002
        PROCESS_SUSPEND_RESUME = 0x0800

        class PROCESSENTRY32(ctypes.Structure):
            _fields_ = [
                ("dwSize", ctypes.wintypes.DWORD),
                ("cntUsage", ctypes.wintypes.DWORD),
                ("th32ProcessID", ctypes.wintypes.DWORD),
                ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
                ("th32ModuleID", ctypes.wintypes.DWORD),
                ("cntThreads", ctypes.wintypes.DWORD),
                ("th32ParentProcessID", ctypes.wintypes.DWORD),
                ("pcPriClassBase", ctypes.c_long),
                ("dwFlags", ctypes.wintypes.DWORD),
                ("szExeFile", ctypes.c_char * 260),
            ]

        snap = ctypes.windll.kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        entry = PROCESSENTRY32()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32)
        if ctypes.windll.kernel32.Process32First(snap, ctypes.byref(entry)):
            while True:
                if entry.szExeFile.decode().lower() == exe_name.lower():
                    h = ctypes.windll.kernel32.OpenProcess(
                        PROCESS_SUSPEND_RESUME, False, entry.th32ProcessID)
                    if h:
                        ctypes.windll.ntdll.NtSuspendProcess(h)
                        ctypes.windll.kernel32.CloseHandle(h)
                if not ctypes.windll.kernel32.Process32Next(snap, ctypes.byref(entry)):
                    break
        ctypes.windll.kernel32.CloseHandle(snap)
    except Exception:
        pass


def _win32_resume_process_by_name(exe_name: str):
    try:
        import ctypes
        import ctypes.wintypes
        TH32CS_SNAPPROCESS = 0x00000002
        PROCESS_SUSPEND_RESUME = 0x0800

        class PROCESSENTRY32(ctypes.Structure):
            _fields_ = [
                ("dwSize", ctypes.wintypes.DWORD),
                ("cntUsage", ctypes.wintypes.DWORD),
                ("th32ProcessID", ctypes.wintypes.DWORD),
                ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
                ("th32ModuleID", ctypes.wintypes.DWORD),
                ("cntThreads", ctypes.wintypes.DWORD),
                ("th32ParentProcessID", ctypes.wintypes.DWORD),
                ("pcPriClassBase", ctypes.c_long),
                ("dwFlags", ctypes.wintypes.DWORD),
                ("szExeFile", ctypes.c_char * 260),
            ]

        snap = ctypes.windll.kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        entry = PROCESSENTRY32()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32)
        if ctypes.windll.kernel32.Process32First(snap, ctypes.byref(entry)):
            while True:
                if entry.szExeFile.decode().lower() == exe_name.lower():
                    h = ctypes.windll.kernel32.OpenProcess(
                        PROCESS_SUSPEND_RESUME, False, entry.th32ProcessID)
                    if h:
                        ctypes.windll.ntdll.NtResumeProcess(h)
                        ctypes.windll.kernel32.CloseHandle(h)
                if not ctypes.windll.kernel32.Process32Next(snap, ctypes.byref(entry)):
                    break
        ctypes.windll.kernel32.CloseHandle(snap)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# BIOS/firmware verification
# ---------------------------------------------------------------------------

def _get_file_hash(path: Path, algorithm: str = "md5") -> str:
    """Compute hash of a file for verification."""
    if not path.exists():
        return ""
    try:
        h = hashlib.new(algorithm)
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
        return h.hexdigest().upper()
    except Exception:
        return ""


def _search_bios_paths(base: Path, sub_path: str, filename: str) -> list[Path]:
    """Search common BIOS locations for a file."""
    candidates = []

    search_roots = [
        base,
        base.parent,
        _home() / "Emulators",
        Path("C:/Users/Public/RetroArch") / "system",
        Path("C:/Program Files/RetroArch") / "system",
        Path("C:/Program Files (x86)/RetroArch") / "system",
    ]

    for root in search_roots:
        if not root.exists():
            continue
        candidate = root / sub_path / filename
        if candidate.exists():
            candidates.append(candidate)

    return candidates


def verify_bios(emu_name: str, system: str, app_home: Path) -> BiosStatus:
    """
    Verify BIOS/firmware requirements for an emulator/system combination.
    Returns status with missing/present files and any errors.
    """
    import json

    bios_json = _home() / "data" / "bios.json"
    if not bios_json.exists():
        return BiosStatus(missing=[], present=[], errors=["BIOS database not found"])

    try:
        with open(bios_json, "r", encoding="utf-8") as f:
            bios_data = json.load(f)
    except Exception as e:
        return BiosStatus(missing=[], present=[], errors=[f"Failed to load BIOS data: {e}"])

    entries = bios_data.get("entries", {})
    system_entry = entries.get(system, {})
    emu_entry = system_entry.get(emu_name.lower(), {})
    required = emu_entry.get("required_files", [])

    if not required:
        return BiosStatus(missing=[], present=[], errors=[])

    missing = []
    present = []
    errors = []

    for req in required:
        req_obj = BiosRequirement(
            path=req.get("path", ""),
            name=req.get("name", ""),
            hash=req.get("hash", "").upper()
        )

        if not req_obj.name:
            continue

        found = _search_bios_paths(app_home, req_obj.path, req_obj.name)

        if not found:
            missing.append(req_obj)
            continue

        actual = found[0]
        present.append(req_obj)

        if req_obj.hash:
            actual_hash = _get_file_hash(actual)
            if actual_hash and actual_hash != req_obj.hash:
                errors.append(f"BIOS hash mismatch for {req_obj.name}: expected {req_obj.hash}, got {actual_hash}")

    return BiosStatus(missing=missing, present=present, errors=errors)


def check_launch_prerequisites(emu_name: str, system: str) -> tuple[bool, list[str]]:
    """
    Check if a ROM can be launched given current BIOS/firmware status.
    Returns (can_launch: bool, warnings: list[str])
    """
    app_home = _home() / "Emulators"
    status = verify_bios(emu_name, system, app_home)

    warnings = []

    for req in status.missing:
        warnings.append(f"Missing BIOS: {req.name} (expected in {req.path})")

    for err in status.errors:
        warnings.append(f"BIOS error: {err}")

    can_launch = len(status.missing) == 0 and len(status.errors) == 0

    return can_launch, warnings
