#!/usr/bin/env python3
"""
Cross-platform build script for MarkItDown CLI with bundled Tesseract OCR.

Produces a single-file executable with Chinese + English OCR support.

Usage:
    python scripts/build.py                   # Build for current platform

Structure:
    markitdown/              git submodule — microsoft/markitdown source
    overrides/               our OCR patches applied on top of submodule
    scripts/build.py         this file
    scripts/markitdown_cli_wrapper.py  PyInstaller entry point
    markitdown.spec          PyInstaller spec

Requirements:
    Python >= 3.10, pip

Platform support:
    Windows  : downloads Tesseract from UB-Mannheim (Inno Setup installer)
    Linux    : downloads static binary from DanielMYT/tesseract-static
    macOS    : installs via Homebrew + dylibbundler, bundles dylibs
"""

import argparse
import os
import platform
import shutil
import signal
import ssl
import stat
import subprocess
import sys
import time
import urllib.request
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DIST_DIR = REPO_ROOT / "dist"
SUBMODULE_DIR = REPO_ROOT / "markitdown"
OVERRIDES_DIR = REPO_ROOT / "overrides"
SYSTEM = platform.system()
ARCH = platform.machine()

# ---------------------------------------------------------------------------
# Paths inside the submodule
# ---------------------------------------------------------------------------
MARKITDOWN_PKG = SUBMODULE_DIR / "packages" / "markitdown"
MARKITDOWN_OCR_PKG = SUBMODULE_DIR / "packages" / "markitdown-ocr"

# ---------------------------------------------------------------------------
# Tesseract download URLs
# ---------------------------------------------------------------------------
TESSERACT_VERSION = "5.5.2"

LINUX_TESSERACT_URLS = {
    "x86_64": f"https://github.com/DanielMYT/tesseract-static/releases/download/tesseract-{TESSERACT_VERSION}/tesseract.x86_64",
    "aarch64": f"https://github.com/DanielMYT/tesseract-static/releases/download/tesseract-{TESSERACT_VERSION}/tesseract.aarch64",
    "arm64":   f"https://github.com/DanielMYT/tesseract-static/releases/download/tesseract-{TESSERACT_VERSION}/tesseract.aarch64",
}

WINDOWS_TESSERACT_URL = (
    "https://sourceforge.net/projects/tesseract-ocr.mirror/files/5.5.0/"
    "tesseract-ocr-w64-setup-5.5.0.20241111.exe/download"
)

EXIFTOOL_VERSION = "13.59"
WINDOWS_EXIFTOOL_URL = f"https://sourceforge.net/projects/exiftool/files/exiftool-{EXIFTOOL_VERSION}_64.zip/download"
UNIX_EXIFTOOL_URL = f"https://sourceforge.net/projects/exiftool/files/Image-ExifTool-{EXIFTOOL_VERSION}.tar.gz/download"

TESDATA_BASE = "https://github.com/tesseract-ocr/tessdata_fast/raw/main"
TESSDATA_LANGS = ["eng", "chi_sim", "chi_tra"]


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
def info(msg):  print(f"[*] {msg}")
def ok(msg):    print(f"[+] {msg}")
def warn(msg):  print(f"[!] {msg}")
def fail(msg):  print(f"[!] {msg}"); sys.exit(1)


# ---------------------------------------------------------------------------
# Step 1 — apply overrides on top of submodule
# ---------------------------------------------------------------------------
def apply_overrides():
    if not OVERRIDES_DIR.is_dir():
        info("No overrides directory found, skipping")
        return

    info("Applying overrides on top of submodule...")
    for src in OVERRIDES_DIR.rglob("*"):
        if src.is_file():
            rel = src.relative_to(OVERRIDES_DIR)
            dst = SUBMODULE_DIR / "packages" / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            ok(f"  {rel}")


def restore_submodule():
    """Restore submodule to pristine state — discard all changes + untracked files."""
    info("Restoring submodule to pristine state...")
    subprocess.run(["git", "checkout", "--", "."], cwd=str(SUBMODULE_DIR), capture_output=True)
    subprocess.run(["git", "clean", "-fd"], cwd=str(SUBMODULE_DIR), capture_output=True)
    ok("Submodule restored")


