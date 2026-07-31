from typing import BinaryIO, Any, Optional
from markitdown import DocumentConverterResult, StreamInfo
from markitdown.converters._image_converter import ImageConverter


class ImageConverterWithOCR(ImageConverter):
    """
    Image Converter with OCR support for standalone image files (PNG, JPG, WEBP, etc.).
    """

    def __init__(self, ocr_service: Optional[Any] = None):
        super().__init__()
        self._ocr_service = ocr_service

    def convert(self, file_stream: BinaryIO, stream_info: StreamInfo, **kwargs: Any) -> DocumentConverterResult:
        result = super().convert(file_stream, stream_info, **kwargs)
        
        md_content = result.markdown or ""

        if self._ocr_service:
            cur_pos = file_stream.tell()
            try:
                file_stream.seek(0)
                ocr_res = self._ocr_service.extract_text(file_stream, stream_info=stream_info)
                if ocr_res and ocr_res.text:
                    if md_content.strip():
                        md_content = md_content.strip() + "\n\n" + ocr_res.text.strip()
                    else:
                        md_content = ocr_res.text.strip()
            except Exception:
                pass
            finally:
                file_stream.seek(cur_pos)

        return DocumentConverterResult(
            markdown=md_content,
            title=getattr(result, "title", None),
        )
