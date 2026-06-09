from typing import Any, Optional
from markitdown import MarkItDown

from ._ocr_service import LLMVisionOCRService
from ._tesseract_service import TesseractOCRService
from ._pdf_converter_with_ocr import PdfConverterWithOCR
from ._docx_converter_with_ocr import DocxConverterWithOCR
from ._pptx_converter_with_ocr import PptxConverterWithOCR
from ._xlsx_converter_with_ocr import XlsxConverterWithOCR


__plugin_interface_version__ = 1


def register_converters(markitdown: MarkItDown, **kwargs: Any) -> None:
    llm_client = kwargs.get("llm_client")
    llm_model = kwargs.get("llm_model")
    llm_prompt = kwargs.get("llm_prompt")

    use_tesseract = kwargs.get("use_tesseract", False)
    tesseract_path = kwargs.get("tesseract_path")
    tesseract_lang = kwargs.get("tesseract_lang", "eng")

    ocr_service: Optional[Any] = None

    if use_tesseract:
        tesseract_service = TesseractOCRService(
            tesseract_path=tesseract_path,
            lang=tesseract_lang,
        )
        if tesseract_service.available:
            ocr_service = tesseract_service
    elif llm_client and llm_model:
        ocr_service = LLMVisionOCRService(
            client=llm_client,
            model=llm_model,
            default_prompt=llm_prompt,
        )

    PRIORITY_OCR_ENHANCED = -1.0

    markitdown.register_converter(
        PdfConverterWithOCR(ocr_service=ocr_service), priority=PRIORITY_OCR_ENHANCED
    )

    markitdown.register_converter(
        DocxConverterWithOCR(ocr_service=ocr_service), priority=PRIORITY_OCR_ENHANCED
    )

    markitdown.register_converter(
        PptxConverterWithOCR(ocr_service=ocr_service), priority=PRIORITY_OCR_ENHANCED
    )

    markitdown.register_converter(
        XlsxConverterWithOCR(ocr_service=ocr_service), priority=PRIORITY_OCR_ENHANCED
    )
