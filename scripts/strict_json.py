#!/usr/bin/env python3
"""Strict, dependency-free JSON loading for canonical Knowledge Distiller inputs.

Python's :mod:`json` intentionally accepts duplicate object names and the non-JSON
``NaN``/``Infinity`` constants.  It can also materialise isolated UTF-16 surrogate
code points from escape sequences.  Canonical graph inputs must reject all three so
that every accepted byte stream has one portable interpretation.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any


class StrictJsonError(ValueError):
    """The input is not interoperable, unambiguous RFC 8259 JSON."""


MAX_NUMBER_CHARS = 4_300

#: The house limit for a self-supplied input file, shared with
#: ``extract_source`` and the runner.  A caller that already checks the size
#: itself may raise or disable it, but no caller reads without a bound.
MAX_FILE_BYTES = 64 * 1024 * 1024


def _object_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise StrictJsonError(f"duplicate object key {key!r}")
        result[key] = value
    return result


def _reject_constant(token: str) -> None:
    raise StrictJsonError(f"non-finite number {token!r} is not valid JSON")


def _parse_int(token: str) -> int:
    if len(token.lstrip("-")) > MAX_NUMBER_CHARS:
        raise StrictJsonError("integer literal exceeds the safe numeric length limit")
    return int(token)


def _parse_float(token: str) -> float:
    if len(token) > MAX_NUMBER_CHARS:
        raise StrictJsonError("floating-point literal exceeds the safe numeric length limit")
    value = float(token)
    if not math.isfinite(value):
        raise StrictJsonError(f"non-finite number {token!r} is not valid JSON")
    return value


def _has_surrogate(value: str) -> bool:
    return any(0xD800 <= ord(character) <= 0xDFFF for character in value)


def _validate_tree(value: Any, location: str = "$") -> None:
    if isinstance(value, float):
        if not math.isfinite(value):
            raise StrictJsonError(f"{location}: non-finite number is not valid JSON")
        return
    if isinstance(value, str):
        if _has_surrogate(value):
            raise StrictJsonError(f"{location}: isolated Unicode surrogate is not allowed")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _validate_tree(item, f"{location}[{index}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if _has_surrogate(key):
                raise StrictJsonError(
                    f"{location}: object key contains an isolated Unicode surrogate"
                )
            _validate_tree(item, f"{location}.{key}")


def loads(data: str | bytes | bytearray, *, source: str = "<string>") -> Any:
    """Parse strict JSON and identify ``source`` in every expected failure."""
    try:
        if isinstance(data, (bytes, bytearray)):
            text = bytes(data).decode("utf-8", errors="strict")
        elif isinstance(data, str):
            text = data
        else:
            raise TypeError("strict_json.loads expects str, bytes, or bytearray")
        value = json.loads(
            text,
            object_pairs_hook=_object_without_duplicates,
            parse_constant=_reject_constant,
            parse_int=_parse_int,
            parse_float=_parse_float,
        )
        _validate_tree(value)
        return value
    except StrictJsonError as exc:
        raise StrictJsonError(f"{source}: {exc}") from exc
    except UnicodeDecodeError as exc:
        raise StrictJsonError(f"{source}: input is not valid UTF-8: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise StrictJsonError(f"{source}: invalid JSON: {exc}") from exc
    except ValueError as exc:
        raise StrictJsonError(f"{source}: invalid or unsafe JSON number: {exc}") from exc
    except RecursionError as exc:
        raise StrictJsonError(f"{source}: JSON nesting is too deep") from exc


def load_path(path: str | Path, *, max_bytes: int | None = MAX_FILE_BYTES) -> Any:
    """Read and strictly parse one UTF-8 JSON file.

    ``max_bytes`` bounds the read.  The size is checked before the file is read
    and again afterwards, because a file can grow between the two.  ``None``
    reads without a bound and is for callers that have already checked.
    """
    source = Path(path)
    if max_bytes is not None:
        if not isinstance(max_bytes, int) or isinstance(max_bytes, bool) or max_bytes <= 0:
            raise ValueError("max_bytes must be a positive integer or None")
        try:
            declared = source.stat().st_size
        except OSError as exc:
            raise StrictJsonError(f"{source}: could not read JSON: {exc}") from exc
        if declared > max_bytes:
            raise StrictJsonError(
                f"{source}: input is {declared} bytes; configured limit is {max_bytes} bytes"
            )
    try:
        data = source.read_bytes()
    except OSError as exc:
        raise StrictJsonError(f"{source}: could not read JSON: {exc}") from exc
    if max_bytes is not None and len(data) > max_bytes:
        raise StrictJsonError(
            f"{source}: input grew beyond the configured {max_bytes}-byte limit"
        )
    return loads(data, source=str(source))
