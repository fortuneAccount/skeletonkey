"""
core/launcher.py

ROM / emulator launch logic.
Replaces emuexe.ahk and BSL.ahk.

Cross-platform note: subprocess.Popen is used instead of AHK's Run/RunWait
so the same code works on Windows, Linux and macOS.  Platform-specific
behaviour (e.g. process suspend/resume) is gated behind sys.platform checks.
"""
import hashlib
import subprocess
import logging
import shlex
import sys
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import NamedTuple
from utils.paths import assets_dir, app_root, check_paths_exist, emu_cfgs_dir
from utils.archive import extract

logger = logging.getLogger(__name__)

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
    extract_rom: bool = False   # Flag to trigger archive extraction
    clean_after: bool = False   # Delete temp files after launch
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
        self._rom_path = Path(cfg.rom_path)
        self._rom_dir = self._rom_path.parent
        self._title = self._rom_path.stem
        self._proc: subprocess.Popen | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def _prepare_sandbox(self, system_name: str, emu_name: str):
        """
        Resolves configuration based on new priority:
        Priority 1: path/to/$system/$Title/ (ROM-local)
        Priority 2: downloaded/$system/$Title/ (App-local cache)
        Priority 3: assets/emuCfgs/ (Template)
        """
        # 1. Determine local sandbox location (Priority 1)
        local_pkg = self._rom_dir / self._title
        download_pkg = app_root() / "downloaded" / system_name / self._title
        
        # Choose primary sandbox (Priority 1 preferred)
        sandbox_dir = local_pkg if local_pkg.exists() else download_pkg
        sandbox_dir.mkdir(parents=True, exist_ok=True)

        # 2. Redirect standard env vars
        os.environ["XDG_CONFIG_HOME"] = str(sandbox_dir)
        os.environ["XDG_DATA_HOME"] = str(sandbox_dir / "saves")

        # 3. Stage template if no config exists in the sandbox yet
        # We check for a generic config extension like .ini or .cfg
        if not any(sandbox_dir.glob("*.ini")) and not any(sandbox_dir.glob("*.cfg")):
            template = emu_cfgs_dir() / f"{emu_name}.cfg"
            if template.exists():
                shutil.copy(template, sandbox_dir / template.name)
                logger.info(f"Staged template {template.name} to {sandbox_dir}")

        logger.info(f"Sandbox prepared at {sandbox_dir}")

    def _resolve_rom_path(self, path_str: str) -> str:
        """
        Resolves a multi-path constructed string into a single existing file.
        """
        if "|" not in path_str:
            return path_str

        # Handle filename resolution from multiple base directories
        filename = Path(path_str).name
        for d in str(Path(path_str).parent).split("|"):
            if (candidate := Path(d.strip()) / filename).exists():
                return str(candidate)

        return path_str

    def launch(self) -> int:
        """
        Execute the full launch sequence.
        Returns the emulator process exit code.
        """
        self.cfg.rom_path = self._resolve_rom_path(self.cfg.rom_path)
        
        # Prepare the isolated environment
        self._prepare_sandbox()

        # Handle Archive Extraction if required
        original_rom = Path(self.cfg.rom_path)
        if self.cfg.extract_rom and original_rom.suffix.lower() in ('.zip', '.7z', '.rar'):
            temp_dir = app_root() / "temp" / original_rom.stem
            if extract(str(original_rom), str(temp_dir)):
                # Find the first file in the extracted folder to use as the target
                files = [f for f in temp_dir.iterdir() if f.is_file()]
                if files:
                    self.cfg.rom_path = str(files[0])

        self._start_keymapper()

        cmd = self._build_command()
        # Log the command itself to the primary log file
        logger.info(f"Executing launch command: {' '.join(cmd)}")

        # Ensure the executable exists and path is clean
        exe_path = Path(self.cfg.emulator_path.strip('"'))
        if not exe_path.exists():
            raise FileNotFoundError(f"Emulator executable not found: {exe_path}")

        cwd = self.cfg.working_dir or str(exe_path.parent)
        if not Path(cwd).exists():
            cwd = None # Fallback to system default if specific CWD is missing

        # Redirect stdout/stderr to capture output in the log
        self._proc = subprocess.Popen(
            cmd, 
            cwd=cwd, 
            shell=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding='utf-8',
            errors='replace',
            bufsize=1
        )
        
        # Pipe output lines to the logger
        if self._proc.stdout:
            for line in self._proc.stdout:
                logger.info(f"[Process Output] {line.rstrip()}")

        exit_code = self._proc.wait()

        # Cleanup if required
        if self.cfg.clean_after and self.cfg.extract_rom:
            import shutil
            shutil.rmtree(Path(self.cfg.rom_path).parent, ignore_errors=True)

        self._stop_keymapper()
        return exit_code

    def terminate(self):
        """Kill the running emulator process if still alive."""
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _replace_tags(self, text: str) -> str:
        """Dynamically replaces [ROMPATH], [ROMNAME], [ROMFILE], and [EMUPATH] tags."""
        if not text:
            return ""
        rom_p = Path(self.cfg.rom_path)
        emu_p = Path(self.cfg.emulator_path.strip('"'))
        # Replacements using pathlib components
        replacements = {
            "[ROMPATH]": str(rom_p.parent),
            "[ROMNAME]": rom_p.stem,
            "[ROMFILE]": rom_p.name,
            "[EMUPATH]": str(emu_p.parent)
        }
        for tag, val in replacements.items():
            text = text.replace(tag, val)
        return text

    def _build_command(self) -> list[str]:
        rom = self.cfg.rom_path

        if not self.cfg.include_path:
            rom = Path(rom).name
        if not self.cfg.include_extension:
            rom = Path(rom).stem

        is_posix = sys.platform != "win32"
        parts = [self.cfg.emulator_path]
        if self.cfg.options:
            opts = self._replace_tags(self.cfg.options)
            parts += shlex.split(opts, posix=is_posix)
        if self.cfg.use_quotes:
            parts.append(rom)
        else:
            parts.append(rom)
        if self.cfg.arguments:
            args = self._replace_tags(self.cfg.arguments)
            parts += shlex.split(args, posix=is_posix)
        return parts

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
        app_root() / "Emulators",
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

    from core.config import global_config
    if global_config().get("GLOBAL", "validate_bios", fallback="0") != "1":
        return BiosStatus(missing=[], present=[], errors=[])

    bios_json = assets_dir() / "bios.json"
    if not bios_json.exists():
        return BiosStatus(missing=[], present=[], errors=["BIOS database not found"])

    try:
        with open(bios_json, "r", encoding="utf-8") as f:
            bios_data = json.load(f)
        
        if not isinstance(bios_data, dict):
            raise ValueError("BIOS database format mismatch (expected dictionary)")
            
        entries = bios_data.get("entries", {})
        if not isinstance(entries, dict):
            raise ValueError("'entries' section is not a dictionary")
    except Exception as e:
        return BiosStatus(missing=[], present=[], errors=[f"Failed to load BIOS data: {e}"])

    # If auditing, check global entries or iterate systems
    emu_entry = {}
    if system == "Audit-Mode":
        for s_data in entries.values():
            if emu_name.lower() in s_data:
                emu_entry = s_data.get(emu_name.lower())
                break
    else:
        system_entry = entries.get(system)
        if not isinstance(system_entry, dict):
            system_entry = {}
        emu_entry = system_entry.get(emu_name.lower())

    if not isinstance(emu_entry, dict):
        emu_entry = {}
        
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
    app_home = app_root() / "Emulators"
    status = verify_bios(emu_name, system, app_home)

    warnings = []

    for req in status.missing:
        warnings.append(f"Missing BIOS: {req.name} (expected in {req.path})")

    for err in status.errors:
        warnings.append(f"BIOS error: {err}")

    can_launch = len(status.missing) == 0 and len(status.errors) == 0

    return can_launch, warnings
