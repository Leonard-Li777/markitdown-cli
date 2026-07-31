# -*- mode: python ; coding: utf-8 -*-
#
# PyInstaller spec for MarkItDown onefile CLI.
# Produces a single dist/markitdown.exe (self-contained, no runtime deps).
# Tesseract, ExifTool, and helper scripts are bundled inside the exe.
#
# Build:
#   python scripts/build.py
#
# Packages are installed from git submodule (markitdown/packages/):
#   pip install -e markitdown/packages/markitdown
#   pip install -e markitdown/packages/markitdown-ocr

import os
import sys
import sysconfig
import importlib.util
from pathlib import Path

# On python-build-standalone (astral-sh), the stdlib is at a non-standard path.
# PyInstaller's Analysis will NOT find it during the module scan unless we add
# it to pathex.  This is critical for base_library.zip: core modules like
# encodings must be collected into the zip, otherwise Py_InitializeFromConfig
# fails with "Failed to import encodings module".
_stdlib = sysconfig.get_path("stdlib") or ""
_platstdlib = sysconfig.get_path("platstdlib") or ""

block_cipher = None

datas = []


def _mark(label):
    """Print a timestamped marker to stderr for diagnosing spec hangs."""
    print(f"[spec] {label}", file=sys.stderr, flush=True)


def _collect_package_as_datas(package_name):
    """Collect a package tree as raw datas (pure file copy, NO binary analysis).

    Uses importlib.util.find_spec() and import fallback to locate the package.
    The collected files land at the correct relative path in the bundle so that
    Python's import system finds them at runtime.
    """
    collected = []
    try:
        pkg_dir = None
        spec = importlib.util.find_spec(package_name)
        if spec:
            if spec.submodule_search_locations:
                pkg_dir = list(spec.submodule_search_locations)[0]
            elif spec.origin:
                pkg_dir = os.path.dirname(spec.origin)
        
        if not pkg_dir or not os.path.isdir(pkg_dir):
            try:
                mod = importlib.import_module(package_name)
                if hasattr(mod, "__path__") and mod.__path__:
                    pkg_dir = mod.__path__[0]
                elif hasattr(mod, "__file__") and mod.__file__:
                    pkg_dir = os.path.dirname(mod.__file__)
            except Exception:
                pass

        if pkg_dir and os.path.isdir(pkg_dir):
            parent_dir = os.path.dirname(pkg_dir)
            for root, _dirs, files in os.walk(pkg_dir):
                for f in files:
                    src = os.path.join(root, f)
                    rel = os.path.relpath(os.path.dirname(src), parent_dir)
                    collected.append((src, rel))
    except Exception as e:
        print(
            f"[ERROR] Could not locate package '{package_name}' for manual "
            f"collection: {e}",
            file=sys.stderr,
        )
        raise RuntimeError(f"Missing required build dependency: {package_name}")
    if not collected:
        raise RuntimeError(f"Missing or empty required build dependency: {package_name}")
    return collected

_mark("start")

# Include certifi CA bundle for HTTPS support
try:
    import certifi as _certifi
    _cacert = _certifi.where()
    if os.path.isfile(_cacert):
        datas.append((_cacert, "certifi"))
except ImportError:
    pass
_mark("certifi done")

# Ensure encodings package is bundled as a directory (not just in base_library.zip)
# python-build-standalone on CI may produce an incomplete base_library.zip, causing
# PYI-30193 "Failed to import encodings module" during Py_InitializeFromConfig.
try:
    import encodings as _encodings_mod
    _encodings_dir = os.path.dirname(_encodings_mod.__file__)
    if _encodings_dir and os.path.isdir(_encodings_dir):
        for root, dirs, files in os.walk(_encodings_dir):
            for f in files:
                src = os.path.join(root, f)
                rel = os.path.relpath(os.path.dirname(src), os.path.dirname(_encodings_dir))
                datas.append((src, rel))
except ImportError:
    pass
_mark("encodings done")

# Collect onnxruntime as raw files via datas (NOT binaries).
# PyInstaller's binary/shared-library analysis of onnxruntime hangs on Linux.
# By keeping it in excludes and manually copying via datas, we bypass the
# static scan entirely while still making the package available at runtime.
_mark("collecting magika")
datas.extend(_collect_package_as_datas("magika"))
_mark("collecting onnxruntime")
datas.extend(_collect_package_as_datas("onnxruntime"))
_mark("collecting cv2")
datas.extend(_collect_package_as_datas("cv2"))
_mark("datas collection done")

