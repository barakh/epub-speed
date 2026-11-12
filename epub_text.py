"""Dependency-free EPUB 2/3 text extraction.

A book becomes a list of chapters, each a list of blocks, each a list of
tokens. A token is ``[word, flags]`` where flags is a bitmask of
BOLD / ITALIC / SUP so that emphasis survives the trip into a word stream.

Only the standard library is used: zipfile + ElementTree for the container
and OPF, html.parser for the documents.
"""

from __future__ import annotations

import hashlib
import os
import posixpath
import re
import zipfile
from html.parser import HTMLParser
from xml.etree import ElementTree as ET

CONTAINER_NS = "urn:oasis:names:tc:opendocument:xmlns:container"
OPF_NS = "http://www.idpf.org/2007/opf"
DC_NS = "http://purl.org/dc/elements/1.1/"
NCX_NS = "http://www.daisy.org/z3986/2005/ncx/"
XHTML_NS = "http://www.w3.org/1999/xhtml"
EPUB_NS = "http://www.idpf.org/2007/ops"

BOLD = 1
ITALIC = 2
SUP = 4

XHTML_TYPES = {"application/xhtml+xml", "text/html", "application/xml"}

# Elements whose subtree is noise for reading.
_SKIP = {
    "script", "style", "head", "title", "rt", "rp", "svg", "math",
    "noscript", "template", "audio", "video", "object", "iframe", "canvas",
}

# Elements that end the current word/paragraph run.
_BLOCK = {
    "address", "article", "aside", "blockquote", "body", "br", "caption",
    "dd", "div", "dl", "dt", "fieldset", "figcaption", "figure", "footer",
    "form", "h1", "h2", "h3", "h4", "h5", "h6", "header", "hr", "li", "main",
    "nav", "ol", "p", "pre", "section", "table", "tbody", "td", "tfoot",
    "th", "thead", "tr", "ul",
}

_EMPH = {
    "b": BOLD, "strong": BOLD,
    "i": ITALIC, "em": ITALIC, "cite": ITALIC, "dfn": ITALIC, "var": ITALIC,
    "sup": SUP, "sub": SUP,
}

_KIND_OF = {
    "h1": "h1", "h2": "h2", "h3": "h3", "h4": "h4", "h5": "h5", "h6": "h6",
    "li": "li", "blockquote": "blockquote", "pre": "pre",
}

_ZERO_WIDTH = dict.fromkeys(map(ord, "\u00ad\u200b\u200c\u200d\ufeff"), None)


