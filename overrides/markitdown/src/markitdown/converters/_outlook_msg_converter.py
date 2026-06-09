import sys
from typing import Any, Union, BinaryIO
from .._stream_info import StreamInfo
from .._base_converter import DocumentConverter, DocumentConverterResult
from .._exceptions import MissingDependencyException, MISSING_DEPENDENCY_MESSAGE

_dependency_exc_info = None
olefile = None
try:
    import olefile
except ImportError:
    _dependency_exc_info = sys.exc_info()

ACCEPTED_MIME_TYPE_PREFIXES = ["application/vnd.ms-outlook"]
ACCEPTED_FILE_EXTENSIONS = [".msg"]


class OutlookMsgConverter(DocumentConverter):
    def accepts(self, file_stream: BinaryIO, stream_info: StreamInfo, **kwargs: Any) -> bool:
        mimetype = (stream_info.mimetype or "").lower()
        extension = (stream_info.extension or "").lower()
        if extension in ACCEPTED_FILE_EXTENSIONS:
            return True
        for prefix in ACCEPTED_MIME_TYPE_PREFIXES:
            if mimetype.startswith(prefix):
                return True
        cur_pos = file_stream.tell()
        try:
            if olefile and not olefile.isOleFile(file_stream):
                return False
        finally:
            file_stream.seek(cur_pos)
        try:
            if olefile is not None:
                msg = olefile.OleFileIO(file_stream)
                toc = "\n".join([str(stream) for stream in msg.listdir()])
                return "__properties_version1.0" in toc and "__recip_version1.0_#00000000" in toc
        except Exception:
            pass
        finally:
            file_stream.seek(cur_pos)
        return False

    def convert(self, file_stream: BinaryIO, stream_info: StreamInfo, **kwargs: Any) -> DocumentConverterResult:
        with_metadata = kwargs.get("with_metadata", False)

        if _dependency_exc_info is not None:
            raise MissingDependencyException(
                MISSING_DEPENDENCY_MESSAGE.format(converter=type(self).__name__, extension=".msg", feature="outlook")
            ) from _dependency_exc_info[1].with_traceback(_dependency_exc_info[2])

        assert olefile is not None
        msg = olefile.OleFileIO(file_stream)

        headers = {
            "From": self._get_stream_data(msg, "__substg1.0_0C1F001F"),
            "To": self._get_stream_data(msg, "__substg1.0_0E04001F"),
            "Subject": self._get_stream_data(msg, "__substg1.0_0037001F"),
        }

        md_content = ""
        if with_metadata:
            md_content = "# Email Message\n\n"
            for key, value in headers.items():
                if value:
                    md_content += f"**{key}:** {value}\n"
            md_content += "\n## Content\n\n"

        body = self._get_stream_data(msg, "__substg1.0_1000001F")
        if body:
            md_content += body

        msg.close()

        return DocumentConverterResult(
            markdown=md_content.strip(),
            title=headers.get("Subject"),
            metadata={k: v for k, v in headers.items() if v} if not with_metadata else {},
        )

    def _get_stream_data(self, msg: Any, stream_path: str) -> Union[str, None]:
        assert olefile is not None
        assert isinstance(msg, olefile.OleFileIO)
        try:
            if msg.exists(stream_path):
                data = msg.openstream(stream_path).read()
                try:
                    return data.decode("utf-16-le").strip()
                except UnicodeDecodeError:
                    try:
                        return data.decode("utf-8").strip()
                    except UnicodeDecodeError:
                        return data.decode("utf-8", errors="ignore").strip()
        except Exception:
            pass
        return None
