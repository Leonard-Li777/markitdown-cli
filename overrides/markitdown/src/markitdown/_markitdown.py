import mimetypes
import os
import re
import sys
import shutil
import traceback
import io
from dataclasses import dataclass
from importlib.metadata import entry_points
from typing import Any, List, Dict, Optional, Union, BinaryIO
from pathlib import Path
from urllib.parse import urlparse
from warnings import warn
import requests
import magika
import charset_normalizer
import codecs

from ._stream_info import StreamInfo
from ._uri_utils import parse_data_uri, file_uri_to_path

from .converters import (
    PlainTextConverter,
    HtmlConverter,
    RssConverter,
    WikipediaConverter,
    YouTubeConverter,
    IpynbConverter,
    BingSerpConverter,
    PdfConverter,
    DocxConverter,
    XlsxConverter,
    XlsConverter,
    PptxConverter,
    ImageConverter,
    AudioConverter,
    OutlookMsgConverter,
    ZipConverter,
    EpubConverter,
    DocumentIntelligenceConverter,
    ContentUnderstandingConverter,
    CsvConverter,
)

from ._base_converter import DocumentConverter, DocumentConverterResult
from ._exceptions import (
    FileConversionException,
    UnsupportedFormatException,
    FailedConversionAttempt,
)

PRIORITY_SPECIFIC_FILE_FORMAT = 0.0
PRIORITY_GENERIC_FILE_FORMAT = 10.0

_plugins: Union[None, List[Any]] = None


def _load_plugins() -> Union[None, List[Any]]:
    global _plugins
    if _plugins is not None:
        return _plugins
    _plugins = []
    for entry_point in entry_points(group="markitdown.plugin"):
        try:
            _plugins.append(entry_point.load())
        except Exception:
            tb = traceback.format_exc()
            warn(f"Plugin '{entry_point.name}' failed to load ... skipping:\n{tb}")
    return _plugins


@dataclass(kw_only=True, frozen=True)
class ConverterRegistration:
    converter: DocumentConverter
    priority: float


