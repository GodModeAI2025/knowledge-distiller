#!/usr/bin/env python3
"""Deterministically extract local source files into normalized text segments.

This adapter is deliberately local and passive: it never fetches URLs, imports a
document-specific package, executes macros/formulas/scripts, or invokes a model.
Supported inputs are UTF-8 TXT/Markdown/HTML/CSV/TSV/JSON and DOCX OOXML.

The public ``extract_source`` function returns a JSON-serializable document. The
CLI writes that document as deterministic JSON or JSONL to stdout.
"""

from __future__ import annotations

import argparse
import csv
import errno
import hashlib
import io
import json
import os
import re
import stat
import sys
import zipfile
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from typing import Any, Iterable
from xml.etree import ElementTree as ET

import strict_json


ADAPTER_VERSION = "1.0"
DEFAULT_MAX_INPUT_BYTES = 64 * 1024 * 1024
DEFAULT_MAX_SEGMENT_CHARS = 16_000
DEFAULT_MAX_SEGMENTS = 10_000
DEFAULT_MAX_OUTPUT_BYTES = 256 * 1024 * 1024
MAX_ARCHIVE_ENTRIES = 10_000
MAX_STRUCTURE_DEPTH = 512
MAX_LOGICAL_NAME_CHARS = 4_096
QUOTE_CONTEXT_CHARS = 32


FORMAT_BY_SUFFIX = {
    ".txt": ("txt", "text/plain"),
    ".md": ("md", "text/markdown"),
    ".markdown": ("md", "text/markdown"),
    ".html": ("html", "text/html"),
    ".htm": ("html", "text/html"),
    ".csv": ("csv", "text/csv"),
    ".tsv": ("tsv", "text/tab-separated-values"),
    ".json": ("json", "application/json"),
    ".docx": (
        "docx",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ),
}

URI_PREFIX = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:[\\/]")
XML_DECLARATION_ATTACK = re.compile(br"<!\s*(?:DOCTYPE|ENTITY)\b", re.IGNORECASE)
# The DTD screen above reads bytes, so it only sees a declaration that is
# written in ASCII-compatible bytes. A UTF-16 part carries the same declaration
# as "<\x00!\x00..." and would pass unseen, while the parser still reads it.
# OOXML parts are UTF-8 in practice, so a part that is not is refused rather
# than decoded into a second code path.
XML_UTF8_BOM = b"\xef\xbb\xbf"
XML_LEADING_SPACE = b" \t\r\n"
XML_DECLARED_ENCODING = re.compile(
    br"""^<\?xml[^>]*?encoding\s*=\s*['"]([A-Za-z0-9._-]+)['"]""", re.IGNORECASE
)
UTF8_NAMES = {b"utf-8", b"utf8"}

WORD_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
WORD_PARAGRAPH = "{%s}p" % WORD_NS
WORD_TEXT = "{%s}t" % WORD_NS
WORD_TAB = "{%s}tab" % WORD_NS
WORD_BREAKS = {"{%s}br" % WORD_NS, "{%s}cr" % WORD_NS}


class ExtractionError(Exception):
    """Base class for expected, user-facing extraction failures."""

    code = "extraction_error"


class PathSafetyError(ExtractionError):
    code = "path_rejected"


class UnsupportedSourceError(ExtractionError):
    code = "unsupported_source"


class InputTooLargeError(ExtractionError):
    code = "input_too_large"


class OutputTooLargeError(ExtractionError):
    code = "output_too_large"


class SourceEncodingError(ExtractionError):
    code = "encoding_error"


class MalformedSourceError(ExtractionError):
    code = "malformed_source"


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _looks_like_uri(value: str) -> bool:
    # Keep Windows drive paths distinguishable when the adapter is used on Windows.
    return bool(URI_PREFIX.match(value)) and not bool(WINDOWS_DRIVE.match(value))


