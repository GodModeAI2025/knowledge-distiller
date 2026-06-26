#!/usr/bin/env python3
"""Validate a Knowledge Distiller ``.knowledge.json`` against the format spec.

This is the deterministic replacement for the LLM "Validation-Agent": the format is
enforced by code, and ``metadata.quality_score`` is *computed* from the check results
rather than self-assigned by the model.

Checks are split into ERRORS (a non-conformant document) and WARNINGS (still conformant
but worth fixing), mirroring ``SPEC.md`` §9. With ``--prev`` it also enforces the merge
**monotonicity contract** (§6): a merged graph may never shrink.

Usage:
    python3 scripts/validate_knowledge.py <file.knowledge.json>
    python3 scripts/validate_knowledge.py --prev <old.json> <new.json>
    python3 scripts/validate_knowledge.py --md <file.knowledge.md> <file.knowledge.json>
    python3 scripts/validate_knowledge.py --json <file.knowledge.json>   # machine output

Exit code: 0 if there are no errors, 1 otherwise. Pure standard library; if the optional
``jsonschema`` package is installed it is additionally used for schema-level validation.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

EDGE_TYPES = {"uses", "enables", "based-on", "part-of", "tension", "replaces", "extends", "example-of"}
CONFIDENCE = {"high", "medium", "low"}
TEMPORAL_CONFIDENCE = {"explicit", "inferred", "unknown"}
KNOWN_SPEC_VERSIONS = {"1.0"}
KNOWN_TOP_KEYS = {"@context", "metadata", "clusters", "nodes", "edges", "facts", "chunks", "open_questions"}

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schema" / "knowledge.schema.json"

CITATION_MARKER = re.compile(r"\[(\d+)\]")
WIKILINK = re.compile(r"\[\[([^\]]+)\]\]")
EMOJI = re.compile(
    "[" "\U0001F000-\U0001FAFF" "\U00002600-\U000027BF" "\U0001F1E6-\U0001F1FF" "\U00002190-\U000021FF" "]"
)
# Syntax artifacts that must never appear in an embedding chunk.
CHUNK_FORBIDDEN = {
    "wikilink": re.compile(r"\[\[|\]\]"),
    "bold/italic": re.compile(r"\*\*|__"),
    "code fence/inline": re.compile(r"`"),
    "arrow": re.compile(r"→|↔|⟶|->|<->"),
    "heading marker": re.compile(r"(?m)^\s{0,3}#{1,6}\s"),
}


class Report:
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.warnings: list[str] = []

    def err(self, msg: str) -> None:
        self.errors.append(msg)

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)

    @property
    def ok(self) -> bool:
        return not self.errors

    def quality_score(self) -> int:
        """Deterministic score: 100, minus 10 per error and 2 per warning, floored at 0."""
        return max(0, 100 - 10 * len(self.errors) - 2 * len(self.warnings))


def _require(obj: dict, keys: list[str], where: str, rep: Report) -> None:
    for k in keys:
        if k not in obj:
            rep.err(f"{where}: missing required field '{k}'")


def schema_validate(doc: dict, rep: Report) -> bool:
    """Validate against the JSON Schema if jsonschema is installed. Returns True if it ran."""
    try:
        import jsonschema  # type: ignore
    except Exception:
        return False
    try:
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    except Exception as e:  # pragma: no cover - schema should always be present
        rep.warn(f"schema: could not load {SCHEMA_PATH.name} ({e}); skipping schema validation")
        return False
    validator = jsonschema.Draft202012Validator(schema)
    for e in sorted(validator.iter_errors(doc), key=lambda x: list(x.path)):
        path = "/".join(str(p) for p in e.path) or "<root>"
        rep.err(f"schema[{path}]: {e.message}")
    return True


def validate(doc: dict, prev: dict | None = None, ran_schema: bool = False) -> Report:
    rep = Report()

    if not isinstance(doc, dict):
        rep.err("<root>: document must be a JSON object")
        return rep

    # --- top-level structure ---
    _require(doc, ["metadata", "clusters", "nodes", "edges"], "<root>", rep)
    for k in doc:
        if k not in KNOWN_TOP_KEYS:
            rep.warn(f"<root>: unknown extra key '{k}' (tolerated by consumers)")

    meta = doc.get("metadata", {}) or {}
    clusters = doc.get("clusters", []) or []
    nodes = doc.get("nodes", []) or []
    edges = doc.get("edges", []) or []
    facts = doc.get("facts", []) or []
    chunks = doc.get("chunks", []) or []

    # --- metadata (manual checks; redundant-but-cheap when schema also ran) ---
    if not ran_schema:
        _require(
            meta,
            ["title", "distiller_version", "distiller_spec_version", "sources",
             "distillation_date", "domain", "language", "depth", "mode",
             "quality_score", "concept_count", "relationship_count", "cluster_count"],
            "metadata", rep,
        )
        if meta.get("distiller_version") not in (None, "4.0"):
            rep.err(f"metadata.distiller_version: expected '4.0', got {meta.get('distiller_version')!r}")

    spec_v = meta.get("distiller_spec_version")
    if spec_v is not None and spec_v not in KNOWN_SPEC_VERSIONS:
        rep.warn(f"metadata.distiller_spec_version: unknown version {spec_v!r} (best-effort consumption)")

    # --- source registry ---
    source_ids: set[str] = set()
    sources = meta.get("sources", []) or []
    for i, s in enumerate(sources):
        sid = (s or {}).get("id")
        if not sid:
            rep.err(f"metadata.sources[{i}]: missing 'id'")
        else:
            if sid in source_ids:
                rep.err(f"metadata.sources[{i}]: duplicate source id {sid!r}")
            source_ids.add(sid)
    n_sources = len(sources)

    # --- clusters ---
    cluster_ids: set[str] = set()
    cluster_concepts: dict[str, list[str]] = {}
    for i, c in enumerate(clusters):
        cid = (c or {}).get("id")
        if not cid:
            rep.err(f"clusters[{i}]: missing 'id'")
            continue
        if cid in cluster_ids:
            rep.err(f"clusters[{i}]: duplicate cluster id {cid!r}")
        cluster_ids.add(cid)
        cluster_concepts[cid] = list(c.get("concepts", []) or [])
        if not cluster_concepts[cid]:
            rep.warn(f"clusters[{cid}]: has no concepts")

    # --- nodes ---
    node_ids: set[str] = set()
    node_label = {}
    for i, n in enumerate(nodes):
        nid = (n or {}).get("id")
        where = f"nodes[{nid or i}]"
        if not nid:
            rep.err(f"{where}: missing 'id'")
            continue
        if nid in node_ids:
            rep.err(f"{where}: duplicate node id {nid!r}")
        node_ids.add(nid)
        node_label[nid] = n.get("label", nid)
        if not ran_schema:
            _require(n, ["label", "cluster", "confidence", "definition", "relevance",
                         "statements", "temporal", "sources"], where, rep)
            if n.get("confidence") not in CONFIDENCE:
                rep.err(f"{where}.confidence: {n.get('confidence')!r} not in {sorted(CONFIDENCE)}")
        # cluster resolves
        nc = n.get("cluster")
        if nc and nc not in cluster_ids:
            rep.err(f"{where}.cluster: {nc!r} does not resolve to a declared cluster")
        elif nc and nid not in cluster_concepts.get(nc, []):
            rep.warn(f"{where}: not listed in cluster {nc!r}.concepts (run build_graph to fix)")
        # provenance resolves
        for sid in n.get("sources", []) or []:
            if sid not in source_ids:
                rep.err(f"{where}.sources: {sid!r} does not resolve to metadata.sources")
        # temporal
        temp = n.get("temporal", {}) or {}
        if not ran_schema and "source_date" not in temp:
            rep.err(f"{where}.temporal: missing 'source_date'")
        if temp.get("temporal_confidence") not in TEMPORAL_CONFIDENCE | {None}:
            rep.err(f"{where}.temporal.temporal_confidence: {temp.get('temporal_confidence')!r} invalid")
        # citations
        cited_ns = {c.get("n") for c in (n.get("citations") or [])}
        for c in (n.get("citations") or []):
            if c.get("source") not in source_ids:
                rep.err(f"{where}.citations: source {c.get('source')!r} does not resolve to metadata.sources")
        marker_ns = set()
        for st in n.get("statements", []) or []:
            for m in CITATION_MARKER.findall(st):
                marker_ns.add(int(m))
        for mn in marker_ns:
            if mn < 1 or mn > n_sources:
                rep.err(f"{where}: citation marker [{mn}] out of range (1..{n_sources} sources)")
        if not cited_ns and not marker_ns:
            rep.warn(f"{where}: has no citations")

    # --- edges ---
    for i, e in enumerate(edges):
        where = f"edges[{i}]"
        if not ran_schema:
            _require(e, ["source", "target", "type", "weight", "confidence"], where, rep)
        et = e.get("type")
        if et is not None and et not in EDGE_TYPES:
            rep.err(f"{where}.type: {et!r} not in the 8-type vocabulary")
        for end in ("source", "target"):
            v = e.get(end)
            if v is not None and v not in node_ids:
                rep.err(f"{where}.{end}: {v!r} does not resolve to a node")
        w = e.get("weight")
        if isinstance(w, (int, float)) and not (0 <= w <= 1):
            rep.err(f"{where}.weight: {w} outside [0,1]")

    # --- facts ---
    for i, f in enumerate(facts):
        where = f"facts[{(f or {}).get('id', i)}]"
        if not ran_schema:
            _require(f, ["id", "statement", "value", "confidence", "source"], where, rep)
        fs = f.get("source")
        if fs is not None and fs not in source_ids:
            rep.err(f"{where}.source: {fs!r} does not resolve to metadata.sources")

    # --- chunks: must be clean text ---
    for i, ch in enumerate(chunks):
        where = f"chunks[{(ch or {}).get('id', i)}]"
        text = ch.get("text", "") or ""
        for label, pat in CHUNK_FORBIDDEN.items():
            if pat.search(text):
                rep.err(f"{where}: embedding text contains a {label} artifact")
        if EMOJI.search(text):
            rep.err(f"{where}: embedding text contains an emoji")
        for cid in ch.get("concepts", []) or []:
            if cid not in node_ids:
                rep.warn(f"{where}.concepts: {cid!r} does not resolve to a node")

    # --- count rollups must match reality ---
    _check_count(meta, "concept_count", len(nodes), rep)
    _check_count(meta, "relationship_count", len(edges), rep)
    _check_count(meta, "cluster_count", len(clusters), rep)
    if "fact_count" in meta:
        _check_count(meta, "fact_count", len(facts), rep)

    # --- monotonicity (merge contract, §6) ---
    if prev is not None:
        _check_monotonic(prev, doc, rep)

    return rep


def _check_count(meta: dict, field: str, actual: int, rep: Report) -> None:
    declared = meta.get(field)
    if declared is not None and declared != actual:
        rep.err(f"metadata.{field}: declared {declared} but graph has {actual}")


def _citation_total(doc: dict) -> int:
    total = 0
    for n in doc.get("nodes", []) or []:
        total += len(n.get("citations") or [])
    return total


def _check_monotonic(prev: dict, new: dict, rep: Report) -> None:
    pairs = [
        ("nodes", len(prev.get("nodes", []) or []), len(new.get("nodes", []) or [])),
        ("edges", len(prev.get("edges", []) or []), len(new.get("edges", []) or [])),
        ("facts", len(prev.get("facts", []) or []), len(new.get("facts", []) or [])),
        ("citations", _citation_total(prev), _citation_total(new)),
    ]
    for name, before, after in pairs:
        if after < before:
            rep.err(f"merge: {name} shrank from {before} to {after} (violates monotonicity contract §6)")
    prev_node_ids = {n.get("id") for n in (prev.get("nodes", []) or [])}
    new_node_ids = {n.get("id") for n in (new.get("nodes", []) or [])}
    dropped = prev_node_ids - new_node_ids
    if dropped:
        rep.err(f"merge: dropped node(s) {sorted(dropped)} present in the prior graph (§6)")


def _print_human(path: str, rep: Report, score: int) -> None:
    for w in rep.warnings:
        print(f"  ⚠ WARNING  {w}")
    for e in rep.errors:
        print(f"  ✗ ERROR    {e}")
    status = "PASS" if rep.ok else "FAIL"
    print(f"\n{path}: {status}  —  {len(rep.errors)} error(s), {len(rep.warnings)} warning(s)  —  quality_score = {score}/100")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Validate a Knowledge Distiller .knowledge.json")
    ap.add_argument("file", help="path to the .knowledge.json to validate")
    ap.add_argument("--prev", help="prior .knowledge.json to enforce the merge monotonicity contract")
    ap.add_argument("--md", help="optional sibling .knowledge.md to check [[wikilinks]] against nodes")
    ap.add_argument("--json", action="store_true", dest="as_json", help="emit a machine-readable JSON report")
    ap.add_argument("--quiet", action="store_true", help="only print the summary line")
    args = ap.parse_args(argv)

    try:
        doc = json.loads(Path(args.file).read_text(encoding="utf-8"))
    except FileNotFoundError:
        print(f"{args.file}: file not found", file=sys.stderr)
        return 1
    except json.JSONDecodeError as e:
        print(f"{args.file}: invalid JSON: {e}", file=sys.stderr)
        return 1

    prev = None
    if args.prev:
        try:
            prev = json.loads(Path(args.prev).read_text(encoding="utf-8"))
        except Exception as e:
            print(f"{args.prev}: could not read prior graph: {e}", file=sys.stderr)
            return 1

    rep = Report()
    ran_schema = schema_validate(doc, rep)
    sub = validate(doc, prev=prev, ran_schema=ran_schema)
    rep.errors.extend(sub.errors)
    rep.warnings.extend(sub.warnings)

    # optional .md wikilink check
    if args.md:
        try:
            md = Path(args.md).read_text(encoding="utf-8")
            labels = {n.get("label") for n in (doc.get("nodes", []) or [])}
            ids = {n.get("id") for n in (doc.get("nodes", []) or [])}
            known = labels | ids
            for target in {m for m in WIKILINK.findall(md)}:
                if target not in known:
                    rep.warn(f"{args.md}: wikilink [[{target}]] has no matching node label/id")
        except Exception as e:
            rep.warn(f"{args.md}: could not read for wikilink check ({e})")

    score = rep.quality_score()

    if args.as_json:
        print(json.dumps({
            "file": args.file,
            "ok": rep.ok,
            "errors": rep.errors,
            "warnings": rep.warnings,
            "quality_score": score,
            "schema_checked": ran_schema,
        }, ensure_ascii=False, indent=2))
    elif args.quiet:
        status = "PASS" if rep.ok else "FAIL"
        print(f"{args.file}: {status} ({len(rep.errors)} errors, {len(rep.warnings)} warnings, score {score}/100)")
    else:
        _print_human(args.file, rep, score)

    return 0 if rep.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
