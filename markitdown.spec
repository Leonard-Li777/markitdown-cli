# -*- mode: python ; coding: utf-8 -*-
#
# PyInstaller spec for MarkItDown single-file CLI.
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

# Include magika model files (must be at magika/models/... relative to _MEIPASS)
if _MAGIKA_DIR:
    for root, dirs, files in os.walk(_MAGIKA_DIR):
        for f in files:
            src = os.path.join(root, f)
            rel = os.path.relpath(os.path.dirname(src), os.path.dirname(_MAGIKA_DIR))
            datas.append((src, rel))

a = Analysis(
    ['scripts/markitdown_cli_wrapper.py'],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=[
        # Core markitdown
        'markitdown',
        'markitdown.__main__',
        'markitdown.__about__',
        'markitdown._markitdown',
        'markitdown._base_converter',
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
        'defusedxml',
        'markdown',
        'markdown.extensions',
        'markdown.extensions.fenced_code',
        'markdown.extensions.tables',
        'markdown.extensions.codehilite',
        'markdown.extensions.sane_lists',
        'requests',
        'olefile',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'tkinter', 'PyQt5', 'PyQt6', 'PySide2', 'PySide6',
        'matplotlib', 'scipy', 'notebook', 'jupyter',
        'azure', 'openai', 'pydub', 'speechrecognition',
        'setuptools._distutils', 'youtube_transcript_api',
        'pytest', 'unittest', 'test', 'nose',
        'cv2', 'torch', 'tensorflow',
    ],
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='markitdown',
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