# ---------------------------------------------------------------------------
# Download helpers
# ---------------------------------------------------------------------------
def download_file(url, dest, max_retries=3):
    info(f"Downloading {dest.name}...")
    for attempt in range(1, max_retries + 1):
        try:
            ctx = ssl.create_default_context()
            req = urllib.request.Request(url, headers={"User-Agent": "MarkItDown-Build/1.0"})
            with urllib.request.urlopen(req, context=ctx, timeout=120) as resp:
                with open(dest, "wb") as f:
                    shutil.copyfileobj(resp, f)
            size = dest.stat().st_size
            ok(f"{dest.name} ({size // 1024} KB)")
            return
        except Exception as e:
            if dest.exists():
                dest.unlink()
            if attempt < max_retries:
                warn(f"Download attempt {attempt}/{max_retries} failed: {e}. Retrying...")
                import time
                time.sleep(2 * attempt)
            else:
                raise


def download_tessdata(tessdata_dir: Path):
    tessdata_dir.mkdir(parents=True, exist_ok=True)
    for lang in TESSDATA_LANGS:
        dest = tessdata_dir / f"{lang}.traineddata"
        if dest.exists():
            ok(f"{lang}.traineddata already present")
            continue
        download_file(f"{TESDATA_BASE}/{lang}.traineddata", dest)


# ---------------------------------------------------------------------------
# Step 2 — Tesseract per platform
# ---------------------------------------------------------------------------
def setup_tesseract_windows(tesseract_dir: Path):
    installer = DIST_DIR / "tesseract_setup.exe"

    if not installer.exists():
        download_file(WINDOWS_TESSERACT_URL, installer)

    seven_zip = (
        shutil.which("7z")
        or shutil.which("7za")
        or (Path("C:/Program Files/7-Zip/7z.exe") if Path("C:/Program Files/7-Zip/7z.exe").exists() else None)
        or (Path("C:/Program Files (x86)/7-Zip/7z.exe") if Path("C:/Program Files (x86)/7-Zip/7z.exe").exists() else None)
    )

    if seven_zip:
        info("Extracting with 7-Zip...")
        subprocess.run(
            [str(seven_zip), "x", str(installer), f"-o{str(tesseract_dir)}", "-y"],
            capture_output=True, check=False,
        )

    exe = tesseract_dir / "tesseract.exe"
    if not exe.exists():
        info("Running installer silently (may need admin elevation)...")
        subprocess.run(
            [str(installer), "/VERYSILENT", "/SUPPRESSMSGBOXES", f"/DIR={tesseract_dir}"],
            check=False, timeout=180,
        )

    if not exe.exists():
        found = list(tesseract_dir.rglob("tesseract.exe"))
        if found:
            shutil.copy2(found[0], exe)

    # Flatten DLLs
    for dll in tesseract_dir.rglob("*.dll"):
        target = tesseract_dir / dll.name
        if not target.exists():
            shutil.copy2(dll, target)

    # Pre-populate tessdata from installer if available
    tess_src = next(tesseract_dir.rglob("tessdata"), None)
    if tess_src and tess_src.is_dir():
        tess_dst = tesseract_dir / "tessdata"
        if not tess_dst.exists() or not any(tess_dst.iterdir()):
            tess_dst.mkdir(exist_ok=True)
            for f in tess_src.iterdir():
                shutil.copy2(f, tess_dst)

    if installer.exists():
        installer.unlink()
        info("Removed installer")

    if exe.exists():
        ok("Tesseract extracted and flattened")
    else:
        warn("tesseract.exe not found — OCR will not work")


def setup_exiftool_windows(exiftool_dir: Path):
    zip_file = DIST_DIR / "exiftool_setup.zip"
    temp_dir = DIST_DIR / "exiftool_temp"

    if not zip_file.exists():
        download_file(WINDOWS_EXIFTOOL_URL, zip_file)

    if temp_dir.exists():
        shutil.rmtree(temp_dir)
    temp_dir.mkdir(parents=True, exist_ok=True)

    import zipfile
    with zipfile.ZipFile(zip_file, 'r') as zip_ref:
        zip_ref.extractall(temp_dir)

    # Find the main directory inside temp_dir and move contents
    subdirs = [d for d in temp_dir.iterdir() if d.is_dir()]
    if subdirs:
        for item in subdirs[0].iterdir():
            target = exiftool_dir / item.name
            if target.exists():
                if target.is_dir():
                    shutil.rmtree(target)
                else:
                    target.unlink()
            shutil.move(str(item), str(target))
    else:
        for item in temp_dir.iterdir():
            target = exiftool_dir / item.name
            if target.exists():
                if target.is_dir():
                    shutil.rmtree(target)
                else:
                    target.unlink()
            shutil.move(str(item), str(target))

    # Clean up temp_dir and zip
    shutil.rmtree(temp_dir)
    if zip_file.exists():
        zip_file.unlink()
        info("Removed ExifTool zip archive")

    # Rename exiftool(-k).exe to exiftool.exe
    exe_k = exiftool_dir / "exiftool(-k).exe"
    if exe_k.exists():
        shutil.move(str(exe_k), str(exiftool_dir / "exiftool.exe"))

    exe = exiftool_dir / "exiftool.exe"
    if exe.exists():
        ok("ExifTool extracted and set up")
    else:
        warn("exiftool.exe not found — ExifTool metadata extraction will not work")


