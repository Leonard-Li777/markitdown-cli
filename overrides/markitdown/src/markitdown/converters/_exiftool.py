import os
import json
import locale
import subprocess
from typing import Any, BinaryIO, Union, Optional


def _parse_version(version: str) -> tuple:
    return tuple(map(int, (version.split("."))))


def exiftool_metadata(
    file_stream: BinaryIO,
    *,
    exiftool_path: Union[str, None],
    file_path: Optional[str] = None,
) -> Any:
    if not exiftool_path:
        return {}

    try:
        version_output = subprocess.run(
            [exiftool_path, "-ver"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        version = _parse_version(version_output)
        min_version = (12, 24)
        if version < min_version:
            raise RuntimeError(
                f"ExifTool version {version_output} is vulnerable to CVE-2021-22204. "
                "Please upgrade to version 12.24 or later."
            )
    except (subprocess.CalledProcessError, ValueError) as e:
        raise RuntimeError("Failed to verify ExifTool version.") from e

    if file_path and os.path.isfile(file_path):
        output = subprocess.run(
            [exiftool_path, "-json", file_path],
            capture_output=True,
            text=False,
        ).stdout
    else:
        if isinstance(file_stream, bytes):
            input_bytes = file_stream
        else:
            cur_pos = file_stream.tell()
            try:
                input_bytes = file_stream.read()
            finally:
                file_stream.seek(cur_pos)

        output = subprocess.run(
            [exiftool_path, "-json", "-"],
            input=input_bytes,
            capture_output=True,
            text=False,
        ).stdout

    if not output:
        return {}

    parsed = json.loads(
        output.decode(locale.getpreferredencoding(False), errors="replace"),
    )
    return parsed[0] if isinstance(parsed, list) and parsed else {}
