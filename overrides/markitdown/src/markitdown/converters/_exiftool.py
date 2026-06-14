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

    if file_path:
        output = subprocess.run(
            [exiftool_path, "-json", file_path],
            capture_output=True,
            text=False,
        ).stdout
    else:
        cur_pos = file_stream.tell()
        try:
            output = subprocess.run(
                [exiftool_path, "-json", "-"],
                input=file_stream.read(),
                capture_output=True,
                text=False,
            ).stdout
        finally:
            file_stream.seek(cur_pos)

    return json.loads(
        output.decode(locale.getpreferredencoding(False)),
    )[0]
