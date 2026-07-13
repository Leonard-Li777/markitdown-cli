# -*- mode: python ; coding: utf-8 -*-
#
# PyInstaller spec for MarkItDown onedir CLI.
# Produces dist/markitdown/ directory (self-contained, no runtime deps).
# Tesseract is NOT embedded; place alongside the executable.
#
# Build:
#   python scripts/build.py
#
# Packages are installed from git submodule (markitdown/packages/):
#   pip install -e markitdown/packages/markitdown
#   pip install -e markitdown/packages/markitdown-ocr

import os
import sys
from pathlib import Path

_MAGIKA_DIR = None
try:
    import magika
    _MAGIKA_DIR = os.path.dirname(magika.__file__)
except ImportError:
    pass

block_cipher = None

datas = []

# Include certifi CA bundle for HTTPS support
try:
    import certifi as _certifi
    _cacert = _certifi.where()
    if os.path.isfile(_cacert):
        datas.append((_cacert, "certifi"))
except ImportError:
    pass

# Include magika model files (must be at magika/models/... relative to _MEIPASS)
if _MAGIKA_DIR:
    for root, dirs, files in os.walk(_MAGIKA_DIR):
        for f in files:
            src = os.path.join(root, f)
            rel = os.path.relpath(os.path.dirname(src), os.path.dirname(_MAGIKA_DIR))
            datas.append((src, rel))

# Explicitly collect libpython shared library to avoid PYI-2973 (Linux/macOS)
import sysconfig, glob as _glob

def _find_libpython():
    """Find the libpython shared library across platforms."""
    candidates = []

    # Primary: LDLIBRARY (Linux .so, macOS framework name)
    ldlib = sysconfig.get_config_var("LDLIBRARY")
    libdir = sysconfig.get_config_var("LIBDIR")
    if libdir and ldlib:
        candidates.append(os.path.join(libdir, ldlib))

    # Alternate: INSTSONAME (e.g. libpython3.13.so.1.0 or Python)
    instsoname = sysconfig.get_config_var("INSTSONAME")
    if libdir and instsoname:
        candidates.append(os.path.join(libdir, instsoname))

    # macOS framework: look for .dylib alongside the framework
    base = sysconfig.get_config_var("base") or sysconfig.get_config_var("installed_base")
    if base:
        for pattern in ("lib/libpython*.dylib", "lib/libpython*.so*"):
            candidates.extend(_glob.glob(os.path.join(base, pattern)))

    for p in candidates:
        if os.path.isfile(p):
            return os.path.realpath(p)
    return None

_binaries = []
_libpython = _find_libpython()
if _libpython:
    _binaries.append((_libpython, "."))

# On Linux, lxml needs libxml2 and libxslt bundled (they're dynamically linked)
if sys.platform == "linux":
    _lib_dir = sysconfig.get_config_var("LIBDIR") or ""
    for _soname in ("libxml2.so.*", "libxslt.so.*"):
        for _p in _glob.glob(os.path.join(_lib_dir, _soname)):
            if os.path.isfile(_p):
                _binaries.append((os.path.realpath(_p), "."))
                break

a = Analysis(
    ['scripts/markitdown_cli_wrapper.py'],
    pathex=[],
    binaries=_binaries,
    datas=datas,
    hiddenimports=[
        # Core markitdown
        'markitdown',
        'markitdown.__main__',
        'markitdown.__about__',
        'markitdown._markitdown',
        'markitdown._base_converter',
        'markitdown._extractor',
        'markitdown._server',
        'markitdown._router',
        'markitdown._page_range',
        'markitdown._libreoffice_detect',
        'markitdown._thumbnail',
        'markitdown._html_output',
        'markitdown._pdf_output',
        'markitdown._stream_info',
        'markitdown._exceptions',
        'markitdown._uri_utils',
        'markitdown.converters',
        'markitdown.converters._plain_text_converter',
        'markitdown.converters._html_converter',
        'markitdown.converters._markdownify',
        'markitdown.converters._pdf_converter',
        'markitdown.converters._docx_converter',
        'markitdown.converters._pptx_converter',
        'markitdown.converters._xlsx_converter',
        'markitdown.converters._csv_converter',
        'markitdown.converters._image_converter',
        'markitdown.converters._audio_converter',
        'markitdown.converters._epub_converter',
        'markitdown.converters._zip_converter',
        'markitdown.converters._youtube_converter',
        'markitdown.converters._wikipedia_converter',
        'markitdown.converters._rss_converter',
        'markitdown.converters._ipynb_converter',
        'markitdown.converters._bing_serp_converter',
        'markitdown.converters._outlook_msg_converter',
        'markitdown.converters._doc_intel_converter',
        'markitdown.converters._cu_converter',
        'markitdown.converters._llm_caption',
        'markitdown.converters._exiftool',
        'markitdown.converters._transcribe_audio',
        'markitdown.converter_utils',
        'markitdown.converter_utils.docx',
        'markitdown.converter_utils.docx.pre_process',
        'markitdown.converter_utils.docx.math',
        'markitdown.converter_utils.docx.math.omml',
        'markitdown.converter_utils.docx.math.latex_dict',

        # OCR plugin
        'markitdown_ocr',
        'markitdown_ocr._plugin',
        'markitdown_ocr._ocr_service',
        'markitdown_ocr._tesseract_service',
        'markitdown_ocr._pdf_converter_with_ocr',
        'markitdown_ocr._docx_converter_with_ocr',
        'markitdown_ocr._pptx_converter_with_ocr',
        'markitdown_ocr._xlsx_converter_with_ocr',

        # OCR dependencies
        'pytesseract',
        'PIL',
        'PIL._imaging',
        'PIL.Image',

        # PDF
        'pdfminer',
        'pdfminer.high_level',
        'pdfplumber',
        'fitz',
        'fitz._fitz',

        # DOCX
        'mammoth',
        'docx',
        'lxml',
        'lxml._elementpath',
        'lxml.etree',

        # PPTX
        'pptx',

        # XLSX
        'pandas',
        'openpyxl',

        # General
        'bs4',
        'markdownify',
        'magika',
        'charset_normalizer',
        'charset_normalizer._charset_normalizer',
        'certifi',
        'defusedxml',
        'markdown',
        'markdown.extensions',
        'markdown.extensions.fenced_code',
        'markdown.extensions.tables',
        'markdown.extensions.codehilite',
        'markdown.extensions.sane_lists',
        'requests',
        'urllib3',
        'urllib3.util.retry',
        'olefile',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'tkinter', 'PyQt5', 'PyQt6', 'PySide2', 'PySide6',
        'matplotlib', 'scipy', 'notebook', 'jupyter',
        'azure', 'openai', 'pydub', 'speechrecognition',
        'speech_recognition', 'pocketsphinx', 'sphinxbase',
        'setuptools._distutils', 'youtube_transcript_api',
        'pytest', 'unittest', 'test', 'nose',
        'cv2', 'torch', 'tensorflow',
        'PIL._imagingtk', 'PIL.ImageTk', 'PIL.ImageGrab',
        'pandas.io.clipboard', 'pandas.io.sql',
        'numpy.distutils', 'numpy.testing',
        'h2', 'hpack', 'hyperframe', 'priority',
    ],
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data)

# EXE must have a name that doesn't collide with COLLECT on macOS/Linux
# (on Windows, .exe suffix avoids the collision automatically)
exe = EXE(
    pyz,
    a.scripts,
    [],
    name='_markitdown_boot',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    name='markitdown',
)
