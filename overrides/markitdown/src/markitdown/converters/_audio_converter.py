from typing import Any, BinaryIO
from ._exiftool import exiftool_metadata
from ._transcribe_audio import transcribe_audio
from .._base_converter import DocumentConverter, DocumentConverterResult
from .._stream_info import StreamInfo
from .._exceptions import MissingDependencyException

ACCEPTED_MIME_TYPE_PREFIXES = ["audio/x-wav", "audio/mpeg", "video/mp4"]
ACCEPTED_FILE_EXTENSIONS = [".wav", ".mp3", ".m4a", ".mp4"]


class AudioConverter(DocumentConverter):
    def accepts(self, file_stream: BinaryIO, stream_info: StreamInfo, **kwargs: Any) -> bool:
        mimetype = (stream_info.mimetype or "").lower()
        extension = (stream_info.extension or "").lower()
        if extension in ACCEPTED_FILE_EXTENSIONS:
            return True
        for prefix in ACCEPTED_MIME_TYPE_PREFIXES:
            if mimetype.startswith(prefix):
                return True
        return False

    def convert(self, file_stream: BinaryIO, stream_info: StreamInfo, **kwargs: Any) -> DocumentConverterResult:
        with_metadata = kwargs.get("with_metadata", False)
        md_content = ""
        meta: dict = {}

        raw_meta = exiftool_metadata(file_stream, exiftool_path=kwargs.get("exiftool_path"))
        if raw_meta:
            meta = raw_meta

        if stream_info.extension == ".wav" or stream_info.mimetype == "audio/x-wav":
            audio_format = "wav"
        elif stream_info.extension == ".mp3" or stream_info.mimetype == "audio/mpeg":
            audio_format = "mp3"
        elif stream_info.extension in [".mp4", ".m4a"] or stream_info.mimetype == "video/mp4":
            audio_format = "mp4"
        else:
            audio_format = None

        if audio_format:
            try:
                transcript = transcribe_audio(file_stream, audio_format=audio_format)
                if transcript:
                    if with_metadata:
                        md_content += "\n\n### Audio Transcript:\n" + transcript
                    meta["transcript"] = transcript.strip()
            except MissingDependencyException:
                pass

        return DocumentConverterResult(markdown=md_content.strip(), metadata=meta)
