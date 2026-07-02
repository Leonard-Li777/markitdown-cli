"""
LibreOffice Detection Module

Cross-platform detection of LibreOffice installation with multi-tier fallback strategies.
Provides a unified `detectLibreOffice()` entry point that returns a
`LibreOfficeDetectionResult` describing what was found (or why not found).

Export:
    detectLibreOffice() -> LibreOfficeDetectionResult
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Optional


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class LibreOfficeDetectionResult:
    """Outcome of a LibreOffice detection attempt."""
    installed: bool = False
    version: Optional[str] = None
    path: Optional[str] = None          # Absolute path to the soffice executable
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# Version extraction  (section 7 of the plan)
# ---------------------------------------------------------------------------

def extractVersion(lo_path: str, timeout: int = 30) -> Optional[str]:
    """
    Extract the LibreOffice version string by running ``<lo_path> --version``.

    On Windows ``soffice.exe --version`` often returns no output; this function
    automatically tries the ``soffice.com`` counterpart as a fallback.

    Two matching strategies (in order of priority):
      1. ``LibreOffice X.Y.Z.W``  →  e.g.  ``7.6.4.1``
      2. bare dotted version       →  e.g.  ``24.2.0``
    """

    def _run_and_extract(exe: str) -> Optional[str]:
        try:
            kwargs: dict = {
                "args": [exe, "--version"],
                "capture_output": True,
                "text": True,
                "timeout": timeout,
                "stdin": subprocess.DEVNULL,  # Prevent blocking on "Press Enter to continue..."
            }
            if sys.platform == "win32":
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                kwargs["startupinfo"] = startupinfo
            result = subprocess.run(**kwargs)
            output = (result.stdout or result.stderr or "").strip()
        except (OSError, subprocess.TimeoutExpired):
            return None

        if not output:
            return None

        # Priority 1: "LibreOffice 7.6.4.1" style
        m = re.search(r"LibreOffice\s+([\d.]+)", output)
        if m:
            return m.group(1)

        # Priority 2: bare dotted version
        m = re.search(r"([\d.]+)", output)
        if m:
            return m.group(1)

        return None

    # On Windows, soffice.exe --version produces no output and can hang for
    # the full timeout. Try soffice.com first, which reliably outputs version info.
    candidates = [lo_path]
    if sys.platform == "win32":
        dirname = os.path.dirname(lo_path)
        com_in_dir = os.path.join(dirname, "soffice.com")
        if com_in_dir != lo_path and os.path.isfile(com_in_dir):
            candidates.insert(0, com_in_dir)

    for exe in candidates:
        version = _run_and_extract(exe)
        if version is not None:
            return version

    return None


# ---------------------------------------------------------------------------
# Windows short-path helper  (section 4.4)
# ---------------------------------------------------------------------------

def toShortPathOnWindows(long_path: str) -> str:
    """
    Convert a long Windows path to its 8.3 short form
    (e.g. ``C:\\Program Files\\LibreOffice`` → ``C:\\PROGRA~1\\LIBREOF~1``).

    Uses ``GetShortPathNameW`` from ``kernel32.dll`` via ctypes.
    Falls back to the original long path on failure or on non-Windows platforms.
    """
    if sys.platform != "win32":
        return long_path

    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.windll.kernel32
        buf = ctypes.create_unicode_buffer(260)
        length = kernel32.GetShortPathNameW(long_path, buf, len(buf))
        if length > 0 and length < len(buf):
            return buf.value
    except (OSError, AttributeError, ImportError):
        pass

    return long_path


# ---------------------------------------------------------------------------
# Windows-specific detection  (section 4)
# ---------------------------------------------------------------------------

def _query_registry(key: str, value_name: str = "InstallPath") -> Optional[str]:
    """
    Query a Windows Registry key using ``reg query``.
    Returns the string data of *value_name* if found, otherwise ``None``.
    """
    try:
        result = subprocess.run(
            ["reg", "query", key, "/v", value_name],
            capture_output=True,
            text=True,
            timeout=5,
        )
        output = result.stdout.strip()
        # Typical output line:  InstallPath    REG_SZ    C:\Program Files\LibreOffice
        m = re.search(r"InstallPath\s+REG_SZ\s+(.+)", output)
        if m:
            return m.group(1).strip()
    except (OSError, subprocess.TimeoutExpired):
        pass
    return None


def _soffice_exists(path: str) -> bool:
    """Return ``True`` if *path* points to an existing, executable ``soffice``."""
    return os.path.isfile(path)


def detectLibreOfficeWindows() -> LibreOfficeDetectionResult:
    """
    Windows detection with three-tier fallback:

      1. Registry  (HKLM\\SOFTWARE\\LibreOffice\\UNO → install path → soffice.exe)
      2. Common installation paths  (Program Files, Program Files (x86))
      3. ``where soffice``  (PATH)
    """
    soffice_exe = "soffice.exe"

    # ---- Tier 1: Registry queries ----
    reg_keys = [
        r"HKLM\SOFTWARE\LibreOffice\UNO",
        r"HKLM\SOFTWARE\WOW6432Node\LibreOffice\UNO",
        r"HKCU\SOFTWARE\LibreOffice\UNO",
    ]
    for key in reg_keys:
        install_dir = _query_registry(key)
        if install_dir:
            candidate = os.path.join(install_dir, "program", soffice_exe)
            if _soffice_exists(candidate):
                short_path = toShortPathOnWindows(candidate)
                return LibreOfficeDetectionResult(
                    installed=True,
                    path=short_path,
                )

    # ---- Tier 2: Common installation paths ----
    common_dirs = [
        r"C:\Program Files\LibreOffice",
        r"C:\Program Files (x86)\LibreOffice",
    ]
    for base_dir in common_dirs:
        candidate = os.path.join(base_dir, "program", soffice_exe)
        if _soffice_exists(candidate):
            short_path = toShortPathOnWindows(candidate)
            return LibreOfficeDetectionResult(
                installed=True,
                path=short_path,
            )

    # ---- Tier 3: PATH lookup via ``where`` ----
    try:
        result = subprocess.run(
            ["where", soffice_exe],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            lines = [line.strip() for line in result.stdout.strip().splitlines() if line.strip()]
            # Filter: prefer paths ending in .exe, and fix paths with abnormal
            # whitespace (e.g. "C:\Program Files\LibreOffice\program\soffice .exe").
            for line in lines:
                cleaned = re.sub(r"\s+\.exe$", ".exe", line)
                if cleaned.lower().endswith(".exe") and _soffice_exists(cleaned):
                    short_path = toShortPathOnWindows(cleaned)
                    return LibreOfficeDetectionResult(
                        installed=True,
                        path=short_path,
                    )
    except (OSError, subprocess.TimeoutExpired):
        pass

    return LibreOfficeDetectionResult(
        installed=False,
        error="LibreOffice not found. Install from https://libreoffice.org "
              "or ensure 'libreoffice' is on your PATH.",
    )


# ---------------------------------------------------------------------------
# macOS-specific detection  (section 5)
# ---------------------------------------------------------------------------

def detectLibreOfficeMacOS() -> LibreOfficeDetectionResult:
    """
    macOS detection with two-tier fallback:

      1. /Applications/LibreOffice.app  (standard .app bundle)
      2. ``which soffice``             (command-line tool on PATH)
    """
    # ---- Tier 1: /Applications bundle ----
    app_bundle = "/Applications/LibreOffice.app"
    if os.path.isdir(app_bundle):
        candidate = os.path.join(app_bundle, "Contents", "MacOS", "soffice")
        if os.path.isfile(candidate):
            return LibreOfficeDetectionResult(
                installed=True,
                path=candidate,
            )

    # ---- Tier 2: which soffice ----
    candidate = shutil.which("soffice")
    if candidate and os.path.isfile(candidate):
        return LibreOfficeDetectionResult(
            installed=True,
            path=candidate,
        )

    return LibreOfficeDetectionResult(
        installed=False,
        error="LibreOffice not found. Install from https://libreoffice.org "
              "or run 'brew install --cask libreoffice'.",
    )


# ---------------------------------------------------------------------------
# Linux-specific detection  (section 6)
# ---------------------------------------------------------------------------

def _dpkg_check() -> Optional[str]:
    """Check via ``dpkg -l`` — returns the soffice path or ``None``."""
    try:
        r = subprocess.run(
            ["dpkg", "-l"],
            capture_output=True, text=True, timeout=10,
        )
        if r.returncode != 0:
            return None
        if re.search(r"(?m)^ii\s+libre", r.stdout):
            candidate = shutil.which("libreoffice")
            if candidate:
                return candidate
    except (OSError, subprocess.TimeoutExpired):
        pass
    return None


def _rpm_check() -> Optional[str]:
    """Check via ``rpm -qa`` — returns the soffice path or ``None``."""
    try:
        r = subprocess.run(
            ["rpm", "-qa"],
            capture_output=True, text=True, timeout=10,
        )
        if r.returncode != 0:
            return None
        if re.search(r"(?m)^libreoffice", r.stdout):
            candidate = shutil.which("libreoffice")
            if candidate:
                return candidate
    except (OSError, subprocess.TimeoutExpired):
        pass
    return None


def detectLibreOfficeLinux() -> LibreOfficeDetectionResult:
    """
    Linux detection with three-tier fallback:

      1. ``which soffice`` or ``shutil.which("soffice")``
      2. ``dpkg -l`` — check Debian package manager, then ``which libreoffice``
      3. ``rpm -qa`` — check RPM package manager, then ``which libreoffice``
    """
    # ---- Tier 1: which (most direct) ----
    for name in ("soffice", "libreoffice"):
        candidate = shutil.which(name)
        if candidate and os.path.isfile(candidate):
            return LibreOfficeDetectionResult(
                installed=True,
                path=candidate,
            )

    # ---- Tier 2: dpkg (Debian / Ubuntu) ----
    candidate = _dpkg_check()
    if candidate and os.path.isfile(candidate):
        return LibreOfficeDetectionResult(
            installed=True,
            path=candidate,
        )

    # ---- Tier 3: rpm (RedHat / Fedora / SUSE) ----
    candidate = _rpm_check()
    if candidate and os.path.isfile(candidate):
        return LibreOfficeDetectionResult(
            installed=True,
            path=candidate,
        )

    return LibreOfficeDetectionResult(
        installed=False,
        error="LibreOffice not found. Install from https://libreoffice.org "
              "or use your package manager: "
              "sudo apt install libreoffice  /  sudo dnf install libreoffice",
    )


# ---------------------------------------------------------------------------
# Explicit path override (set via CLI --libreoffice-path)
# ---------------------------------------------------------------------------

_explicit_path: Optional[str] = None


def setLibreOfficePath(path: str) -> None:
    """
    Bypass auto-detection and force a specific LibreOffice path.
    Call before ``detectLibreOffice()`` or ``findLibreOfficePath()``.
    """
    global _explicit_path
    if not os.path.isfile(path):
        raise FileNotFoundError(f"LibreOffice executable not found: {path}")
    _explicit_path = path


# ---------------------------------------------------------------------------
# Main entry point  (section 3)
# ---------------------------------------------------------------------------

def detectLibreOffice() -> LibreOfficeDetectionResult:
    """
    Detect LibreOffice installation on the current platform.

    If an explicit path was set via ``setLibreOfficePath()``, it is used
    directly without auto-detection.

    Dispatch table:

    Dispatch table:

    +---------+---------------------------+
    | Platform | Method                   |
    +---------+---------------------------+
    | win32   | detectLibreOfficeWindows() |
    | darwin  | detectLibreOfficeMacOS()   |
    | linux   | detectLibreOfficeLinux()   |
    | other   | unsupported               |
    +---------+---------------------------+
    """
    platform_map = {
        "win32": detectLibreOfficeWindows,
        "darwin": detectLibreOfficeMacOS,
        "linux": detectLibreOfficeLinux,
    }

    # Explicit path override — skip auto-detection entirely
    if _explicit_path is not None:
        return LibreOfficeDetectionResult(
            installed=True,
            path=toShortPathOnWindows(_explicit_path) if sys.platform == "win32" else _explicit_path,
        )

    detect_fn = platform_map.get(sys.platform)
    if detect_fn is None:
        return LibreOfficeDetectionResult(
            installed=False,
            error=f"Unsupported platform: {sys.platform}",
        )

    return detect_fn()


# ---------------------------------------------------------------------------
# Convenience: find the soffice path, raise if not found
# ---------------------------------------------------------------------------

def findLibreOfficePath() -> str:
    """
    Return the absolute path to the LibreOffice executable, or raise
    ``FileNotFoundError`` with a descriptive message on failure.

    This is the simplest drop-in replacement for the old ``_find_libreoffice()``
    helper used throughout the codebase.
    """
    result = detectLibreOffice()
    if not result.installed or result.path is None:
        raise FileNotFoundError(result.error or "LibreOffice not found")
    return result.path


def getLibreOfficeVersion() -> Optional[str]:
    """
    Retrieve the LibreOffice version string by running ``soffice --version``
    on the detected installation. Returns ``None`` if LibreOffice is not
    installed, the executable cannot be found, or version extraction fails.

    Unlike ``detectLibreOffice()``, this explicitly invokes the LibreOffice
    binary, so it is slower but provides version information when needed.
    """
    result = detectLibreOffice()
    if not result.installed or result.path is None:
        return None
    return extractVersion(result.path)


def findLibreOfficePython() -> Optional[str]:
    """
    Locate the Python interpreter bundled with LibreOffice (which has the
    ``uno`` module available), or return ``None`` if not found.
    """
    lo = findLibreOfficePath()
    lo_dir = os.path.dirname(lo)
    # Typical locations:
    candidates = [
        os.path.join(lo_dir, "python.exe"),                              # Windows
        os.path.join(lo_dir, "python"),                                  # Linux/macOS
        os.path.join(os.path.dirname(lo_dir), "python", "python3"),      # macOS .app bundle
    ]
    for cand in candidates:
        if os.path.isfile(cand):
            try:
                r = subprocess.run([cand, "-c", "import uno; print('ok')"],
                                   capture_output=True, text=True, timeout=5)
                if r.returncode == 0:
                    return cand
            except (OSError, subprocess.TimeoutExpired):
                pass
    return None
