"""
core/updater.py

Application self-update logic.
Replaces update.ahk.
"""
import subprocess
import sys
from pathlib import Path

import requests

from core.config import Config, global_config


class Updater:
    """
    Checks for a newer version and downloads / extracts the update archive.

    The version manifest is a plain-text file hosted at SOURCEHOST whose
    first line is  version=<tag>  (matching the AHK update.ahk convention).
    """

    def __init__(self):
        self._arcorg = Config(Config.ARCORG_FILE)
        self._settings = global_config()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def current_version(self) -> str:
        return self._arcorg.get("GLOBAL", "Version", fallback="")

    @property
    def source_host(self) -> str:
        return self._arcorg.get("GLOBAL", "SOURCEHOST", fallback="")

    @property
    def update_url(self) -> str:
        raw = self._arcorg.get("GLOBAL", "UPDATEFILE", fallback="")
        return raw.split("|")[0].strip()

    def check(self) -> tuple[bool, str]:
        """
        Fetch the remote version string.

        Returns:
            (update_available: bool, remote_version: str)
        """
        try:
            resp = requests.get(self.source_host + "/releases/latest",
                                timeout=10, allow_redirects=True)
            # GitHub redirects to the tag URL; extract tag from final URL
            remote = resp.url.rstrip("/").split("/")[-1]
            return remote != self.current_version, remote
        except Exception:
            return False, ""

    def download_and_apply(self, cache_dir: str, seven_zip_path: str) -> bool:
        """
        Download the update archive and extract it over the current install.

        Returns True on success.
        """
        cache = Path(cache_dir)
        cache.mkdir(parents=True, exist_ok=True)
        archive = cache / "skeletonkey_update.zip"

        try:
            resp = requests.get(self.update_url, stream=True, timeout=60)
            resp.raise_for_status()
            with open(archive, "wb") as fh:
                for chunk in resp.iter_content(65536):
                    fh.write(chunk)
        except Exception as exc:
            return False

        home = Path(__file__).resolve().parent.parent
        result = subprocess.run(
            [seven_zip_path, "x", "-y", str(archive), f"-O{home}"],
            capture_output=True,
        )
        archive.unlink(missing_ok=True)
        return result.returncode == 0