# Bundle tesseract, exiftool, and helper scripts into the exe.
# Build script downloads these BEFORE PyInstaller runs.
# Onefile: tools are at dist/tesseract/ (no subdirectory created by PyInstaller)
# Onedir:  tools are at dist/markitdown/tesseract/ (inside COLLECT dir)
_repo = Path(SPECPATH) if 'SPECPATH' in dir() else Path.cwd()

for _name in ("exiftool",):
    _candidates = [
        _repo / "dist" / _name,                # onefile
        _repo / "dist" / "markitdown" / _name, # onedir
        _repo / _name,                         # repo root models dir
    ]
    for _dir in _candidates:
        if _dir.is_dir():
            datas.append((str(_dir), _name))
            _mark(f"bundled {_name} from {_dir}")
            break

for _script in ("render_page.py", "probe_uno.py"):
    _path = _repo / "scripts" / _script
    if _path.is_file():
        datas.append((str(_path), "."))
        _mark(f"bundled {_script}")

# Explicitly collect libpython shared library to avoid PYI-2973 (Linux/macOS)
import sysconfig, glob as _glob, subprocess, ctypes.util
_mark("stdlib imports done")

def _collect_sysconfig_candidates(candidates):
    libdir = sysconfig.get_config_var("LIBDIR")
    base = sysconfig.get_config_var("base") or sysconfig.get_config_var("installed_base") or ""

    ldlib = sysconfig.get_config_var("LDLIBRARY")
    if libdir and ldlib:
        candidates.append(os.path.join(libdir, ldlib))

    instsoname = sysconfig.get_config_var("INSTSONAME")
    if libdir and instsoname:
        candidates.append(os.path.join(libdir, instsoname))

    search_dirs = []
    for d in (
        libdir,
        os.path.join(base, "lib"),
        os.path.join(base, "lib64"),
        sysconfig.get_path("data"),
        os.path.join(sys.prefix, "lib"),
        os.path.join(sys.exec_prefix, "lib"),
    ):
        if d and d not in search_dirs and os.path.isdir(d):
            search_dirs.append(d)

    for search_dir in search_dirs:
        for pattern in ("libpython*.dylib", "libpython*.so*", "Python"):
            candidates.extend(_glob.glob(os.path.join(search_dir, pattern)))


def _collect_ldconfig_candidates(candidates):
    """Resolve the soname via ldconfig -p."""
    try:
        soname = ctypes.util.find_library(
            f"python{sys.version_info.major}.{sys.version_info.minor}"
        )
        if not soname:
            return
        result = subprocess.run(
            ["ldconfig", "-p"], capture_output=True, text=True, timeout=10
        )
        for line in result.stdout.splitlines():
            if soname in line and "=>" in line:
                path = line.split("=>")[-1].strip()
                if path and path not in candidates:
                    candidates.append(path)
    except Exception:
        pass


def _collect_common_search_candidates(candidates):
    """Recursive fallback in standard system library directories."""
    soname = ctypes.util.find_library(
        f"python{sys.version_info.major}.{sys.version_info.minor}"
    )
    if not soname:
        return
    for root in ("/usr/lib", "/usr/lib64", "/usr/local/lib", "/lib", "/lib64"):
        if os.path.isdir(root):
            for p in _glob.glob(os.path.join(root, "**", soname), recursive=True):
                if p not in candidates:
                    candidates.append(p)


