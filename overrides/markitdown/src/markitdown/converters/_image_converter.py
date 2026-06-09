from typing import BinaryIO, Any, Union
import base64
import mimetypes
from ._exiftool import exiftool_metadata
from .._base_converter import DocumentConverter, DocumentConverterResult
from .._stream_info import StreamInfo

ACCEPTED_MIME_TYPE_PREFIXES = ["image/jpeg", "image/png"]
ACCEPTED_FILE_EXTENSIONS = [".jpg", ".jpeg", ".png"]


class ImageConverter(DocumentConverter):
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
            for f in ["ImageSize", "Title", "Caption", "Description", "Keywords",
                       "Artist", "Author", "DateTimeOriginal", "CreateDate", "GPSPosition"]:
                if f in raw_meta:
                    meta[f] = raw_meta[f]
                    if with_metadata:
                        md_content += f"{f}: {raw_meta[f]}\n"

        llm_client = kwargs.get("llm_client")
        llm_model = kwargs.get("llm_model")
        if llm_client is not None and llm_model is not None:
            llm_description = self._get_llm_description(
                file_stream, stream_info, client=llm_client, model=llm_model, prompt=kwargs.get("llm_prompt"),
            )
            if llm_description is not None:
                if with_metadata:
                    md_content += "\n# Description:\n" + llm_description.strip() + "\n"
                meta["llm_description"] = llm_description.strip()

        return DocumentConverterResult(markdown=md_content, metadata=meta)

    def _get_llm_description(self, file_stream: BinaryIO, stream_info: StreamInfo, *, client, model, prompt=None) -> Union[None, str]:
        if prompt is None or prompt.strip() == "":
            prompt = "Write a detailed caption for this image."
        content_type = stream_info.mimetype
        if not content_type:
            content_type, _ = mimetypes.guess_type("_dummy" + (stream_info.extension or ""))
        if not content_type:
            content_type = "application/octet-stream"
        cur_pos = file_stream.tell()
        try:
            base64_image = base64.b64encode(file_stream.read()).decode("utf-8")
        except Exception:
            return None
        finally:
            file_stream.seek(cur_pos)
        data_uri = f"data:{content_type};base64,{base64_image}"
        messages = [{"role": "user", "content": [{"type": "text", "text": prompt}, {"type": "image_url", "image_url": {"url": data_uri}}]}]
        response = client.chat.completions.create(model=model, messages=messages)
        return response.choices[0].message.content
