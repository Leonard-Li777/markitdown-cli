import os
import sys
from typing import BinaryIO, Optional
from ._ocr_service import OCRResult


class TesseractOCRService:
    def __init__(
        self,
        tesseract_path: Optional[str] = None,
        lang: str = "eng",
        psm: int = 3,
        oem: int = 3,
    ):
        self.lang = lang
        self.psm = psm
        self.oem = oem

        try:
            import pytesseract

            if tesseract_path:
                pytesseract.pytesseract.tesseract_cmd = tesseract_path
            elif os.environ.get("TESSERACT_PATH"):
                pytesseract.pytesseract.tesseract_cmd = os.environ["TESSERACT_PATH"]
            elif sys.platform == "win32":
                exe_dir = os.path.dirname(sys.executable)
                # onedir: exe at dist/markitdown/markitdown.exe → tesseract at dist/tesseract/
                parent_dir = os.path.dirname(exe_dir) if os.path.basename(exe_dir) in ("_internal", "markitdown") else exe_dir
                candidates = [
                    os.path.join(exe_dir, "tesseract", "tesseract.exe"),
                    os.path.join(exe_dir, "tesseract.exe"),
                    os.path.join(parent_dir, "tesseract", "tesseract.exe"),
                    os.path.join(parent_dir, "tesseract.exe"),
                    r"C:\Program Files\Tesseract-OCR\tesseract.exe",
                    r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
                ]
                for c in candidates:
                    if os.path.exists(c):
                        pytesseract.pytesseract.tesseract_cmd = c
                        break

            self._pytesseract = pytesseract

            self._tesseract_cmd = pytesseract.pytesseract.tesseract_cmd
            self._available = os.path.isfile(self._tesseract_cmd)

            if self._available:
                tessdata_env = os.environ.get("TESSDATA_PREFIX")
                if not tessdata_env:
                    tessdata_dir = os.path.join(
                        os.path.dirname(self._tesseract_cmd), "tessdata"
                    )
                    if os.path.isdir(tessdata_dir):
                        os.environ["TESSDATA_PREFIX"] = tessdata_dir
        except ImportError:
            self._available = False

    @property
    def available(self) -> bool:
        return self._available

    def extract_text(
        self,
        image_stream: BinaryIO,
        prompt: Optional[str] = None,
        **kwargs,
    ) -> OCRResult:
        if not self._available:
            return OCRResult(
                text="",
                backend_used="tesseract",
                error="pytesseract is not installed",
            )

        try:
            from PIL import Image

            image_stream.seek(0)
            pil_image = Image.open(image_stream)

            if pil_image.mode not in ("RGB", "L"):
                pil_image = pil_image.convert("RGB")

            custom_config = f"--psm {self.psm} --oem {self.oem}"
            text = self._pytesseract.image_to_string(
                pil_image, lang=self.lang, config=custom_config
            )

            return OCRResult(
                text=text.strip(),
                backend_used="tesseract",
            )
        except Exception as e:
            return OCRResult(
                text="", backend_used="tesseract", error=str(e)
            )
        finally:
            image_stream.seek(0)
