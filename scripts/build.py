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
import stat
import subprocess
import sys
import urllib.request
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
WINDOWS_EXIFTOOL_URL = f"https://exiftool.org/exiftool-{EXIFTOOL_VERSION}_64.zip"
UNIX_EXIFTOOL_URL = f"https://exiftool.org/Image-ExifTool-{EXIFTOOL_VERSION}.tar.gz"

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
def download_file(url, dest):
    info(f"Downloading {dest.name}...")
    urllib.request.urlretrieve(url, dest)
    size = dest.stat().st_size
    ok(f"{dest.name} ({size // 1024} KB)")


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
def build_markitdown(onefile: bool):
    info("Installing Python build dependencies...")
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "pyinstaller", "pytesseract"],
        check=False, capture_output=True,
    )

    for name, pkg_path in [("markitdown", MARKITDOWN_PKG), ("markitdown-ocr", MARKITDOWN_OCR_PKG)]:
        if not (pkg_path / "pyproject.toml").exists():
            warn(f"Skipping {name} — no pyproject.toml at {pkg_path}")
            continue
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "-e", str(pkg_path)],
            check=False, capture_output=True,
        )
        ok(f"{name} installed from submodule")

    info("Running PyInstaller...")
    cmd = [
        sys.executable, "-m", "PyInstaller",
        str(REPO_ROOT / "markitdown.spec"),
        "--clean", "--noconfirm",
    ]
    subprocess.run(cmd, cwd=str(REPO_ROOT), check=True)

    exe = DIST_DIR / ("markitdown.exe" if SYSTEM == "Windows" else "markitdown")
    if exe.exists():
        ok(f"Executable: {exe} ({exe.stat().st_size // (1024*1024)} MB)")


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
            info("Running PyInstaller (skip deps)...")
            subprocess.run(
                [sys.executable, "-m", "PyInstaller",
                 str(REPO_ROOT / "markitdown.spec"),
                 "--clean", "--noconfirm"],
                cwd=str(REPO_ROOT), check=True,
            )

        # Step 3: bundle Tesseract
        if not args.skip_tesseract:
            tesseract_dir = DIST_DIR / "tesseract"
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

        # Step 4: bundle ExifTool
        if not args.skip_exiftool:
            exiftool_dir = DIST_DIR / "exiftool"
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