class MarkItDown:
    def __init__(
        self,
        *,
        enable_builtins: Union[None, bool] = None,
        enable_plugins: Union[None, bool] = None,
        **kwargs,
    ):
        self._builtins_enabled = False
        self._plugins_enabled = False

        requests_session = kwargs.get("requests_session")
        if requests_session is None:
            self._requests_session = requests.Session()
            self._requests_session.headers.update({
                "Accept": "text/markdown, text/html;q=0.9, text/plain;q=0.8, */*;q=0.1"
            })
        else:
            self._requests_session = requests_session

        self._magika = magika.Magika()

        self._llm_client: Any = None
        self._llm_model: Union[str | None] = None
        self._llm_prompt: Union[str | None] = None
        self._exiftool_path: Union[str | None] = None
        self._style_map: Union[str | None] = None

        self._converters: List[ConverterRegistration] = []

        if enable_builtins is None or enable_builtins:
            self.enable_builtins(**kwargs)

        if enable_plugins:
            self.enable_plugins(**kwargs)

    def enable_builtins(self, **kwargs) -> None:
        if not self._builtins_enabled:
            self._llm_client = kwargs.get("llm_client")
            self._llm_model = kwargs.get("llm_model")
            self._llm_prompt = kwargs.get("llm_prompt")
            self._exiftool_path = kwargs.get("exiftool_path")
            self._style_map = kwargs.get("style_map")

            if self._exiftool_path is None:
                self._exiftool_path = os.getenv("EXIFTOOL_PATH")
            if self._exiftool_path is None:
                candidate = shutil.which("exiftool")
                if candidate:
                    candidate = os.path.abspath(candidate)
                    if any(d == os.path.dirname(candidate) for d in [
                        "/usr/bin", "/usr/local/bin", "/opt", "/opt/bin",
                        "/opt/local/bin", "/opt/homebrew/bin",
                        "C:\\Windows\\System32", "C:\\Program Files",
                        "C:\\Program Files (x86)",
                    ]):
                        self._exiftool_path = candidate

            self.register_converter(PlainTextConverter(), priority=PRIORITY_GENERIC_FILE_FORMAT)
            self.register_converter(ZipConverter(markitdown=self), priority=PRIORITY_GENERIC_FILE_FORMAT)
            self.register_converter(HtmlConverter(), priority=PRIORITY_GENERIC_FILE_FORMAT)
            self.register_converter(RssConverter())
            self.register_converter(WikipediaConverter())
            self.register_converter(YouTubeConverter())
            self.register_converter(BingSerpConverter())
            self.register_converter(DocxConverter())
            self.register_converter(XlsxConverter())
            self.register_converter(XlsConverter())
            self.register_converter(PptxConverter())
            self.register_converter(AudioConverter())
            self.register_converter(ImageConverter())
            self.register_converter(IpynbConverter())
            self.register_converter(PdfConverter())
            self.register_converter(OutlookMsgConverter())
            self.register_converter(EpubConverter())
            self.register_converter(CsvConverter())

            docintel_endpoint = kwargs.get("docintel_endpoint")
            if docintel_endpoint is not None:
                docintel_args: Dict[str, Any] = {}
                docintel_args["endpoint"] = docintel_endpoint
                docintel_credential = kwargs.get("docintel_credential")
                if docintel_credential is not None:
                    docintel_args["credential"] = docintel_credential
                docintel_types = kwargs.get("docintel_file_types")
                if docintel_types is not None:
                    docintel_args["file_types"] = docintel_types
                docintel_version = kwargs.get("docintel_api_version")
                if docintel_version is not None:
                    docintel_args["api_version"] = docintel_version
                self.register_converter(DocumentIntelligenceConverter(**docintel_args))

            cu_endpoint = kwargs.get("cu_endpoint")
            if cu_endpoint is not None:
                cu_args: Dict[str, Any] = {}
                cu_args["endpoint"] = cu_endpoint
                cu_credential = kwargs.get("cu_credential")
                if cu_credential is not None:
                    cu_args["credential"] = cu_credential
                cu_analyzer_id = kwargs.get("cu_analyzer_id")
                if cu_analyzer_id is not None:
                    cu_args["analyzer_id"] = cu_analyzer_id
                cu_file_types = kwargs.get("cu_file_types")
                if cu_file_types is not None:
                    cu_args["file_types"] = cu_file_types
                self.register_converter(ContentUnderstandingConverter(**cu_args))

            self._builtins_enabled = True
        else:
            warn("Built-in converters are already enabled.", RuntimeWarning)

    def enable_plugins(self, **kwargs) -> None:
        if not self._plugins_enabled:
            plugins = _load_plugins()
            assert plugins is not None
            for plugin in plugins:
                try:
                    plugin.register_converters(self, **kwargs)
                except Exception:
                    tb = traceback.format_exc()
                    warn(f"Plugin '{plugin}' failed to register converters:\n{tb}")
            self._plugins_enabled = True
        else:
            warn("Plugins converters are already enabled.", RuntimeWarning)

    def convert(
        self,
        source: Union[str, requests.Response, Path, BinaryIO],
        *,
        stream_info: Optional[StreamInfo] = None,
        **kwargs: Any,
    ) -> DocumentConverterResult:
        if isinstance(source, str):
            if source.startswith("http:") or source.startswith("https:") or source.startswith("file:") or source.startswith("data:"):
                _kwargs = {k: v for k, v in kwargs.items()}
                if "url" in _kwargs:
                    _kwargs["mock_url"] = _kwargs["url"]
                    del _kwargs["url"]
                return self.convert_uri(source, stream_info=stream_info, **_kwargs)
            else:
                return self.convert_local(source, stream_info=stream_info, **kwargs)
        elif isinstance(source, Path):
            return self.convert_local(source, stream_info=stream_info, **kwargs)
        elif isinstance(source, requests.Response):
            return self.convert_response(source, stream_info=stream_info, **kwargs)
        elif hasattr(source, "read") and callable(source.read) and not isinstance(source, io.TextIOBase):
            return self.convert_stream(source, stream_info=stream_info, **kwargs)
        else:
            raise TypeError(f"Invalid source type: {type(source)}. Expected str, requests.Response, BinaryIO.")

    def convert_local(self, path: Union[str, Path], *, stream_info: Optional[StreamInfo] = None, file_extension: Optional[str] = None, url: Optional[str] = None, **kwargs: Any) -> DocumentConverterResult:
        if isinstance(path, Path):
            path = str(path)
        base_guess = StreamInfo(local_path=path, extension=os.path.splitext(path)[1], filename=os.path.basename(path))
        if stream_info is not None:
            base_guess = base_guess.copy_and_update(stream_info)
        if file_extension is not None:
            base_guess = base_guess.copy_and_update(extension=file_extension)
        if url is not None:
            base_guess = base_guess.copy_and_update(url=url)
        with open(path, "rb") as fh:
            guesses = self._get_stream_info_guesses(file_stream=fh, base_guess=base_guess)
            return self._convert(file_stream=fh, stream_info_guesses=guesses, **kwargs)

    def convert_stream(self, stream: BinaryIO, *, stream_info: Optional[StreamInfo] = None, file_extension: Optional[str] = None, url: Optional[str] = None, **kwargs: Any) -> DocumentConverterResult:
        guesses: List[StreamInfo] = []
        base_guess = None
        if stream_info is not None or file_extension is not None or url is not None:
            if stream_info is None:
                base_guess = StreamInfo()
            else:
                base_guess = stream_info
            if file_extension is not None:
                assert base_guess is not None
                base_guess = base_guess.copy_and_update(extension=file_extension)
            if url is not None:
                assert base_guess is not None
                base_guess = base_guess.copy_and_update(url=url)
        if not stream.seekable():
            buffer = io.BytesIO()
            while True:
                chunk = stream.read(4096)
                if not chunk:
                    break
                buffer.write(chunk)
            buffer.seek(0)
            stream = buffer
        guesses = self._get_stream_info_guesses(file_stream=stream, base_guess=base_guess or StreamInfo())
        return self._convert(file_stream=stream, stream_info_guesses=guesses, **kwargs)

    def convert_url(self, url: str, *, stream_info: Optional[StreamInfo] = None, file_extension: Optional[str] = None, mock_url: Optional[str] = None, **kwargs: Any) -> DocumentConverterResult:
        return self.convert_uri(url, stream_info=stream_info, file_extension=file_extension, mock_url=mock_url, **kwargs)

    def convert_uri(self, uri: str, *, stream_info: Optional[StreamInfo] = None, file_extension: Optional[str] = None, mock_url: Optional[str] = None, **kwargs: Any) -> DocumentConverterResult:
        uri = uri.strip()
        if uri.startswith("file:"):
            netloc, path = file_uri_to_path(uri)
            if netloc and netloc != "localhost":
                raise ValueError(f"Unsupported file URI: {uri}.")
            return self.convert_local(path, stream_info=stream_info, file_extension=file_extension, url=mock_url, **kwargs)
        elif uri.startswith("data:"):
            mimetype, attributes, data = parse_data_uri(uri)
            base_guess = StreamInfo(mimetype=mimetype, charset=attributes.get("charset"))
            if stream_info is not None:
                base_guess = base_guess.copy_and_update(stream_info)
            return self.convert_stream(io.BytesIO(data), stream_info=base_guess, file_extension=file_extension, url=mock_url, **kwargs)
        elif uri.startswith("http:") or uri.startswith("https:"):
            response = self._requests_session.get(uri, stream=True)
            response.raise_for_status()
            return self.convert_response(response, stream_info=stream_info, file_extension=file_extension, url=mock_url, **kwargs)
        else:
            raise ValueError(f"Unsupported URI scheme: {uri.split(':')[0]}.")

    def convert_response(self, response: requests.Response, *, stream_info: Optional[StreamInfo] = None, file_extension: Optional[str] = None, url: Optional[str] = None, **kwargs: Any) -> DocumentConverterResult:
        mimetype: Optional[str] = None
        charset: Optional[str] = None
        if "content-type" in response.headers:
            parts = response.headers["content-type"].split(";")
            mimetype = parts.pop(0).strip()
            for part in parts:
                if part.strip().startswith("charset="):
                    _charset = part.split("=")[1].strip()
                    if len(_charset) > 0:
                        charset = _charset
        filename: Optional[str] = None
        extension: Optional[str] = None
        if "content-disposition" in response.headers:
            m = re.search(r"filename=([^;]+)", response.headers["content-disposition"])
            if m:
                filename = m.group(1).strip("\"'")
                _, _extension = os.path.splitext(filename)
                if len(_extension) > 0:
                    extension = _extension
        if filename is None:
            parsed_url = urlparse(response.url)
            _, _extension = os.path.splitext(parsed_url.path)
            if len(_extension) > 0:
                filename = os.path.basename(parsed_url.path)
                extension = _extension
        base_guess = StreamInfo(mimetype=mimetype, charset=charset, filename=filename, extension=extension, url=response.url)
        if stream_info is not None:
            base_guess = base_guess.copy_and_update(stream_info)
        if file_extension is not None:
            base_guess = base_guess.copy_and_update(extension=file_extension)
        if url is not None:
            base_guess = base_guess.copy_and_update(url=url)
        buffer = io.BytesIO()
        for chunk in response.iter_content(chunk_size=512):
            buffer.write(chunk)
        buffer.seek(0)
        guesses = self._get_stream_info_guesses(file_stream=buffer, base_guess=base_guess)
        return self._convert(file_stream=buffer, stream_info_guesses=guesses, **kwargs)

    def _convert(
        self, *, file_stream: BinaryIO, stream_info_guesses: List[StreamInfo], **kwargs
    ) -> DocumentConverterResult:
        res: Union[None, DocumentConverterResult] = None
        failed_attempts: List[FailedConversionAttempt] = []
        sorted_registrations = sorted(self._converters, key=lambda x: x.priority)
        cur_pos = file_stream.tell()

        # Extract metadata flags
        with_metadata = kwargs.pop("with_metadata", False)
        metadata_only = kwargs.pop("metadata_only", False)

        for stream_info in stream_info_guesses + [StreamInfo()]:
            for converter_registration in sorted_registrations:
                converter = converter_registration.converter
                assert cur_pos == file_stream.tell(), "File stream position should NOT change between guess iterations"

                _kwargs = {k: v for k, v in kwargs.items()}

                if "llm_client" not in _kwargs and self._llm_client is not None:
                    _kwargs["llm_client"] = self._llm_client
                if "llm_model" not in _kwargs and self._llm_model is not None:
                    _kwargs["llm_model"] = self._llm_model
                if "llm_prompt" not in _kwargs and self._llm_prompt is not None:
                    _kwargs["llm_prompt"] = self._llm_prompt
                if "style_map" not in _kwargs and self._style_map is not None:
                    _kwargs["style_map"] = self._style_map
                if "exiftool_path" not in _kwargs and self._exiftool_path is not None:
                    _kwargs["exiftool_path"] = self._exiftool_path

                _kwargs["_parent_converters"] = self._converters

                if stream_info is not None:
                    if stream_info.extension is not None:
                        _kwargs["file_extension"] = stream_info.extension
                    if stream_info.url is not None:
                        _kwargs["url"] = stream_info.url

                _accepts = False
                try:
                    _accepts = converter.accepts(file_stream, stream_info, **_kwargs)
                except NotImplementedError:
                    pass

                assert cur_pos == file_stream.tell(), f"{type(converter).__name__}.accept() should NOT change the file_stream position"

                if _accepts:
                    try:
                        res = converter.convert(file_stream, stream_info, **_kwargs)
                    except Exception:
                        failed_attempts.append(FailedConversionAttempt(converter=converter, exc_info=sys.exc_info()))
                    finally:
                        file_stream.seek(cur_pos)

                if res is not None:
                    res.text_content = "\n".join([line.rstrip() for line in re.split(r"\r?\n", res.text_content)])
                    res.text_content = re.sub(r"\n{3,}", "\n\n", res.text_content)

                    # Metadata mode handling
                    if not with_metadata and not metadata_only:
                        res.markdown = _strip_inline_metadata(res.markdown)
                    elif metadata_only:
                        md = _build_metadata_output(res)
                        res.markdown = md

                    return res

        if len(failed_attempts) > 0:
            raise FileConversionException(attempts=failed_attempts)
        raise UnsupportedFormatException(
            "Could not convert stream to Markdown. No converter attempted a conversion, suggesting that the filetype is simply not supported."
        )

    def register_page_converter(self, converter: DocumentConverter) -> None:
        warn("register_page_converter is deprecated. Use register_converter instead.", DeprecationWarning)
        self.register_converter(converter)

    def register_converter(self, converter: DocumentConverter, *, priority: float = PRIORITY_SPECIFIC_FILE_FORMAT) -> None:
        self._converters.insert(0, ConverterRegistration(converter=converter, priority=priority))

    def _get_stream_info_guesses(self, file_stream: BinaryIO, base_guess: StreamInfo) -> List[StreamInfo]:
        guesses: List[StreamInfo] = []
        enhanced_guess = base_guess.copy_and_update()

        if base_guess.mimetype is None and base_guess.extension is not None:
            _m, _ = mimetypes.guess_type("placeholder" + base_guess.extension, strict=False)
            if _m is not None:
                enhanced_guess = enhanced_guess.copy_and_update(mimetype=_m)

        if base_guess.mimetype is not None and base_guess.extension is None:
            _e = mimetypes.guess_all_extensions(base_guess.mimetype, strict=False)
            if len(_e) > 0:
                enhanced_guess = enhanced_guess.copy_and_update(extension=_e[0])

        cur_pos = file_stream.tell()
        try:
            result = self._magika.identify_stream(file_stream)
            if result.status == "ok" and result.prediction.output.label != "unknown":
                charset = None
                if result.prediction.output.is_text:
                    file_stream.seek(cur_pos)
                    stream_page = file_stream.read(4096)
                    charset_result = charset_normalizer.from_bytes(stream_page).best()
                    if charset_result is not None:
                        charset = self._normalize_charset(charset_result.encoding)

                guessed_extension = None
                if len(result.prediction.output.extensions) > 0:
                    guessed_extension = "." + result.prediction.output.extensions[0]

                compatible = True
                if base_guess.mimetype is not None and base_guess.mimetype != result.prediction.output.mime_type:
                    compatible = False
                if base_guess.extension is not None and base_guess.extension.lstrip(".") not in result.prediction.output.extensions:
                    compatible = False
                if base_guess.charset is not None and self._normalize_charset(base_guess.charset) != charset:
                    compatible = False

                if compatible:
                    guesses.append(StreamInfo(
                        mimetype=base_guess.mimetype or result.prediction.output.mime_type,
                        extension=base_guess.extension or guessed_extension,
                        charset=base_guess.charset or charset,
                        filename=base_guess.filename,
                        local_path=base_guess.local_path,
                        url=base_guess.url,
                    ))
                else:
                    guesses.append(enhanced_guess)
                    guesses.append(StreamInfo(
                        mimetype=result.prediction.output.mime_type,
                        extension=guessed_extension,
                        charset=charset,
                        filename=base_guess.filename,
                        local_path=base_guess.local_path,
                        url=base_guess.url,
                    ))
            else:
                guesses.append(enhanced_guess)
        finally:
            file_stream.seek(cur_pos)

        return guesses

    def _normalize_charset(self, charset: str | None) -> str | None:
        if charset is None:
            return None
        try:
            return codecs.lookup(charset).name
        except LookupError:
            return charset


