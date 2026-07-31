from typing import Any, Optional
from markitdown import MarkItDown

from ._ocr_service import LLMVisionOCRService
from ._onnx_ocr_service import ONNXPPOCRService
from ._tesseract_service import TesseractOCRService
from ._pdf_converter_with_ocr import PdfConverterWithOCR
from ._docx_converter_with_ocr import DocxConverterWithOCR
from ._pptx_converter_with_ocr import PptxConverterWithOCR
from ._xlsx_converter_with_ocr import XlsxConverterWithOCR
from ._image_converter_with_ocr import ImageConverterWithOCR


__plugin_interface_version__ = 1


def register_converters(markitdown: MarkItDown, **kwargs: Any) -> None:
    llm_client = kwargs.get("llm_client")
    llm_model = kwargs.get("llm_model")
    llm_prompt = kwargs.get("llm_prompt")

    use_ocr = kwargs.get("use_ocr", False)
    use_tesseract = kwargs.get("use_tesseract", False)
    ocr_engine = kwargs.get("ocr_engine", "paddleocr")
    ocr_model_size = kwargs.get("ocr_model_size")
    
    det_model_path = kwargs.get("det_model_path")
    rec_model_path = kwargs.get("rec_model_path")
    keys_path = kwargs.get("keys_path")

    ocr_service: Optional[Any] = None

    if use_ocr or use_tesseract:
        if ocr_engine == "llm" and llm_client and llm_model:
            ocr_service = LLMVisionOCRService(
                client=llm_client,
                model=llm_model,
                default_prompt=llm_prompt,
            )
        elif ocr_engine == "tesseract":
            tess_service = TesseractOCRService(
                tesseract_path=kwargs.get("tesseract_path"),
                lang=kwargs.get("tesseract_lang", "eng"),
            )
            if tess_service.available:
                ocr_service = tess_service
        else:
            # Default or "paddleocr": PP-OCR ONNX service
            onnx_service = ONNXPPOCRService(
                det_model_path=det_model_path,
                rec_model_path=rec_model_path,
                keys_path=keys_path,
                model_size=ocr_model_size,
            )
            if onnx_service.available:
                ocr_service = onnx_service
            else:
                # Fallback to Tesseract if paddleocr unavailable
                tess_service = TesseractOCRService(
                    tesseract_path=kwargs.get("tesseract_path"),
                    lang=kwargs.get("tesseract_lang", "eng"),
                )
                if tess_service.available:
                    ocr_service = tess_service
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

    markitdown.register_converter(
        ImageConverterWithOCR(ocr_service=ocr_service), priority=PRIORITY_OCR_ENHANCED
    )