def _setup_exiftool_unix(exiftool_dir: Path):
    """Download Image-ExifTool-*.tar.gz, extract the exiftool Perl script + lib/."""
    archive = DIST_DIR / f"Image-ExifTool-{EXIFTOOL_VERSION}.tar.gz"
    if not archive.exists():
        download_file(UNIX_EXIFTOOL_URL, archive)

    import tarfile
    info("Extracting ExifTool...")
    with tarfile.open(archive, "r:gz") as tar:
        tar.extractall(DIST_DIR)
    archive.unlink()
    info("Removed ExifTool tar.gz archive")

    extracted = DIST_DIR / f"Image-ExifTool-{EXIFTOOL_VERSION}"
    if extracted.is_dir():
        for item in extracted.iterdir():
            target = exiftool_dir / item.name
            if target.exists():
                if target.is_dir():
                    shutil.rmtree(target)
                else:
                    target.unlink()
            shutil.move(str(item), str(target))
        shutil.rmtree(extracted)

    # Ensure exiftool script has execute permission
    exe = exiftool_dir / "exiftool"
    if exe.exists():
        exe.chmod(exe.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        ok("ExifTool extracted and set up")
    else:
        warn("exiftool not found — ExifTool metadata extraction will not work")


def setup_exiftool_macos(exiftool_dir: Path):
    _setup_exiftool_unix(exiftool_dir)


def setup_exiftool_linux(exiftool_dir: Path):
    _setup_exiftool_unix(exiftool_dir)


def setup_tesseract_linux(tesseract_dir: Path):
    if ARCH not in LINUX_TESSERACT_URLS:
        fail(f"Unsupported architecture: {ARCH}")

    tesseract_dir.mkdir(parents=True, exist_ok=True)
    binary = tesseract_dir / "tesseract"
    download_file(LINUX_TESSERACT_URLS[ARCH], binary)
    binary.chmod(binary.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def setup_tesseract_macos(tesseract_dir: Path):
    if not shutil.which("brew"):
        fail("Homebrew is required on macOS. Install from https://brew.sh")

    if not shutil.which("tesseract"):
        info("Installing Tesseract via Homebrew...")
        subprocess.run(["brew", "install", "tesseract"], check=True)

    if not shutil.which("dylibbundler"):
        info("Installing dylibbundler...")
        subprocess.run(["brew", "install", "dylibbundler"], check=True)

    tess_bin = Path(shutil.which("tesseract")).resolve()

    tesseract_dir.mkdir(parents=True, exist_ok=True)
    bin_dir = tesseract_dir / "bin"
    lib_dir = tesseract_dir / "lib"
    bin_dir.mkdir(exist_ok=True)
    lib_dir.mkdir(exist_ok=True)

    tess_target = bin_dir / "tesseract"
    shutil.copy2(tess_bin, tess_target)

    subprocess.run([
        "dylibbundler", "-od", "-b",
        "-x", str(tess_target),
        "-d", str(lib_dir),
        "-p", "@executable_path/../lib/",
    ], check=True)

    ok(f"macOS Tesseract bundle ready")


# ---------------------------------------------------------------------------
# Step 3 — PyInstaller build
# ---------------------------------------------------------------------------
def _pyinstaller_boot_exe() -> Path:
    name = "_markitdown_boot.exe" if SYSTEM == "Windows" else "_markitdown_boot"
    return DIST_DIR / "markitdown" / name


def _bundled_internal_dir() -> Path | None:
    """Return PyInstaller _internal/ dir (pre-flatten or post-flatten)."""
    for candidate in (DIST_DIR / "markitdown" / "_internal", DIST_DIR / "_internal"):
        if candidate.is_dir():
            return candidate
    return None


def _run_pyinstaller(
    timeout_build: int = 3600,
    timeout_cleanup: int = 180,
) -> subprocess.CompletedProcess:
    """Run PyInstaller with build/cleanup-aware timeouts.

    Never capture stdout/stderr to PIPE — PyInstaller's verbose output can
    fill OS pipe buffers (~64 KiB) and deadlock the build before dist/ exists.

    Once dist/markitdown/_markitdown_boot exists the bundle is complete; if
    PyInstaller then hangs in cache cleanup (common on python-build-standalone)
    we kill it after timeout_cleanup seconds.
    """
    cmd = [
        sys.executable, "-m", "PyInstaller",
        str(REPO_ROOT / "markitdown.spec"),
        "--noconfirm",
        "--log-level=WARN",
    ]
    info("Running PyInstaller...")
    log_path = REPO_ROOT / "build" / "pyinstaller.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    popen_kwargs: dict = {"cwd": str(REPO_ROOT)}
    if os.name != "nt":
        popen_kwargs["preexec_fn"] = os.setsid

    with open(log_path, "w", encoding="utf-8", errors="replace") as log:
        popen_kwargs["stdout"] = log
        popen_kwargs["stderr"] = subprocess.STDOUT
        proc = subprocess.Popen(cmd, **popen_kwargs)
        start = time.monotonic()
        cleanup_since: float | None = None

        while True:
            rc = proc.poll()
            if rc is not None:
                log.write(f"\n[build.py] PyInstaller exited with code {rc}\n")
                log.flush()
                return subprocess.CompletedProcess(cmd, rc, "", "")

            boot_ready = _pyinstaller_boot_exe().is_file()
            elapsed = time.monotonic() - start

            if boot_ready:
                if cleanup_since is None:
                    cleanup_since = time.monotonic()
                    info("PyInstaller bundle ready — waiting for process exit")
                elif time.monotonic() - cleanup_since > timeout_cleanup:
                    warn(
                        f"PyInstaller stuck after bundle ready "
                        f"(>{timeout_cleanup}s) — killing process group"
                    )
                    _kill_process_group(proc)
                    log.write("\n[build.py] killed after cleanup timeout\n")
                    log.flush()
                    return subprocess.CompletedProcess(cmd, 0, "", "")
            elif elapsed > timeout_build:
                warn(f"PyInstaller build timed out after {timeout_build}s")
                _kill_process_group(proc)
                log.write(f"\n[build.py] killed after build timeout ({timeout_build}s)\n")
                log.flush()
                return subprocess.CompletedProcess(cmd, 1, "", "")

            time.sleep(5)


def _kill_process_group(proc: subprocess.Popen) -> None:
    if os.name == "nt":
        proc.kill()
        proc.wait(timeout=30)
        return
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (ProcessLookupError, OSError):
        proc.kill()
    proc.wait(timeout=30)


def _verify_pyinstaller_output() -> None:
    boot = _pyinstaller_boot_exe()
    internal = _bundled_internal_dir()
    if boot.is_file() and internal is not None:
        ok(f"Executable: {boot} ({boot.stat().st_size // (1024 * 1024)} MB)")
        return
    if internal is not None:
        warn(f"Boot binary missing at {boot}, but _internal/ exists")
    else:
        warn("PyInstaller output not found in dist/markitdown/")
    fail("PyInstaller did not produce expected onedir bundle")


def build_markitdown(onefile: bool):
    info("Installing Python build dependencies...")
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "pyinstaller", "pytesseract"],
        check=False, capture_output=True,
    )
    # Ensure magika >= 1.0.3 (score is on result, not result.output)
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "magika>=1.0.3,<2.0"],
        check=False, capture_output=True,
    )

    for name, pkg_path in [("markitdown", MARKITDOWN_PKG), ("markitdown-ocr", MARKITDOWN_OCR_PKG)]:
        if not (pkg_path / "pyproject.toml").exists():
            warn(f"Skipping {name} — no pyproject.toml at {pkg_path}")
            continue
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "-e", str(pkg_path),
             "--no-deps", "--no-build-isolation"],
            check=False, capture_output=True, timeout=60,
        )
        ok(f"{name} installed from submodule")

    result = _run_pyinstaller()
    if result.returncode != 0:
        log_path = REPO_ROOT / "build" / "pyinstaller.log"
        if log_path.is_file():
            tail = log_path.read_text(encoding="utf-8", errors="replace")[-4000:]
            print(tail, file=sys.stderr)
        result.check_returncode()
    _verify_pyinstaller_output()