# ---------------------------------------------------------------------------
# Inline metadata helpers
# ---------------------------------------------------------------------------
_INLINE_METADATA_KEYWORDS = {
    "Title", "Artist", "Author", "Band", "Album", "Genre", "Track",
    "DateTimeOriginal", "CreateDate", "NumChannels", "SampleRate",
    "AvgBytesPerSec", "BitsPerSample", "ImageSize", "Caption",
    "Description", "Keywords", "GPSPosition",
}


def _strip_inline_metadata(markdown: str) -> str:
    """Remove inline metadata lines (ExifTool fields) from the start of markdown.

    Handles formats like:
        Key: value
        **Key:** value
    Stops stripping at the first line that doesn't match a known metadata key.
    """
    lines = markdown.split("\n")
    content_start = 0
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            content_start = i + 1
            continue
        # Match "Key: value" or "**Key:** value" patterns
        m = re.match(r"\*{0,2}([A-Za-z]+)\*{0,2}:\s*(.*)", stripped)
        if m and m.group(1) in _INLINE_METADATA_KEYWORDS:
            content_start = i + 1
        else:
            break
    return "\n".join(lines[content_start:]).strip()


def _build_metadata_output(res: DocumentConverterResult) -> str:
    """Build metadata-only output from result."""
    parts = []
    if res.title:
        parts.append(f"title: {res.title}")
    if res.metadata:
        for key, value in res.metadata.items():
            if isinstance(value, list):
                value = ", ".join(value)
            parts.append(f"{key}: {value}")
    if not parts and res.title:
        parts.append(f"title: {res.title}")
    return "\n".join(parts) if parts else ""