def _find_libpython():
    """Find the libpython shared library across platforms."""
    candidates = []

    _mark("sysconfig search")
    _collect_sysconfig_candidates(candidates)
    _mark("ldconfig search")
    _collect_ldconfig_candidates(candidates)
    _mark("common search")
    # NOTE: _collect_common_search_candidates is INTENTIONALLY SKIPPED.
    # It does recursive glob.glob("/usr/lib/**/libpython*.so*") which hangs
    # indefinitely in CI container environments (GitHub Actions, Docker)
    # due to filesystem traversal overhead. On python-build-standalone the
    # library is NOT in standard system paths anyway; sysconfig + ldconfig
    # searches above are sufficient, and PyInstaller 6.x auto-detection
    # handles the rest.

    # Direct ctypes.util.find_library result — this returns a full path on macOS,
    # while the above functions may miss it (ldconfig doesn't exist on macOS,
    # and _collect_common_search_candidates globs with the full path incorrectly).
    try:
        soname = ctypes.util.find_library(
            f"python{sys.version_info.major}.{sys.version_info.minor}"
        )
        if soname and soname not in candidates:
            candidates.append(soname)
    except Exception:
        pass

    # macOS framework fallback: the shared lib is named "Python3" or "Python"
    # at the root of the framework's version directory, not under lib/.
    if sys.platform == "darwin":
        for d in (sys.prefix, sys.exec_prefix):
            if d and os.path.isdir(d):
                for name in ("Python3", "Python"):
                    p = os.path.join(d, name)
                    if os.path.isfile(p) and p not in candidates:
                        candidates.append(p)

    for p in candidates:
        if os.path.isfile(p):
            # Return the path as-is (do NOT resolve realpath) — PyInstaller needs the
            # basename to match the soname the bootloader looks for. On macOS framework
            # builds, libpython*.dylib is often a symlink to Python3/Python; resolving
            # the symlink would cause PYI-5875 at runtime because the bootloader looks
            # for libpython3.x.dylib, not Python3.
            return p

    print(
        "[WARN] libpython shared library not found — PYI-30798 may occur at runtime",
        file=sys.stderr,
    )
    return None

_binaries = []

# NOTE (#1): On macOS, the Python shared library is ALREADY collected by
# PyInstaller 6.x's Analysis.assemble() with correct framework bundle
# structure. DO NOT add it manually here — it creates conflicting TOC
# entries causing PYI-7989/PYI-5875 at runtime.
#
# NOTE (#2): On Linux (especially astral-sh/python-build-standalone used
# by setup-python on CI), PyInstaller's auto-detection may fail. Add it
# manually via _find_libpython() which has more search fallbacks.
if sys.platform == "linux":
    _mark("calling find_libpython")
    _libpython = _find_libpython()
    _mark(f"find_libpython returned: {_libpython}")
    if _libpython:
        _binaries.append((_libpython, "."))

# On Linux, lxml needs libxml2 and libxslt bundled (they're dynamically linked)
if sys.platform == "linux":
    _mark("globbing libxml2/libxslt")
    _lib_dir = sysconfig.get_config_var("LIBDIR") or ""
    for _soname in ("libxml2.so.*", "libxslt.so.*"):
        for _p in _glob.glob(os.path.join(_lib_dir, _soname)):
            if os.path.isfile(_p):
                _binaries.append((os.path.realpath(_p), "."))
                break
    _mark("libxml2/libxslt done")

_mark("entering Analysis")
a = Analysis(
    ['scripts/markitdown_cli_wrapper.py'],
    pathex=[p for p in (_stdlib, _platstdlib) if p],
    binaries=_binaries,
    datas=datas,
    hiddenimports=[
        # Stdlib — must be in base_library.zip for Py_InitializeFromConfig
        'encodings',
        'encodings.utf_8',
        'encodings.aliases',
        'encodings.idna',
        'encodings.mbcs',
        'encodings.ascii',
        'encodings.latin_1',
        'codecs',

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
        'markitdown_ocr._onnx_ocr_service',
        'markitdown_ocr._tesseract_service',
        'markitdown_ocr._pdf_converter_with_ocr',
        'markitdown_ocr._docx_converter_with_ocr',
        'markitdown_ocr._pptx_converter_with_ocr',
        'markitdown_ocr._xlsx_converter_with_ocr',
        'markitdown_ocr._image_converter_with_ocr',

        # OCR dependencies
        'pytesseract',
        'cv2',
        'onnxruntime',
        'numpy',
        'PIL',
        'PIL._imaging',
        'PIL.Image',

        # Magika (file type detection)
        'magika',
        'magika.magika',
        'magika.types',
        'magika.types.content_type_info',
        'magika.types.content_type_label',
        'magika.types.magika_prediction',
        'magika.types.magika_result',
        'magika.types.model',
        'magika.types.prediction_mode',
        'magika.types.status',
        'magika.types.strenum',
        'magika.logger',

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
        'torch', 'tensorflow',
        'onnxruntime',
        'PIL._imagingtk', 'PIL.ImageTk', 'PIL.ImageGrab',
        'pandas.io.clipboard', 'pandas.io.sql',
        'numpy.distutils', 'numpy.testing',
        'h2', 'hpack', 'hyperframe', 'priority',
    ],
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    a.zipfiles,
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