# ---------------------------------------------------------------------------
# Post-build: ensure encodings is inside base_library.zip
# ---------------------------------------------------------------------------
def _encodings_source_dir(internal: Path) -> Path | None:
    """Locate encodings/ files to inject into base_library.zip."""
    bundled = internal / "encodings"
    if bundled.is_dir():
        return bundled
    try:
        import encodings as _encodings_mod
        system_dir = Path(_encodings_mod.__file__).resolve().parent
        if system_dir.is_dir():
            info(f"Using system encodings from {system_dir}")
            return system_dir
    except ImportError:
        pass
    return None


def ensure_encodings_in_zip():
    """Ensure encodings is available for the PyInstaller bootloader.

    With noarchive=True (spec), stdlib modules live as loose files under
    _internal/encodings/.  With noarchive=False, they must be inside
    base_library.zip — we inject them if Analysis missed them.
    """
    internal = _bundled_internal_dir()
    if internal is None:
        warn("No _internal/ directory found — skipping encodings check")
        return

    loose = internal / "encodings"
    if loose.is_dir() and any(loose.iterdir()):
        ok("encodings present as loose files in _internal/encodings/")
        return

    zip_path = internal / "base_library.zip"
    if not zip_path.is_file():
        warn(f"base_library.zip not found at {zip_path} — skipping encodings injection")
        return

    enc_dir = _encodings_source_dir(internal)
    if enc_dir is None:
        warn("encodings/ source not found — skipping injection")
        return

    with zipfile.ZipFile(str(zip_path), "r") as z:
        existing = set(z.namelist())
    has_enc = any(n.startswith("encodings/") for n in existing)
    if has_enc:
        ok("encodings already in base_library.zip")
        return

    info("Injecting encodings/ into base_library.zip...")
    # Build a fresh zip with the existing content + encodings/
    tmp = zip_path.with_suffix(".zip.tmp")
    with zipfile.ZipFile(str(tmp), "w", zipfile.ZIP_DEFLATED) as zout:
        # Copy existing entries
        with zipfile.ZipFile(str(zip_path), "r") as zin:
            for item in zin.infolist():
                zout.writestr(item, zin.read(item.filename))
        # Add encodings files
        for root, dirs, files in os.walk(str(enc_dir)):
            for fn in files:
                src = os.path.join(root, fn)
                rel = os.path.relpath(src, str(enc_dir))
                arcname = f"encodings/{rel}"
                zout.write(src, arcname)
                if len(rel) < 60:
                    info(f"  + {arcname}")
    tmp.replace(zip_path)
    ok("encodings injected into base_library.zip")


