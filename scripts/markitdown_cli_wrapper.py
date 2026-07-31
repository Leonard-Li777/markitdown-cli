#!/usr/bin/env python3
"""
Entry-point wrapper for PyInstaller builds.
Handles Tesseract path resolution and plugin registration for frozen builds.
Cross-platform: works on Windows, macOS, and Linux.
"""
import sys
import os
import platform

# --- Base directories ---
_is_win = platform.system() == "Windows"
_is_frozen = getattr(sys, 'frozen', False)
_exe_dir = os.path.dirname(os.path.abspath(sys.executable if _is_frozen else __file__))
# In onefile mode, bundled files are extracted to sys._MEIPASS (temp dir).
# In onedir mode, _MEIPASS == exe_dir. Always check _MEIPASS first.
_meipass = getattr(sys, '_MEIPASS', _exe_dir) if _is_frozen else _exe_dir

# --- Tesseract path setup ---
_tess_exe_name = "tesseract.exe" if _is_win else "tesseract"

_candidates = [
    os.path.join(_meipass, "tesseract", _tess_exe_name),
    os.path.join(_meipass, "tesseract", "bin", _tess_exe_name),
    os.path.join(_exe_dir, _tess_exe_name),
    os.path.join(_exe_dir, "tesseract", _tess_exe_name),
    os.path.join(_exe_dir, "tesseract", "bin", _tess_exe_name),
]

tesseract_exe = None
for c in _candidates:
    if os.path.isfile(c):  # isfile prevents dist/tesseract/ directory from matching on Linux
        tesseract_exe = c
        tess_bin_dir = os.path.dirname(c)
        path_parts = os.environ.get("PATH", "").split(os.pathsep)
        if tess_bin_dir not in path_parts and _is_win:
            os.environ["PATH"] = tess_bin_dir + os.pathsep + os.environ.get("PATH", "")
        break

if tesseract_exe:
    import pytesseract
    pytesseract.pytesseract.tesseract_cmd = tesseract_exe
    # On Linux/macOS, set LD_LIBRARY_PATH / DYLD_LIBRARY_PATH for bundled libs
    # macOS dylibbundler layout: tesseract/bin/tesseract + tesseract/lib/*.dylib
    if not _is_win:
        _tess_bin_dir = os.path.dirname(tesseract_exe)
        ld_key = "LD_LIBRARY_PATH" if platform.system() == "Linux" else "DYLD_LIBRARY_PATH"
        # Check lib/ next to the binary AND lib/ one level up (for bin/ layout)
        for _lib_candidate in [
            os.path.join(_tess_bin_dir, "lib"),
            os.path.join(os.path.dirname(_tess_bin_dir), "lib"),
        ]:
            if os.path.isdir(_lib_candidate):
                existing = os.environ.get(ld_key, "")
                if _lib_candidate not in existing:
                    os.environ[ld_key] = _lib_candidate + os.pathsep + existing
                break
    # tessdata — check extraction dir first, then exe dir
    _tess_dir = os.path.dirname(tesseract_exe)
    for td in [
        os.path.join(_tess_dir, "tessdata"),
        os.path.join(_meipass, "tessdata"),
        os.path.join(_meipass, "tesseract", "tessdata"),
        os.path.join(_meipass, "tesseract", "share", "tessdata"),
        os.path.join(_exe_dir, "tessdata"),
        os.path.join(_exe_dir, "tesseract", "tessdata"),
        os.path.join(_exe_dir, "tesseract", "share", "tessdata"),
    ]:
        if os.path.isdir(td):
            os.environ["TESSDATA_PREFIX"] = td
            break

# --- ExifTool path setup ---
_exiftool_exe_name = "exiftool.exe" if _is_win else "exiftool"
_exiftool_candidates = [
    os.path.join(_meipass, "exiftool", _exiftool_exe_name),
    os.path.join(_exe_dir, _exiftool_exe_name),
    os.path.join(_exe_dir, "exiftool", _exiftool_exe_name),
]
for c in _exiftool_candidates:
    if os.path.exists(c):
        os.environ["EXIFTOOL_PATH"] = c
        break

# --- ONNX PP-OCR models setup ---
for m_dir in [
    os.path.join(_meipass, "models"),
    os.path.join(_exe_dir, "models"),
    os.path.join(_meipass),
    os.path.join(_exe_dir),
]:
    if os.path.isdir(m_dir) and any(f.endswith(".onnx") for f in os.listdir(m_dir)):
        os.environ["PPOCR_MODELS_DIR"] = m_dir
        break

# --- Plugin discovery fix for frozen builds ---
if _is_frozen:
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
