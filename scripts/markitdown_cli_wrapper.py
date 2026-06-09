#!/usr/bin/env python3
"""
Entry-point wrapper for PyInstaller builds.
Handles Tesseract path resolution and plugin registration for frozen builds.
Cross-platform: works on Windows, macOS, and Linux.
"""
import sys
import os
import platform

# --- Tesseract path setup ---
_is_win = platform.system() == "Windows"
_exe_dir = os.path.dirname(os.path.abspath(sys.executable if getattr(sys, 'frozen', False) else __file__))
_tess_exe_name = "tesseract.exe" if _is_win else "tesseract"

_candidates = [
    os.path.join(_exe_dir, _tess_exe_name),
    os.path.join(_exe_dir, "tesseract", _tess_exe_name),
    os.path.join(_exe_dir, "tesseract", "bin", _tess_exe_name),
]

tesseract_exe = None
for c in _candidates:
    if os.path.exists(c):
        tesseract_exe = c
        tess_bin_dir = os.path.dirname(c)
        path_parts = os.environ.get("PATH", "").split(os.pathsep)
        if tess_bin_dir not in path_parts and _is_win:
            os.environ["PATH"] = tess_bin_dir + os.pathsep + os.environ.get("PATH", "")
        break

if tesseract_exe:
    import pytesseract
    pytesseract.pytesseract.tesseract_cmd = tesseract_exe
    # On Linux, also set LD_LIBRARY_PATH for bundled libs
    if not _is_win:
        lib_dir = os.path.join(os.path.dirname(tesseract_exe), "lib")
        ld_key = "LD_LIBRARY_PATH" if platform.system() == "Linux" else "DYLD_LIBRARY_PATH"
        if os.path.isdir(lib_dir):
            existing = os.environ.get(ld_key, "")
            if lib_dir not in existing:
                os.environ[ld_key] = lib_dir + os.pathsep + existing
    # tessdata
    for td in [
        os.path.join(os.path.dirname(tesseract_exe), "tessdata"),
        os.path.join(_exe_dir, "tessdata"),
        os.path.join(_exe_dir, "tesseract", "tessdata"),
        os.path.join(_exe_dir, "tesseract", "share", "tessdata"),
    ]:
        if os.path.isdir(td):
            os.environ["TESSDATA_PREFIX"] = td
            break

# --- Plugin discovery fix for frozen builds ---
if getattr(sys, 'frozen', False):
    import importlib.metadata as md
    _orig_entry_points = md.entry_points

    def _patched_entry_points(*args, **kwargs):
        eps = _orig_entry_points(*args, **kwargs)
        if not eps and kwargs.get('group') == 'markitdown.plugin':
            class FakeEntryPoint:
                name = 'ocr'
                value = 'markitdown_ocr'
                group = 'markitdown.plugin'
                def load(self):
                    import markitdown_ocr
                    return markitdown_ocr
            return (FakeEntryPoint(),)
        return eps

    md.entry_points = _patched_entry_points

from markitdown.__main__ import main
sys.exit(main())
