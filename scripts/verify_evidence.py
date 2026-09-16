#!/usr/bin/env python3
"""Check that graph evidence still points at the normalized source it claims to quote.

``validate_knowledge.py`` proves that an evidence record is well formed. It cannot prove that
the quoted passage exists, because the graph does not carry its sources. An external compiler can
therefore produce a perfectly conformant graph whose ``TextQuoteSelector.exact`` never occurred in
the document. This tool closes that gap mechanically, using the output of ``extract_source.py``:

* evidence is matched to a normalized source only through an identical ``content_sha256``;
* ``TextQuoteSelector.exact`` and an optional ``excerpt`` must occur in the normalized text
  (whitespace runs compared as one space);
* ``TextPositionSelector`` ranges must lie inside one extracted text segment, and a present
  ``excerpt`` must equal that slice;
* ``FragmentSelector``, ``CsvSelector`` and ``JsonPointerSelector`` must equal a selector the
  adapter emitted, because evidence is expected to copy segment selectors rather than invent them.

Anything else is reported as ``unverifiable``, never as verified. A resolved anchor says that the
passage exists; it does not say that the passage supports the claim.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import strict_json  # noqa: E402

MAX_FILE_BYTES = 64 * 1024 * 1024
WHITESPACE = re.compile(r"\s+")
IDENTITY_FIELDS = {
    "FragmentSelector": ("fragment",),
    "CsvSelector": ("sheet", "cell_range"),
    "JsonPointerSelector": ("json_pointer",),
}


class VerificationError(ValueError):
    pass


def _collapse(value: str) -> str:
    return WHITESPACE.sub(" ", value).strip()


def _digest(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    return value.removeprefix("sha256:").lower()


def _read_bounded(path: Path) -> str:
    if not path.is_file():
        raise VerificationError(f"not a regular file: {path}")
    if path.stat().st_size > MAX_FILE_BYTES:
        raise VerificationError(f"file exceeds {MAX_FILE_BYTES} byte limit: {path}")
    return path.read_text(encoding="utf-8")


def _load_json(path: Path) -> Any:
    try:
        return strict_json.loads(_read_bounded(path))
    except strict_json.StrictJsonError as exc:
        raise VerificationError(f"invalid strict JSON in {path}: {exc}") from exc


def load_normalized(path: Path) -> dict[str, Any]:
    """Read one ``extract_source.py`` result in JSON or JSONL mode."""
    if path.suffix.lower() == ".jsonl":
        source: dict[str, Any] | None = None
        segments: list[dict[str, Any]] = []
        for number, line in enumerate(_read_bounded(path).splitlines(), 1):
            if not line.strip():
                continue
            try:
                record = strict_json.loads(line)
            except strict_json.StrictJsonError as exc:
                raise VerificationError(f"{path}:{number}: invalid strict JSON: {exc}") from exc
            kind = record.get("record_type") if isinstance(record, dict) else None
            if kind == "source":
                source = record.get("source", record)
            elif kind == "segment":
                segments.append(record.get("segment", record))
        document: Any = {"source": source, "segments": segments}
    else:
        document = _load_json(path)
    if not isinstance(document, dict) or not isinstance(document.get("source"), dict):
        raise VerificationError(f"{path}: not an extract_source.py result (missing source)")
    if not isinstance(document.get("segments"), list):
        raise VerificationError(f"{path}: not an extract_source.py result (missing segments)")
    if _digest(document["source"].get("content_sha256")) is None:
        raise VerificationError(f"{path}: normalized source has no content_sha256")
    return document


def _segment_selectors(segment: dict[str, Any]) -> list[dict[str, Any]]:
    selectors = [s for s in segment.get("selectors", []) if isinstance(s, dict)]
    primary = segment.get("selector")
    if isinstance(primary, dict) and primary not in selectors:
        selectors.append(primary)
    return selectors


class _Index:
    def __init__(self, document: dict[str, Any]) -> None:
        segments = [s for s in document["segments"] if isinstance(s, dict)]
        texts = [s["text"] for s in segments if isinstance(s.get("text"), str)]
        self.segment_texts = [_collapse(text) for text in texts]
        self.joined = _collapse(" ".join(texts))
        self.positions: list[tuple[int, int, str]] = []
        self.identities: set[tuple[str, tuple[Any, ...]]] = set()
        for segment in segments:
            text = segment.get("text") if isinstance(segment.get("text"), str) else ""
            for selector in _segment_selectors(segment):
                stype = selector.get("type")
                if stype == "TextPositionSelector":
                    start, end = selector.get("start"), selector.get("end")
                    if isinstance(start, int) and isinstance(end, int):
                        self.positions.append((start, end, text))
                elif stype in IDENTITY_FIELDS:
                    self.identities.add((stype, tuple(selector.get(f) for f in IDENTITY_FIELDS[stype])))

    def contains(self, quote: str) -> bool:
        needle = _collapse(quote)
        if not needle:
            return False
        return any(needle in text for text in self.segment_texts) or needle in self.joined


def _check_evidence(evidence: dict[str, Any], index: _Index) -> tuple[str, str]:
    selector = evidence.get("selector") if isinstance(evidence.get("selector"), dict) else {}
    stype = selector.get("type")
    excerpt = evidence.get("excerpt")
    if isinstance(excerpt, str) and excerpt and stype != "TextPositionSelector":
        if not index.contains(excerpt):
            return "not_found", "excerpt does not occur in the normalized source"
    if stype == "TextQuoteSelector":
        exact = selector.get("exact")
        if isinstance(exact, str) and index.contains(exact):
            return "verified", "exact quote found"
        return "not_found", "exact quote does not occur in the normalized source"
    if stype == "TextPositionSelector":
        start, end = selector.get("start"), selector.get("end")
        for seg_start, seg_end, text in index.positions:
            if isinstance(start, int) and isinstance(end, int) and seg_start <= start <= end <= seg_end:
                if isinstance(excerpt, str) and excerpt:
                    if text[start - seg_start:end - seg_start] != excerpt:
                        return "not_found", "excerpt differs from the text at this position"
                return "verified", "position lies inside an extracted segment"
        return "not_found", "position lies outside every extracted text segment"
    if stype in IDENTITY_FIELDS:
        key = (stype, tuple(selector.get(f) for f in IDENTITY_FIELDS[stype]))
        if key in index.identities:
            return "verified", "selector equals an extracted segment selector"
        return "not_found", "selector matches no extracted segment selector"
    return "unverifiable", f"selector type {stype!r} is not produced by the local adapter"


def verify(graph: dict[str, Any], normalized: list[dict[str, Any]]) -> dict[str, Any]:
    by_digest = {_digest(doc["source"].get("content_sha256")): _Index(doc) for doc in normalized}
    sources = graph.get("metadata", {}).get("sources", []) if isinstance(graph.get("metadata"), dict) else []
    source_digest = {
        s.get("id"): _digest(s.get("content_sha256")) for s in sources if isinstance(s, dict)
    }
    results = []
    for evidence in graph.get("evidence", []) or []:
        if not isinstance(evidence, dict):
            continue
        digest = source_digest.get(evidence.get("source"))
        if digest is None:
            status, detail = "unverifiable", "graph source carries no content_sha256"
        elif digest not in by_digest:
            status, detail = "unverifiable", "no normalized source with this content_sha256 was supplied"
        else:
            status, detail = _check_evidence(evidence, by_digest[digest])
        results.append({"evidence": evidence.get("id"), "status": status, "detail": detail})
    counts = {name: sum(1 for r in results if r["status"] == name)
              for name in ("verified", "not_found", "unverifiable")}
    return {
        "counts": counts,
        "results": results,
        "semantic_support": {
            "evaluated": False,
            "scope": "anchor existence only; a found passage is not proof that it supports the claim",
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Resolve graph evidence against extract_source.py output")
    parser.add_argument("graph", help="path to the .knowledge.json")
    parser.add_argument("normalized", nargs="+", help="extract_source.py result(s), JSON or JSONL")
    parser.add_argument("--require-all", action="store_true",
                        help="also fail when any evidence record is unverifiable")
    parser.add_argument("--json", action="store_true", dest="as_json", help="emit a machine-readable report")
    args = parser.parse_args(argv)
    try:
        graph = _load_json(Path(args.graph))
        if not isinstance(graph, dict):
            raise VerificationError(f"{args.graph}: graph must be a JSON object")
        report = verify(graph, [load_normalized(Path(p)) for p in args.normalized])
    except (OSError, UnicodeError, VerificationError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    counts = report["counts"]
    if args.as_json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        for result in report["results"]:
            if result["status"] != "verified":
                print(f"{result['status'].upper():<13} {result['evidence']}: {result['detail']}")
        print(f"evidence: {counts['verified']} verified, {counts['not_found']} not found, "
              f"{counts['unverifiable']} unverifiable — anchors only, not semantic support")
    failed = counts["not_found"] or (args.require_all and counts["unverifiable"])
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