class _TextExtractor(HTMLParser):
    """Turn one XHTML document into paragraph-ish blocks of word tokens."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[dict] = []
        self.tokens: list[list] = []
        self.kind = "p"
        self._skip = 0
        self._bold = 0
        self._italic = 0
        self._sup = 0
        self._joinable = False

    # -- state ---------------------------------------------------------

    def _flags(self) -> int:
        flags = 0
        if self._bold:
            flags |= BOLD
        if self._italic:
            flags |= ITALIC
        if self._sup:
            flags |= SUP
        return flags

    def _flush(self) -> None:
        if self.tokens:
            self.blocks.append({"kind": self.kind, "tokens": self.tokens})
            self.tokens = []
        self.kind = "p"
        self._joinable = False

    # -- parser callbacks ----------------------------------------------

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in _SKIP:
            self._skip += 1
            return
        if self._skip:
            return
        bit = _EMPH.get(tag)
        if bit:
            if bit == BOLD:
                self._bold += 1
            elif bit == ITALIC:
                self._italic += 1
            else:
                self._sup += 1
            return
        if tag in _BLOCK:
            self._flush()
            if tag in _KIND_OF:
                self.kind = _KIND_OF[tag]

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in _SKIP:
            self._skip = max(0, self._skip - 1)
            return
        if self._skip:
            return
        bit = _EMPH.get(tag)
        if bit:
            if bit == BOLD:
                self._bold = max(0, self._bold - 1)
            elif bit == ITALIC:
                self._italic = max(0, self._italic - 1)
            else:
                self._sup = max(0, self._sup - 1)
            return
        if tag in _BLOCK:
            self._flush()

    def handle_data(self, data):
        if self._skip or not data:
            return
        data = data.translate(_ZERO_WIDTH).replace("\xa0", " ")
        flags = self._flags()
        for part in re.split(r"(\s+)", data):
            if not part:
                continue
            if part.isspace():
                self._joinable = False
                continue
            if self._joinable and self.tokens:
                self.tokens[-1][0] += part
                self.tokens[-1][1] |= flags
            else:
                self.tokens.append([part, flags])
            self._joinable = True

    def finish(self) -> list[dict]:
        try:
            self.close()
        except Exception:
            pass
        self._flush()
        return self.blocks


def _decode(data: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", "replace")


def _tokens_in(blocks: list[dict]) -> int:
    return sum(len(b["tokens"]) for b in blocks)


# -- container / package ---------------------------------------------------


def _manifest(opf: ET.Element, opf_dir: str) -> dict[str, dict]:
    items: dict[str, dict] = {}
    node = opf.find(f"{{{OPF_NS}}}manifest")
    if node is None:
        return items
    for item in node:
        ident = item.get("id")
        href = item.get("href")
        if not ident or not href:
            continue
        path = posixpath.normpath(posixpath.join(opf_dir, href.split("#")[0]))
        items[ident] = {
            "path": path,
            "type": (item.get("media-type") or "").strip().lower(),
            "props": (item.get("properties") or "").split(),
        }
    return items


def _toc_titles(z: zipfile.ZipFile, items: dict[str, dict]) -> dict[str, str]:
    """Map document path -> chapter title, from the EPUB3 nav or the NCX."""
    titles: dict[str, str] = {}

    def add(href: str, label: str, base_dir: str) -> None:
        label = " ".join(label.split())
        if not href or not label:
            return
        path = posixpath.normpath(posixpath.join(base_dir, href.split("#")[0]))
        titles.setdefault(path, label)

    for item in items.values():
        if "nav" not in item["props"] and item["type"] != "application/x-dtbncx+xml":
            continue
        try:
            data = z.read(item["path"])
        except KeyError:
            continue
        base_dir = posixpath.dirname(item["path"])
        try:
            root = ET.fromstring(data)
        except ET.ParseError:
            continue
        if item["type"] == "application/x-dtbncx+xml":
            for point in root.iter(f"{{{NCX_NS}}}navPoint"):
                content = point.find(f"{{{NCX_NS}}}content")
                label_node = point.find(f".//{{{NCX_NS}}}text")
                if content is None or label_node is None:
                    continue
                add(content.get("src", ""), "".join(label_node.itertext()), base_dir)
        else:
            for anchor in root.iter(f"{{{XHTML_NS}}}a"):
                add(anchor.get("href", ""), "".join(anchor.itertext()), base_dir)
    return titles


def _first_heading(blocks: list[dict]) -> str | None:
    for block in blocks:
        if block["kind"] in ("h1", "h2", "h3"):
            text = " ".join(t[0] for t in block["tokens"])
            if text.strip():
                return text.strip()[:120]
    return None


def _chapter_title(blocks: list[dict], toc: dict[str, str], path: str, index: int) -> str:
    if path in toc:
        return toc[path]
    heading = _first_heading(blocks)
    if heading:
        return heading
    return f"Chapter {index + 1}"


def hash_id(path: str) -> str:
    return hashlib.sha1(os.path.abspath(path).encode("utf-8")).hexdigest()[:12]


def load(path: str) -> dict:
    """Parse an EPUB into a book dict (with chapter blocks included)."""
    path = os.path.abspath(path)
    book = {
        "id": hash_id(path),
        "path": path,
        "file": os.path.basename(path),
        "title": os.path.splitext(os.path.basename(path))[0],
        "author": "",
        "chapters": [],
        "words": 0,
    }
    with zipfile.ZipFile(path) as z:
        names = {name.lower(): name for name in z.namelist()}
        container_name = names.get("meta-inf/container.xml")
        if not container_name:
            raise ValueError("not an EPUB: missing META-INF/container.xml")
        container = ET.fromstring(z.read(container_name))
        rootfile = container.find(f".//{{{CONTAINER_NS}}}rootfile")
        if rootfile is None or not rootfile.get("full-path"):
            raise ValueError("not an EPUB: no rootfile in container.xml")
        opf_path = rootfile.get("full-path")
        opf = ET.fromstring(z.read(names.get(opf_path.lower(), opf_path)))
        opf_dir = posixpath.dirname(opf_path)

        metadata = opf.find(f"{{{OPF_NS}}}metadata")
        if metadata is not None:
            title_node = metadata.find(f"{{{DC_NS}}}title")
            creator_node = metadata.find(f"{{{DC_NS}}}creator")
            if title_node is not None and (title_node.text or "").strip():
                book["title"] = " ".join(title_node.text.split())
            if creator_node is not None and (creator_node.text or "").strip():
                book["author"] = " ".join(creator_node.text.split())

        items = _manifest(opf, opf_dir)
        toc_titles = _toc_titles(z, items)

        spine = opf.find(f"{{{OPF_NS}}}spine")
        if spine is None:
            raise ValueError("not an EPUB: no spine")
        order = [ref.get("idref") for ref in spine if ref.get("idref")]

        seen: set[str] = set()
        for idref in order:
            item = items.get(idref)
            if not item or item["type"] not in XHTML_TYPES:
                continue
            if "nav" in item["props"]:
                continue
            if item["path"] in seen:
                continue
            seen.add(item["path"])
            key = names.get(item["path"].lower())
            if not key:
                continue
            try:
                data = z.read(key)
            except KeyError:
                continue
            blocks = _parse_document(data)
            words = _tokens_in(blocks)
            if words < 8:
                continue
            book["chapters"].append({
                "index": len(book["chapters"]),
                "path": item["path"],
                "title": _chapter_title(blocks, toc_titles, item["path"], len(book["chapters"])),
                "words": words,
                "blocks": blocks,
            })

    if not book["chapters"]:
        raise ValueError("no readable text found in this EPUB")
    book["words"] = sum(c["words"] for c in book["chapters"])
    return book


def _parse_document(data: bytes) -> list[dict]:
    parser = _TextExtractor()
    try:
        parser.feed(_decode(data))
    except Exception:
        pass
    return parser.finish()


def summarize(book: dict) -> dict:
    """Book without paragraph payload, for list/detail endpoints."""
    return {
        "id": book["id"],
        "title": book["title"],
        "author": book["author"],
        "file": book["file"],
        "words": book["words"],
        "chapters": [
            {"index": c["index"], "title": c["title"], "words": c["words"]}
            for c in book["chapters"]
        ],
    }