def _logical_input_path(
    input_value: str | os.PathLike[str],
    input_root: str | os.PathLike[str],
) -> tuple[Path, str, tuple[int, int]]:
    """Return the canonical root and a validated root-relative logical name.

    This function deliberately performs only lexical path selection. The actual
    walk and file open happen relative to an already-open root directory in
    :func:`_open_input_fd`, so a symlink swap cannot redirect the later read.
    """
    value = os.fspath(input_value)
    if not isinstance(value, str) or not value or "\x00" in value:
        raise PathSafetyError("input path must be a non-empty local path without NUL bytes")
    if _looks_like_uri(value):
        raise UnsupportedSourceError("URLs and URI inputs are not supported; provide a local file path")

    declared_root = Path(input_root).expanduser().absolute()
    try:
        declared_identity = os.stat(declared_root, follow_symlinks=False)
        if stat.S_ISLNK(declared_identity.st_mode):
            raise PathSafetyError("input root must not be a symlink")
        if not stat.S_ISDIR(declared_identity.st_mode):
            raise PathSafetyError("input root must be a directory")
        root = declared_root.resolve(strict=True)
    except (FileNotFoundError, OSError) as exc:
        raise PathSafetyError("input root does not exist or cannot be resolved") from exc
    try:
        resolved_identity = os.stat(root, follow_symlinks=False)
    except OSError as exc:
        raise PathSafetyError("input root changed while it was being resolved") from exc
    expected_root = (declared_identity.st_dev, declared_identity.st_ino)
    if (
        not stat.S_ISDIR(resolved_identity.st_mode)
        or (resolved_identity.st_dev, resolved_identity.st_ino) != expected_root
    ):
        raise PathSafetyError("input root changed while it was being resolved")

    raw = Path(value).expanduser()
    if raw.is_absolute():
        try:
            relative = raw.relative_to(declared_root)
        except ValueError as exc:
            try:
                relative = raw.relative_to(root)
            except ValueError:
                raise PathSafetyError(
                    "absolute input path must be lexically beneath the configured input root"
                ) from exc
    else:
        relative = raw
    if not relative.parts or any(part in {"", ".", ".."} for part in relative.parts):
        raise PathSafetyError("input path traversal and empty path components are not allowed")
    logical_name = relative.as_posix()
    if len(logical_name) > MAX_LOGICAL_NAME_CHARS or any(
        ord(character) < 32 or ord(character) == 127 for character in logical_name
    ):
        raise PathSafetyError("logical input path contains control characters or is too long")
    try:
        logical_name.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise PathSafetyError("logical input path is not valid UTF-8") from exc
    return root, logical_name, expected_root


