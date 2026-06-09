from typing import Any, BinaryIO, Optional
from ._stream_info import StreamInfo


class DocumentConverterResult:
    def __init__(
        self,
        markdown: str,
        *,
        title: Optional[str] = None,
        metadata: Optional[dict] = None,
    ):
        self.markdown = markdown
        self.title = title
        self.metadata = metadata or {}

    @property
    def text_content(self) -> str:
        return self.markdown

    @text_content.setter
    def text_content(self, markdown: str):
        self.markdown = markdown

    def __str__(self) -> str:
        return self.markdown


class DocumentConverter:
    def accepts(
        self,
        file_stream: BinaryIO,
        stream_info: StreamInfo,
        **kwargs: Any,
    ) -> bool:
        raise NotImplementedError(
            f"The subclass, {type(self).__name__}, must implement the accepts() method."
        )

    def convert(
        self,
        file_stream: BinaryIO,
        stream_info: StreamInfo,
        **kwargs: Any,
    ) -> DocumentConverterResult:
        raise NotImplementedError("Subclasses must implement this method")