# ---------------------------------------------------------------------------
# Post-build: print output tree
# ---------------------------------------------------------------------------
def print_output_tree():
    print()
    info("Output:")
    for f in sorted(DIST_DIR.rglob("*")):
        if f.is_file() and not any(p.startswith(".") for p in f.parts):
            rel = f.relative_to(REPO_ROOT)
            size = f.stat().st_size
            if size > 1024 * 1024:
                label = f"{size // (1024*1024)} MB"
            elif size > 1024:
                label = f"{size // 1024} KB"
            else:
                label = f"{size} B"
            print(f"  {rel}  ({label})")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="Build MarkItDown CLI with bundled Tesseract OCR"
    )
    parser.add_argument(
        "--onefile", action="store_true",
        help="Build single-file executable (default; spec is already onefile)",
    )
    parser.add_argument(
        "--skip-tesseract", action="store_true",
        help="Skip downloading/bundling Tesseract",
    )
    parser.add_argument(
        "--skip-deps", action="store_true",
        help="Skip pip install step (assumes deps already installed)",
    )
    parser.add_argument(
        "--skip-overrides", action="store_true",
        help="Skip applying overrides/ directory",
    )
    parser.add_argument(
        "--skip-exiftool", action="store_true",
        help="Skip downloading/bundling ExifTool",
    )
    args = parser.parse_args()

    print("=" * 60)
    print(f"  MarkItDown CLI Builder")
    print(f"  Platform: {SYSTEM} ({ARCH})")
    print(f"  Source:   {SUBMODULE_DIR}")
    print("=" * 60)

    applied_overrides = False

    try:
        # Step 1: apply our patches on top of submodule
        if not args.skip_overrides:
            apply_overrides()
            applied_overrides = True
        else:
            info("Skipping overrides (--skip-overrides)")

        # Step 2: install deps + run PyInstaller
        if not args.skip_deps:
            build_markitdown(args.onefile)
        else:
            result = _run_pyinstaller()
            if result.returncode != 0:
                log_path = REPO_ROOT / "build" / "pyinstaller.log"
                if log_path.is_file():
                    tail = log_path.read_text(encoding="utf-8", errors="replace")[-4000:]
                    print(tail, file=sys.stderr)
                result.check_returncode()
            _verify_pyinstaller_output()

        # Post-build: ensure encodings is in base_library.zip (python-build-standalone
        # may produce an incomplete zip, causing PYI-7634 at startup)
        print()
        ensure_encodings_in_zip()

        # Step 3: bundle Tesseract into dist/markitdown/ (alongside _internal/)
        if not args.skip_tesseract:
            # Ensure dist/markitdown is a directory (not a stale file from a failed build)
            markitdown_dir = DIST_DIR / "markitdown"
            if markitdown_dir.exists() and not markitdown_dir.is_dir():
                markitdown_dir.unlink()
            tesseract_dir = DIST_DIR / "markitdown" / "tesseract"
            tesseract_dir.mkdir(parents=True, exist_ok=True)

            print()
            if SYSTEM == "Windows":
                setup_tesseract_windows(tesseract_dir)
            elif SYSTEM == "Linux":
                setup_tesseract_linux(tesseract_dir)
            elif SYSTEM == "Darwin":
                setup_tesseract_macos(tesseract_dir)
            else:
                warn(f"Unsupported platform: {SYSTEM}. Tesseract not bundled.")

            print()
            download_tessdata(tesseract_dir / "tessdata")

        # Step 4: bundle ExifTool into dist/markitdown/
        if not args.skip_exiftool:
            exiftool_dir = DIST_DIR / "markitdown" / "exiftool"
            exiftool_dir.mkdir(parents=True, exist_ok=True)

            print()
            if SYSTEM == "Windows":
                setup_exiftool_windows(exiftool_dir)
            elif SYSTEM == "Linux":
                setup_exiftool_linux(exiftool_dir)
            elif SYSTEM == "Darwin":
                setup_exiftool_macos(exiftool_dir)
            else:
                warn(f"Unsupported platform: {SYSTEM}. ExifTool not bundled.")

        # Step 5: copy helper scripts alongside the executable
        for helper in ("render_page.py", "probe_uno.py"):
            src = REPO_ROOT / "scripts" / helper
            if src.exists():
                shutil.copy2(str(src), str(DIST_DIR / "markitdown" / helper))
                ok(f"{helper} copied")

        # Step 6: flatten — move everything from dist/markitdown/ up to dist/
        app_dir = DIST_DIR / "markitdown"
        if app_dir.is_dir():
            for item in app_dir.iterdir():
                target = DIST_DIR / item.name
                if target.exists():
                    if target.is_dir():
                        shutil.rmtree(target, ignore_errors=True)
                    else:
                        target.unlink()
                shutil.move(str(item), str(DIST_DIR))
            shutil.rmtree(str(app_dir), ignore_errors=True)

            # Rename _markitdown_boot → markitdown (or .exe on Windows).
            # Do NOT set PYTHONPATH to base_library.zip — that makes Python treat
            # the zip as a filesystem directory (NotADirectoryError / missing encodings).
            boot = DIST_DIR / ("_markitdown_boot.exe" if SYSTEM == "Windows" else "_markitdown_boot")
            final = DIST_DIR / ("markitdown.exe" if SYSTEM == "Windows" else "markitdown")
            if boot.exists():
                if SYSTEM == "Darwin" and shutil.which("install_name_tool"):
                    subprocess.run(
                        ["install_name_tool", "-add_rpath", "@executable_path/_internal", str(boot)],
                        check=False,
                        capture_output=True,
                    )
                elif SYSTEM == "Linux" and shutil.which("patchelf"):
                    subprocess.run(
                        ["patchelf", "--set-rpath", "$ORIGIN/_internal", str(boot)],
                        check=False,
                        capture_output=True,
                    )
                shutil.move(str(boot), str(final))
                ok("Renamed _markitdown_boot -> markitdown")
            ok("Flattened dist/markitdown/ -> dist/")

        print_output_tree()

        print()
        print("=" * 60)
        print("  Build complete!")
        print("=" * 60)
        exe = "markitdown.exe" if SYSTEM == "Windows" else "./markitdown"
        print(f"  {DIST_DIR / exe} --use-ocr --tesseract-lang eng+chi_sim file.pdf")
        print()

    finally:
        if applied_overrides:
            restore_submodule()


if __name__ == "__main__":
    main()