def _open_component(name: str, *, directory_fd: int, directory: bool) -> int:
    """Open one component without following a symlink or trusting a prior stat."""
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    if directory:
        flags |= getattr(os, "O_DIRECTORY", 0)
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    before: os.stat_result | None = None
    if nofollow:
        flags |= nofollow
    else:  # pragma: no cover - current supported POSIX platforms provide O_NOFOLLOW
        try:
            before = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        except OSError as exc:
            raise PathSafetyError("input path cannot be inspected safely") from exc
        if stat.S_ISLNK(before.st_mode):
            raise PathSafetyError("symlinks are not accepted in input paths")
    try:
        fd = os.open(name, flags, dir_fd=directory_fd)
    except OSError as exc:
        if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
            raise PathSafetyError("symlinks are not accepted in input paths") from exc
        if exc.errno == errno.ENOENT:
            raise PathSafetyError("input file does not exist") from exc
        raise PathSafetyError("input path cannot be opened safely") from exc
    try:
        opened = os.fstat(fd)
        expected_type = stat.S_ISDIR if directory else stat.S_ISREG
        if not expected_type(opened.st_mode):
            raise PathSafetyError(
                "input path component must be a directory"
                if directory
                else "input must be a regular file"
            )
        if before is not None and (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
            raise PathSafetyError("input path changed while it was being opened")
        return fd
    except Exception:
        os.close(fd)
        raise


def _open_absolute_directory(path: Path) -> int:
    """Anchor a canonical root by walking from ``/`` without following links."""
    if os.name != "posix" or os.open not in getattr(os, "supports_dir_fd", set()):
        raise PathSafetyError(
            "safe path access requires POSIX descriptor-relative filesystem operations"
        )
    absolute = path.expanduser().absolute()
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_DIRECTORY", 0)
    try:
        current_fd = os.open(os.path.sep, flags)
    except OSError as exc:  # pragma: no cover - a usable POSIX host always opens '/'
        raise PathSafetyError("filesystem root cannot be opened safely") from exc
    try:
        for component in absolute.parts[1:]:
            next_fd = _open_component(
                component, directory_fd=current_fd, directory=True
            )
            os.close(current_fd)
            current_fd = next_fd
        return current_fd
    except Exception:
        os.close(current_fd)
        raise


def _open_input_fd(
    root: Path, logical_name: str, expected_root: tuple[int, int]
) -> int:
    current_fd = _open_absolute_directory(root)
    try:
        opened_root = os.fstat(current_fd)
        if (opened_root.st_dev, opened_root.st_ino) != expected_root:
            raise PathSafetyError("input root changed while it was being opened")
        parts = Path(logical_name).parts
        for part in parts[:-1]:
            next_fd = _open_component(part, directory_fd=current_fd, directory=True)
            os.close(current_fd)
            current_fd = next_fd
        return _open_component(parts[-1], directory_fd=current_fd, directory=False)
    finally:
        os.close(current_fd)


def _read_fd_bounded(fd: int, max_bytes: int) -> bytes:
    if not isinstance(max_bytes, int) or isinstance(max_bytes, bool) or max_bytes <= 0:
        raise ValueError("max_bytes must be a positive integer")
    try:
        declared_size = os.fstat(fd).st_size
    except OSError as exc:
        raise PathSafetyError("input file cannot be inspected") from exc
    if declared_size > max_bytes:
        raise InputTooLargeError(
            f"input is {declared_size} bytes; configured limit is {max_bytes} bytes"
        )
    chunks: list[bytes] = []
    remaining = max_bytes + 1
    try:
        while remaining:
            chunk = os.read(fd, min(1024 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
    except OSError as exc:
        raise PathSafetyError("input file cannot be read") from exc
    data = b"".join(chunks)
    if len(data) > max_bytes:
        raise InputTooLargeError(f"input grew beyond the configured {max_bytes}-byte limit")
    return data


def _read_bounded_input(
    input_value: str | os.PathLike[str],
    input_root: str | os.PathLike[str],
    max_bytes: int,
) -> tuple[bytes, str]:
    root, logical_name, expected_root = _logical_input_path(input_value, input_root)
    fd = _open_input_fd(root, logical_name, expected_root)
    try:
        return _read_fd_bounded(fd, max_bytes), logical_name
    finally:
        os.close(fd)


def _decode_utf8(data: bytes, source_type: str) -> str:
    if data.startswith(b"%PDF-"):
        raise UnsupportedSourceError("PDF input is not supported; no text was guessed from binary data")
    if b"\x00" in data:
        raise UnsupportedSourceError(
            f"{source_type} input contains NUL bytes and appears to be binary"
        )
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise SourceEncodingError(
            f"{source_type} input is not valid UTF-8 at byte {exc.start}"
        ) from exc


def _normalize_newlines(value: str) -> str:
    return value.replace("\r\n", "\n").replace("\r", "\n")


def _trim_span(value: str, start: int, end: int) -> tuple[int, int]:
    while start < end and value[start].isspace():
        start += 1
    while end > start and value[end - 1].isspace():
        end -= 1
    return start, end


def _split_span(
    value: str, start: int, end: int, max_segment_chars: int
) -> Iterable[tuple[int, int]]:
    """Split a large exact-text span without changing the referenced characters."""
    start, end = _trim_span(value, start, end)
    while start < end:
        if end - start <= max_segment_chars:
            yield start, end
            return
        ceiling = start + max_segment_chars
        floor = start + max_segment_chars // 2
        candidates = [
            value.rfind("\n", floor, ceiling),
            value.rfind(" ", floor, ceiling),
            value.rfind("\t", floor, ceiling),
        ]
        cut = max(candidates)
        if cut <= start:
            cut = ceiling
        chunk_start, chunk_end = _trim_span(value, start, cut)
        if chunk_start < chunk_end:
            yield chunk_start, chunk_end
        start, _ = _trim_span(value, cut, end)


def _text_selectors(value: str, start: int, end: int) -> list[dict[str, Any]]:
    quote: dict[str, Any] = {
        "type": "TextQuoteSelector",
        "exact": value[start:end],
    }
    prefix = value[max(0, start - QUOTE_CONTEXT_CHARS):start]
    suffix = value[end:min(len(value), end + QUOTE_CONTEXT_CHARS)]
    if prefix:
        quote["prefix"] = prefix
    if suffix:
        quote["suffix"] = suffix
    return [
        {"type": "TextPositionSelector", "start": start, "end": end},
        quote,
    ]


def _append_bounded_draft(
    drafts: list[dict[str, Any]],
    draft: dict[str, Any],
    *,
    max_segments: int,
    max_segment_chars: int,
) -> None:
    text = draft.get("text")
    if not isinstance(text, str):
        raise MalformedSourceError("extractor produced a non-text segment")
    if len(text) > max_segment_chars:
        raise InputTooLargeError(
            f"normalized segment has {len(text)} characters; configured limit is "
            f"{max_segment_chars} characters"
        )
    if len(drafts) >= max_segments:
        raise InputTooLargeError(
            f"normalized source exceeds the configured {max_segments}-segment limit"
        )
    drafts.append(draft)


def _text_drafts(
    value: str, *, max_segments: int, max_segment_chars: int
) -> list[dict[str, Any]]:
    drafts: list[dict[str, Any]] = []
    cursor = 0
    separators = list(re.finditer(r"\n[ \t]*\n+", value))
    spans: list[tuple[int, int]] = []
    for separator in separators:
        spans.append((cursor, separator.start()))
        cursor = separator.end()
    spans.append((cursor, len(value)))
    for start, end in spans:
        for chunk_start, chunk_end in _split_span(value, start, end, max_segment_chars):
            _append_bounded_draft(
                drafts,
                {
                    "text": value[chunk_start:chunk_end],
                    "locator": f"chars={chunk_start}-{chunk_end}",
                    "selectors": _text_selectors(value, chunk_start, chunk_end),
                },
                max_segments=max_segments,
                max_segment_chars=max_segment_chars,
            )
    return drafts


class _HTMLTextExtractor(HTMLParser):
    """Extract visible semantic blocks without rendering or running document code."""

    BLOCK_TAGS = {
        "title", "h1", "h2", "h3", "h4", "h5", "h6", "p", "li",
        "dt", "dd", "blockquote", "pre", "td", "th", "caption",
    }
    SKIP_TAGS = {"script", "style", "noscript", "template", "svg", "canvas"}
    VOID_TAGS = {
        "area", "base", "br", "col", "embed", "hr", "img", "input", "link",
        "meta", "param", "source", "track", "wbr",
    }

    def __init__(self, *, max_segments: int, max_segment_chars: int) -> None:
        super().__init__(convert_charrefs=True)
        self.max_segments = max_segments
        self.max_segment_chars = max_segment_chars
        self.stack: list[dict[str, Any]] = []
        self.root_counts: dict[str, int] = {}
        self.blocks: list[dict[str, Any]] = []
        self.visible: list[str] = []
        self.sequence = 0

    def _skipping(self) -> bool:
        return any(frame["skip"] for frame in self.stack)

    def _active_capture(self) -> dict[str, Any] | None:
        for frame in reversed(self.stack):
            if frame.get("capture") is not None:
                return frame["capture"]
        return None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        parent_counts = self.stack[-1]["counts"] if self.stack else self.root_counts
        parent_counts[tag] = parent_counts.get(tag, 0) + 1
        parent_path = self.stack[-1]["path"] if self.stack else ""
        path = f"{parent_path}/{tag}[{parent_counts[tag]}]"
        inherited_skip = self._skipping()
        skip = inherited_skip or tag in self.SKIP_TAGS

        if tag == "br" and not skip:
            capture = self._active_capture()
            if capture is not None:
                capture["parts"].append("\n")

        if tag in self.VOID_TAGS:
            return
        if len(self.stack) >= MAX_STRUCTURE_DEPTH:
            raise MalformedSourceError(
                f"HTML nesting exceeds the safe depth limit of {MAX_STRUCTURE_DEPTH}"
            )

        attributes = {name.lower(): value for name, value in attrs if name}
        capture = None
        if not skip and tag in self.BLOCK_TAGS:
            self.sequence += 1
            identifier = attributes.get("id")
            selectors = []
            locator = path
            if identifier:
                selectors.append({"type": "FragmentSelector", "fragment": f"#{identifier}"})
                locator = f"#{identifier}; path={path}"
            selectors.append({"type": "FragmentSelector", "fragment": path})
            capture = {
                "sequence": self.sequence,
                "tag": tag,
                "locator": locator,
                "selectors": selectors,
                "parts": [],
            }
        self.stack.append(
            {"tag": tag, "path": path, "counts": {}, "skip": skip, "capture": capture}
        )

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag.lower() not in self.VOID_TAGS:
            self.handle_endtag(tag)

    def handle_data(self, data: str) -> None:
        if self._skipping() or not data:
            return
        self.visible.append(data)
        capture = self._active_capture()
        if capture is not None:
            capture["parts"].append(data)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        match = None
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index]["tag"] == tag:
                match = index
                break
        if match is None:
            return
        popped = self.stack[match:]
        del self.stack[match:]
        for frame in popped:
            self._finish_capture(frame.get("capture"))

    def close(self) -> None:
        super().close()
        while self.stack:
            self._finish_capture(self.stack.pop().get("capture"))

    def _finish_capture(self, capture: dict[str, Any] | None) -> None:
        if capture is None:
            return
        raw = "".join(capture["parts"])
        if capture["tag"] == "pre":
            normalized = _normalize_newlines(raw).strip()
        else:
            normalized = re.sub(r"\s+", " ", raw).strip()
        if not normalized:
            return
        selectors = list(capture["selectors"])
        selectors.append({"type": "TextQuoteSelector", "exact": normalized})
        _append_bounded_draft(
            self.blocks,
            {
                "sequence": capture["sequence"],
                "text": normalized,
                "locator": capture["locator"],
                "selectors": selectors,
            },
            max_segments=self.max_segments,
            max_segment_chars=self.max_segment_chars,
        )


def _html_drafts(
    value: str, *, max_segments: int, max_segment_chars: int
) -> list[dict[str, Any]]:
    parser = _HTMLTextExtractor(
        max_segments=max_segments, max_segment_chars=max_segment_chars
    )
    try:
        parser.feed(value)
        parser.close()
    except (ValueError, RecursionError) as exc:
        raise MalformedSourceError("HTML structure could not be parsed safely") from exc
    ordered = sorted(parser.blocks, key=lambda block: block["sequence"])
    drafts = [
        {key: block[key] for key in ("text", "locator", "selectors")}
        for block in ordered
    ]
    if not drafts:
        fallback = re.sub(r"\s+", " ", "".join(parser.visible)).strip()
        if fallback:
            _append_bounded_draft(
                drafts,
                {
                    "text": fallback,
                    "locator": "document",
                    "selectors": [
                        {"type": "FragmentSelector", "fragment": "document"},
                        {"type": "TextQuoteSelector", "exact": fallback},
                    ],
                },
                max_segments=max_segments,
                max_segment_chars=max_segment_chars,
            )
    return drafts


def _spreadsheet_column(index: int) -> str:
    value = index + 1
    result = ""
    while value:
        value, remainder = divmod(value - 1, 26)
        result = chr(ord("A") + remainder) + result
    return result


def _csv_drafts(
    value: str,
    delimiter: str,
    *,
    max_segments: int,
    max_segment_chars: int,
) -> list[dict[str, Any]]:
    drafts: list[dict[str, Any]] = []
    try:
        reader = csv.reader(io.StringIO(value, newline=""), delimiter=delimiter, strict=True)
        for row_number, row in enumerate(reader, start=1):
            if not row or not any(cell != "" for cell in row):
                continue
            cell_range = f"A{row_number}:{_spreadsheet_column(len(row) - 1)}{row_number}"
            _append_bounded_draft(
                drafts,
                {
                    "text": "\t".join(row),
                    "locator": f"data!{cell_range}",
                    "selectors": [
                        {
                            "type": "CsvSelector",
                            "sheet": "data",
                            "cell_range": cell_range,
                        }
                    ],
                },
                max_segments=max_segments,
                max_segment_chars=max_segment_chars,
            )
    except csv.Error as exc:
        raise MalformedSourceError(f"delimited text is malformed: {exc}") from exc
    return drafts


def _json_pointer_part(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


def _json_scalar_text(value: Any) -> str:
    if isinstance(value, str):
        return value if value.strip() else json.dumps(value, ensure_ascii=False)
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _json_drafts(
    value: str, *, max_segments: int, max_segment_chars: int
) -> list[dict[str, Any]]:
    try:
        document = strict_json.loads(value, source="JSON source")
    except strict_json.StrictJsonError as exc:
        raise MalformedSourceError(str(exc)) from exc

    drafts: list[dict[str, Any]] = []

    def add(pointer: str, item: Any) -> None:
        _append_bounded_draft(
            drafts,
            {
                "text": _json_scalar_text(item),
                "locator": pointer or "(root)",
                "selectors": [
                    {"type": "JsonPointerSelector", "json_pointer": pointer}
                ],
            },
            max_segments=max_segments,
            max_segment_chars=max_segment_chars,
        )

    def walk(item: Any, pointer: str, depth: int) -> None:
        if depth > 256:
            raise MalformedSourceError("JSON nesting exceeds the safe depth limit of 256")
        if isinstance(item, dict):
            if not item:
                add(pointer, item)
            for key in sorted(item):
                walk(item[key], pointer + "/" + _json_pointer_part(key), depth + 1)
        elif isinstance(item, list):
            if not item:
                add(pointer, item)
            for index, child in enumerate(item):
                walk(child, pointer + "/" + str(index), depth + 1)
        else:
            add(pointer, item)

    walk(document, "", 0)
    return drafts


def _safe_zip_names(archive: zipfile.ZipFile, max_bytes: int) -> set[str]:
    infos = archive.infolist()
    if len(infos) > MAX_ARCHIVE_ENTRIES:
        raise InputTooLargeError(
            f"DOCX contains {len(infos)} archive entries; limit is {MAX_ARCHIVE_ENTRIES}"
        )
    names: set[str] = set()
    expanded = 0
    for info in infos:
        name = info.filename
        path = PurePosixPath(name)
        if (
            not name
            or name.startswith(("/", "\\"))
            or "\\" in name
            or any(part in {"", ".", ".."} for part in path.parts)
        ):
            raise MalformedSourceError(f"DOCX contains an unsafe archive member name: {name!r}")
        if name in names:
            raise MalformedSourceError(f"DOCX contains duplicate archive member {name!r}")
        names.add(name)
        if info.flag_bits & 0x1:
            raise UnsupportedSourceError("encrypted DOCX archive members are not supported")
        expanded += info.file_size
        if expanded > max_bytes:
            raise InputTooLargeError(
                f"DOCX expands beyond the configured {max_bytes}-byte limit"
            )
    return names


def _read_zip_member(archive: zipfile.ZipFile, name: str, max_bytes: int) -> bytes:
    try:
        with archive.open(name, "r") as handle:
            data = handle.read(max_bytes + 1)
    except (KeyError, RuntimeError, zipfile.BadZipFile) as exc:
        raise MalformedSourceError(f"DOCX member {name!r} cannot be read safely") from exc
    if len(data) > max_bytes:
        raise InputTooLargeError(f"DOCX member {name!r} exceeds the configured size limit")
    return data


def _require_utf8_xml(name: str, data: bytes) -> None:
    """Refuse an OOXML part that the byte-level DTD screen could not read.

    The screen matches ASCII-compatible bytes.  A part encoded as UTF-16 or
    UTF-32 carries every declaration with interleaved null bytes, so the screen
    finds nothing while the parser reads the declaration all the same.

    The null byte is what decides it: XML forbids U+0000 outright and UTF-8
    never emits one inside a multi-byte sequence, so a part that carries one is
    not UTF-8 XML.  That also covers UTF-16 without a byte order mark, which
    begins with a plain ``<`` and which the parser detects from the two bytes
    that follow.  Leading whitespace is allowed because XML allows it before the
    root element of a part that carries no declaration.
    """
    body = data[len(XML_UTF8_BOM):] if data.startswith(XML_UTF8_BOM) else data
    body = body.lstrip(XML_LEADING_SPACE)
    if body[:1] not in (b"<", b"") or b"\x00" in body:
        raise MalformedSourceError(
            f"DOCX member {name!r} is not UTF-8 encoded XML"
        )
    declared = XML_DECLARED_ENCODING.match(body)
    if declared and declared.group(1).lower() not in UTF8_NAMES:
        raise MalformedSourceError(
            f"DOCX member {name!r} declares encoding "
            f"{declared.group(1).decode('ascii', 'replace')!r}; only UTF-8 is supported"
        )


def _docx_paragraph_text(paragraph: ET.Element) -> str:
    parts: list[str] = []
    for item in paragraph.iter():
        if item.tag == WORD_TEXT:
            parts.append(item.text or "")
        elif item.tag == WORD_TAB:
            parts.append("\t")
        elif item.tag in WORD_BREAKS:
            parts.append("\n")
    return _normalize_newlines("".join(parts)).strip()


def _docx_drafts(
    data: bytes,
    max_bytes: int,
    *,
    max_segments: int,
    max_segment_chars: int,
) -> list[dict[str, Any]]:
    try:
        archive = zipfile.ZipFile(io.BytesIO(data), "r")
    except (zipfile.BadZipFile, OSError) as exc:
        raise MalformedSourceError("DOCX is not a valid OOXML ZIP package") from exc

    with archive:
        names = _safe_zip_names(archive, max_bytes)
        required = {"[Content_Types].xml", "word/document.xml"}
        missing = sorted(required - names)
        if missing:
            raise MalformedSourceError(
                "DOCX is missing required OOXML member(s): " + ", ".join(missing)
            )

        lowered = {name.lower() for name in names}
        if "word/vbaproject.bin" in lowered:
            raise UnsupportedSourceError("macro-bearing OOXML packages are not supported")
        content_types = _read_zip_member(archive, "[Content_Types].xml", max_bytes)
        if b"macroEnabled".lower() in content_types.lower():
            raise UnsupportedSourceError("macro-enabled OOXML content types are not supported")

        parts = ["word/document.xml"]
        for fixed in ("word/footnotes.xml", "word/endnotes.xml"):
            if fixed in names:
                parts.append(fixed)
        parts.extend(sorted(name for name in names if re.fullmatch(r"word/header\d+\.xml", name)))
        parts.extend(sorted(name for name in names if re.fullmatch(r"word/footer\d+\.xml", name)))

        drafts: list[dict[str, Any]] = []
        for part_name in parts:
            xml = _read_zip_member(archive, part_name, max_bytes)
            _require_utf8_xml(part_name, xml)
            if XML_DECLARATION_ATTACK.search(xml):
                raise MalformedSourceError(
                    f"DOCX member {part_name!r} contains a forbidden DTD/entity declaration"
                )
            try:
                root = ET.fromstring(xml)
            except ET.ParseError as exc:
                raise MalformedSourceError(
                    f"DOCX member {part_name!r} contains malformed XML: {exc}"
                ) from exc
            paragraph_number = 0
            for paragraph in root.iter(WORD_PARAGRAPH):
                paragraph_number += 1
                paragraph_text = _docx_paragraph_text(paragraph)
                if not paragraph_text:
                    continue
                fragment = f"{part_name}#paragraph={paragraph_number}"
                _append_bounded_draft(
                    drafts,
                    {
                        "text": paragraph_text,
                        "locator": fragment,
                        "selectors": [
                            {"type": "FragmentSelector", "fragment": fragment},
                            {"type": "TextQuoteSelector", "exact": paragraph_text},
                        ],
                    },
                    max_segments=max_segments,
                    max_segment_chars=max_segment_chars,
                )
        return drafts


def _finalize_segments(
    drafts: Iterable[dict[str, Any]],
    source_hash: str,
    *,
    max_segments: int,
    max_segment_chars: int,
) -> list[dict[str, Any]]:
    segments: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for draft in drafts:
        segment_text = draft.get("text")
        selectors = draft.get("selectors")
        locator = draft.get("locator")
        if not isinstance(segment_text, str) or not isinstance(selectors, list) or not isinstance(locator, str):
            raise MalformedSourceError("extractor produced an invalid internal segment")
        if len(segment_text) > max_segment_chars:
            raise InputTooLargeError(
                f"normalized segment has {len(segment_text)} characters; configured limit is "
                f"{max_segment_chars} characters"
            )
        if len(segments) >= max_segments:
            raise InputTooLargeError(
                f"normalized source exceeds the configured {max_segments}-segment limit"
            )
        identity = {
            "source_sha256": source_hash,
            "selectors": selectors,
            "text": segment_text,
        }
        try:
            text_bytes = segment_text.encode("utf-8")
            identity_bytes = _canonical_json(identity)
        except UnicodeEncodeError as exc:
            raise MalformedSourceError(
                "extracted text or selector contains an invalid Unicode surrogate"
            ) from exc
        identifier = "seg-" + _sha256(identity_bytes)[:24]
        if identifier in seen_ids:
            raise MalformedSourceError("source produced ambiguous duplicate segment identities")
        seen_ids.add(identifier)
        segments.append(
            {
                "id": identifier,
                "index": len(segments),
                "locator": locator,
                "selector": selectors[0],
                "selectors": selectors,
                "text": segment_text,
                "text_sha256": _sha256(text_bytes),
            }
        )
    return segments


def extract_source(
    input_value: str | os.PathLike[str],
    *,
    input_root: str | os.PathLike[str],
    max_bytes: int = DEFAULT_MAX_INPUT_BYTES,
    max_segments: int = DEFAULT_MAX_SEGMENTS,
    max_segment_chars: int = DEFAULT_MAX_SEGMENT_CHARS,
    max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
    output_format: str = "json",
) -> dict[str, Any]:
    """Extract ``input_value`` under ``input_root`` into deterministic segments."""
    limits = (
        ("max_bytes", max_bytes, DEFAULT_MAX_INPUT_BYTES),
        ("max_segments", max_segments, DEFAULT_MAX_SEGMENTS),
        ("max_segment_chars", max_segment_chars, DEFAULT_MAX_SEGMENT_CHARS),
        ("max_output_bytes", max_output_bytes, DEFAULT_MAX_OUTPUT_BYTES),
    )
    for name, value, hard_maximum in limits:
        if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= hard_maximum:
            raise ValueError(f"{name} must be an integer between 1 and {hard_maximum}")
    if output_format not in {"json", "jsonl"}:
        raise ValueError("output_format must be 'json' or 'jsonl'")
    data, logical_name = _read_bounded_input(input_value, input_root, max_bytes)
    suffix = Path(logical_name).suffix.lower()
    if suffix == ".pdf":
        raise UnsupportedSourceError(
            "PDF is intentionally unsupported; use a trusted local PDF text extractor first"
        )
    if suffix not in FORMAT_BY_SUFFIX:
        raise UnsupportedSourceError(
            f"unsupported file type {suffix or '(no extension)'}; supported: "
            + ", ".join(sorted(FORMAT_BY_SUFFIX))
        )

    source_type, mime_type = FORMAT_BY_SUFFIX[suffix]
    source_hash = _sha256(data)

    if source_type in {"txt", "md"}:
        canonical_text = _normalize_newlines(_decode_utf8(data, source_type))
        drafts = _text_drafts(
            canonical_text,
            max_segments=max_segments,
            max_segment_chars=max_segment_chars,
        )
    elif source_type == "html":
        canonical_text = _normalize_newlines(_decode_utf8(data, source_type))
        drafts = _html_drafts(
            canonical_text,
            max_segments=max_segments,
            max_segment_chars=max_segment_chars,
        )
    elif source_type in {"csv", "tsv"}:
        canonical_text = _normalize_newlines(_decode_utf8(data, source_type))
        drafts = _csv_drafts(
            canonical_text,
            "," if source_type == "csv" else "\t",
            max_segments=max_segments,
            max_segment_chars=max_segment_chars,
        )
    elif source_type == "json":
        canonical_text = _normalize_newlines(_decode_utf8(data, source_type))
        drafts = _json_drafts(
            canonical_text,
            max_segments=max_segments,
            max_segment_chars=max_segment_chars,
        )
    elif source_type == "docx":
        drafts = _docx_drafts(
            data,
            max_bytes,
            max_segments=max_segments,
            max_segment_chars=max_segment_chars,
        )
    else:  # pragma: no cover - closed dispatch table
        raise UnsupportedSourceError(f"unsupported source type {source_type!r}")

    source = {
        "id": "source-" + source_hash[:24],
        "file": logical_name,
        "logical_filename": logical_name,
        "type": source_type,
        "mime_type": mime_type,
        "content_sha256": source_hash,
        "size_bytes": len(data),
    }
    segments = _finalize_segments(
        drafts,
        source_hash,
        max_segments=max_segments,
        max_segment_chars=max_segment_chars,
    )
    document = {
        "adapter_version": ADAPTER_VERSION,
        "source": source,
        "segment_count": len(segments),
        "segments": segments,
    }
    if output_format == "jsonl":
        _check_jsonl_output_size(document, max_output_bytes)
    else:
        _check_json_output_size(document, max_output_bytes)
    return document


def _check_json_output_size(document: dict[str, Any], max_output_bytes: int) -> None:
    if (
        not isinstance(max_output_bytes, int)
        or isinstance(max_output_bytes, bool)
        or not 1 <= max_output_bytes <= DEFAULT_MAX_OUTPUT_BYTES
    ):
        raise ValueError(
            f"max_output_bytes must be an integer between 1 and {DEFAULT_MAX_OUTPUT_BYTES}"
        )
    total = 1  # final newline
    encoder = json.JSONEncoder(
        ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False
    )
    for piece in encoder.iterencode(document):
        total += len(piece.encode("utf-8"))
        if total > max_output_bytes:
            raise OutputTooLargeError(
                f"serialized JSON exceeds the configured {max_output_bytes}-byte limit"
            )


def render_json(
    document: dict[str, Any], max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES
) -> bytes:
    _check_json_output_size(document, max_output_bytes)
    return (
        json.dumps(document, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
        + "\n"
    ).encode("utf-8")


def render_jsonl(
    document: dict[str, Any], max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES
) -> bytes:
    if (
        not isinstance(max_output_bytes, int)
        or isinstance(max_output_bytes, bool)
        or not 1 <= max_output_bytes <= DEFAULT_MAX_OUTPUT_BYTES
    ):
        raise ValueError(
            f"max_output_bytes must be an integer between 1 and {DEFAULT_MAX_OUTPUT_BYTES}"
        )
    records = [
        {
            "record_type": "source",
            "adapter_version": document["adapter_version"],
            "segment_count": document["segment_count"],
            "source": document["source"],
        }
    ]
    records.extend(
        {
            "record_type": "segment",
            "source_id": document["source"]["id"],
            **segment,
        }
        for segment in document["segments"]
    )
    chunks: list[bytes] = []
    total = 0
    for record in records:
        chunk = _canonical_json(record) + b"\n"
        total += len(chunk)
        if total > max_output_bytes:
            raise OutputTooLargeError(
                f"serialized JSONL exceeds the configured {max_output_bytes}-byte limit"
            )
        chunks.append(chunk)
    return b"".join(chunks)


def _check_jsonl_output_size(document: dict[str, Any], max_output_bytes: int) -> None:
    # Reuse the serializer so the preflight and emitted JSONL have exactly the same byte bound.
    render_jsonl(document, max_output_bytes)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Extract a bounded local source into deterministic JSON/JSONL segments."
    )
    parser.add_argument("input", help="Input path relative to --input-root (or an absolute path inside it)")
    parser.add_argument("--input-root", required=True, help="Readable sandbox root for the input")
    parser.add_argument(
        "--format",
        choices=("json", "jsonl"),
        default="json",
        help="Output encoding written to stdout (default: json)",
    )
    parser.add_argument(
        "--max-bytes",
        type=int,
        default=DEFAULT_MAX_INPUT_BYTES,
        help=f"Maximum raw and expanded input bytes (default: {DEFAULT_MAX_INPUT_BYTES})",
    )
    parser.add_argument(
        "--max-segments",
        type=int,
        default=DEFAULT_MAX_SEGMENTS,
        help=f"Maximum normalized segment count (default: {DEFAULT_MAX_SEGMENTS})",
    )
    parser.add_argument(
        "--max-segment-chars",
        type=int,
        default=DEFAULT_MAX_SEGMENT_CHARS,
        help=f"Maximum characters per normalized segment (default: {DEFAULT_MAX_SEGMENT_CHARS})",
    )
    parser.add_argument(
        "--max-output-bytes",
        type=int,
        default=DEFAULT_MAX_OUTPUT_BYTES,
        help=f"Maximum serialized output bytes (default: {DEFAULT_MAX_OUTPUT_BYTES})",
    )
    args = parser.parse_args(argv)

    try:
        document = extract_source(
            args.input,
            input_root=args.input_root,
            max_bytes=args.max_bytes,
            max_segments=args.max_segments,
            max_segment_chars=args.max_segment_chars,
            max_output_bytes=args.max_output_bytes,
            output_format=args.format,
        )
        output = (
            render_jsonl(document, args.max_output_bytes)
            if args.format == "jsonl"
            else render_json(document, args.max_output_bytes)
        )
        sys.stdout.buffer.write(output)
        return 0
    except ExtractionError as exc:
        print(f"error [{exc.code}]: {exc}", file=sys.stderr)
        return 2
    except ValueError as exc:
        print(f"error [invalid_request]: {exc}", file=sys.stderr)
        return 2
    except BrokenPipeError:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
