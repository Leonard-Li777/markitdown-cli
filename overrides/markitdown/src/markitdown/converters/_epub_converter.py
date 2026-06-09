import os
import zipfile
from defusedxml import minidom
from xml.dom.minidom import Document
from typing import BinaryIO, Any, Dict, List

from ._html_converter import HtmlConverter
from .._base_converter import DocumentConverterResult
from .._stream_info import StreamInfo

ACCEPTED_MIME_TYPE_PREFIXES = ["application/epub", "application/epub+zip", "application/x-epub+zip"]
ACCEPTED_FILE_EXTENSIONS = [".epub"]

MIME_TYPE_MAPPING = {".html": "text/html", ".xhtml": "application/xhtml+xml"}


class EpubConverter(HtmlConverter):
    def __init__(self):
        super().__init__()
        self._html_converter = HtmlConverter()

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

        with zipfile.ZipFile(file_stream, "r") as z:
            container_dom = minidom.parse(z.open("META-INF/container.xml"))
            opf_path = container_dom.getElementsByTagName("rootfile")[0].getAttribute("full-path")
            opf_dom = minidom.parse(z.open(opf_path))

            metadata: Dict[str, Any] = {
                "title": self._get_text_from_node(opf_dom, "dc:title"),
                "authors": self._get_all_texts_from_nodes(opf_dom, "dc:creator"),
                "language": self._get_text_from_node(opf_dom, "dc:language"),
                "publisher": self._get_text_from_node(opf_dom, "dc:publisher"),
                "date": self._get_text_from_node(opf_dom, "dc:date"),
                "description": self._get_text_from_node(opf_dom, "dc:description"),
                "identifier": self._get_text_from_node(opf_dom, "dc:identifier"),
            }

            manifest = {item.getAttribute("id"): item.getAttribute("href") for item in opf_dom.getElementsByTagName("item")}
            spine_items = opf_dom.getElementsByTagName("itemref")
            spine_order = [item.getAttribute("idref") for item in spine_items]

            base_path = "/".join(opf_path.split("/")[:-1])
            spine = [
                f"{base_path}/{manifest[item_id]}" if base_path else manifest[item_id]
                for item_id in spine_order if item_id in manifest
            ]

            markdown_content: List[str] = []
            for file in spine:
                if file in z.namelist():
                    with z.open(file) as f:
                        filename = os.path.basename(file)
                        extension = os.path.splitext(filename)[1].lower()
                        mimetype = MIME_TYPE_MAPPING.get(extension)
                        converted_content = self._html_converter.convert(
                            f, StreamInfo(mimetype=mimetype, extension=extension, filename=filename),
                        )
                        markdown_content.append(converted_content.markdown.strip())

            if with_metadata:
                metadata_markdown = []
                for key, value in metadata.items():
                    if isinstance(value, list):
                        value = ", ".join(value)
                    if value:
                        metadata_markdown.append(f"**{key.capitalize()}:** {value}")
                markdown_content.insert(0, "\n".join(metadata_markdown))

            return DocumentConverterResult(
                markdown="\n\n".join(markdown_content),
                title=metadata["title"],
                metadata=metadata if not with_metadata else {},
            )

    def _get_text_from_node(self, dom: Document, tag_name: str) -> str | None:
        texts = self._get_all_texts_from_nodes(dom, tag_name)
        return texts[0] if texts else None

    def _get_all_texts_from_nodes(self, dom: Document, tag_name: str) -> List[str]:
        texts: List[str] = []
        for node in dom.getElementsByTagName(tag_name):
            if node.firstChild and hasattr(node.firstChild, "nodeValue"):
                texts.append(node.firstChild.nodeValue.strip())
        return texts
