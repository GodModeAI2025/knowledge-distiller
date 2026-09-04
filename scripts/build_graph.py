#!/usr/bin/env python3
"""Recompute the *derived* parts of a Knowledge Distiller graph deterministically.

Per ``SPEC.md`` §8, the model writes knowledge (definitions, relevance, statements) but all
derived artifacts MUST be computed by code so they can never drift:

* ``metadata.concept_count`` / ``relationship_count`` / ``cluster_count`` / ``fact_count``
* ``cluster.concepts[]`` (from each ``node.cluster``)
* canonical, order-stable ``edge.id`` (§8.1)
* ``metadata.conformance_score`` plus legacy ``quality_score`` alias
* the ``## Concept Map`` and ``## Wissensgraph (Mermaid)`` markdown sections

Usage:
    python3 scripts/build_graph.py <file.knowledge.json>             # report what would change
    python3 scripts/build_graph.py <file.knowledge.json> --write     # rewrite the JSON in place
    python3 scripts/build_graph.py <file.knowledge.json> --emit-md   # print Concept Map + Mermaid

Pure standard library.
"""
from __future__ import annotations

import argparse
import json
import os
import stat
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import strict_json  # noqa: E402
import validate_knowledge as vk  # noqa: E402

# type -> ("symbol phrase" for Concept Map, mermaid connector)
EDGE_DISPLAY = {
    "uses": "→ uses:",
    "enables": "→ enables:",
    "based-on": "→ based on:",
    "part-of": "→ part of:",
    "tension": "↔ tension with:",
    "replaces": "→ replaces:",
    "extends": "→ extends:",
    "example-of": "→ example of:",
}


def canonical_edge_id(edge: dict) -> str:
    """Order-stable id (§8.1): sort endpoints for symmetric `tension`, else keep direction."""
    s, t, ty = edge.get("source", ""), edge.get("target", ""), edge.get("type", "")
    if ty == "tension":
        a, b = sorted([s, t])
        return f"{a}__tension__{b}"
    return f"{s}__{ty}__{t}"


def recompute(doc: dict) -> dict:
    """Return a new doc with derived fields recomputed. Knowledge fields are untouched."""
    nodes = doc.get("nodes", []) or []
    edges = doc.get("edges", []) or []
    clusters = doc.get("clusters", []) or []
    facts = doc.get("facts", []) or []

    # cluster membership from node.cluster, preserving cluster order + node order
    members: dict[str, list[str]] = {c.get("id"): [] for c in clusters}
    for n in nodes:
        cid = n.get("cluster")
        if cid in members:
            members[cid].append(n.get("id"))
    for c in clusters:
        c["concepts"] = members.get(c.get("id"), [])

    # canonical edge ids
    for e in edges:
        e["id"] = canonical_edge_id(e)

    # count rollups
    meta = doc.setdefault("metadata", {})
    meta["concept_count"] = len(nodes)
    meta["relationship_count"] = len(edges)
    meta["cluster_count"] = len(clusters)
    meta["fact_count"] = len(facts)

    # Seed both compatibility fields before schema validation, then compute them from one
    # complete report. A second pass proves the computation is idempotent and avoids the old
    # first-build/self-reference ambiguity.
    meta["quality_score"] = 0
    meta["conformance_score"] = 0

    def full_report() -> vk.Report:
        report = vk.Report()
        ran_schema = vk.schema_validate(doc, report)
        core = vk.validate(doc, ran_schema=ran_schema, check_stored_scores=False)
        report.errors.extend(core.errors)
        report.warnings.extend(core.warnings)
        return report

    score = full_report().conformance_score()
    meta["conformance_score"] = score
    meta["quality_score"] = score  # compatibility alias; not semantic accuracy
    stable_score = full_report().conformance_score()
    meta["conformance_score"] = stable_score
    meta["quality_score"] = stable_score

    return doc


def render_concept_map(doc: dict) -> str:
    """Deterministic '## Concept Map' grouped by cluster (matches the .md house style)."""
    nodes = {n["id"]: n for n in (doc.get("nodes", []) or [])}
    out_by_node: dict[str, list[str]] = {nid: [] for nid in nodes}
    for e in doc.get("edges", []) or []:
        s = e.get("source")
        if s in out_by_node:
            phrase = EDGE_DISPLAY.get(e.get("type"), f"→ {e.get('type')}:")
            tlabel = nodes.get(e.get("target"), {}).get("label", e.get("target"))
            out_by_node[s].append(f"  - {phrase} [[{tlabel}]]")

    lines = ["## Concept Map", ""]
    for c in doc.get("clusters", []) or []:
        cid = c.get("id")
        members = c.get("concepts", []) or []
        if not members:
            continue
        lines.append(f"### 🏷️ {c.get('label', cid)}")
        lines.append("")
        for nid in members:
            n = nodes.get(nid)
            if not n:
                continue
            lines.append(f"- **[[{n.get('label', nid)}]]**")
            lines.extend(out_by_node.get(nid, []))
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_mermaid(doc: dict) -> str:
    """Deterministic '## Wissensgraph (Mermaid)' block."""
    nodes = {n["id"]: n for n in (doc.get("nodes", []) or [])}

    def mid(nid: str) -> str:
        return "n_" + str(nid).replace("-", "_")

    lines = ["## Wissensgraph (Mermaid)", "", "```mermaid", "graph LR"]
    seen = set()
    for e in doc.get("edges", []) or []:
        s, t, ty = e.get("source"), e.get("target"), e.get("type")
        if s not in nodes or t not in nodes:
            continue
        sl = nodes[s].get("label", s).replace('"', "'")
        tl = nodes[t].get("label", t).replace('"', "'")
        connector = "<-->" if ty == "tension" else "-->"
        line = f'    {mid(s)}["{sl}"] {connector}|{ty}| {mid(t)}["{tl}"]'
        if line not in seen:
            seen.add(line)
            lines.append(line)
    lines.append("```")
    return "\n".join(lines) + "\n"


def _diff_summary(before: dict, after: dict) -> list[str]:
    changes = []
    mb, ma = before.get("metadata", {}), after.get("metadata", {})
    for f in ("concept_count", "relationship_count", "cluster_count", "fact_count", "conformance_score", "quality_score"):
        if mb.get(f) != ma.get(f):
            changes.append(f"metadata.{f}: {mb.get(f)} -> {ma.get(f)}")
    for cb, ca in zip(before.get("clusters", []), after.get("clusters", [])):
        if cb.get("concepts") != ca.get("concepts"):
            changes.append(f"clusters[{ca.get('id')}].concepts recomputed ({len(ca.get('concepts', []))} members)")
    return changes


class GraphWriteError(ValueError):
    """The canonical graph cannot be replaced without risking another file."""


def _read_for_rewrite(path: Path) -> tuple[dict, os.stat_result]:
    """Read a regular non-symlink input and retain its identity for compare-before-swap."""
    try:
        declared = path.lstat()
    except OSError as exc:
        raise GraphWriteError(f"could not inspect canonical input safely: {exc}") from exc
    if stat.S_ISLNK(declared.st_mode) or not stat.S_ISREG(declared.st_mode):
        raise GraphWriteError("canonical input must be a regular non-symlink file")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise GraphWriteError(f"could not open canonical input safely: {exc}") from exc
    try:
        opened = os.fstat(fd)
        if not stat.S_ISREG(opened.st_mode):
            raise GraphWriteError("canonical input must be a regular non-symlink file")
        if (declared.st_dev, declared.st_ino) != (opened.st_dev, opened.st_ino):
            raise GraphWriteError("canonical input changed while it was being opened")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(fd, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
    finally:
        os.close(fd)
    try:
        document = strict_json.loads(b"".join(chunks), source=str(path))
    except strict_json.StrictJsonError as exc:
        raise GraphWriteError(str(exc)) from exc
    return document, opened


def _same_file_state(left: os.stat_result, right: os.stat_result) -> bool:
    return (
        left.st_dev,
        left.st_ino,
        left.st_size,
        left.st_mtime_ns,
        left.st_ctime_ns,
    ) == (
        right.st_dev,
        right.st_ino,
        right.st_size,
        right.st_mtime_ns,
        right.st_ctime_ns,
    )


def _atomic_rewrite(path: Path, payload: bytes, expected: os.stat_result) -> None:
    """Replace the selected path atomically only if the bytes read are still current."""
    try:
        current = path.lstat()
    except OSError as exc:
        raise GraphWriteError(f"canonical input disappeared before replacement: {exc}") from exc
    if stat.S_ISLNK(current.st_mode) or not stat.S_ISREG(current.st_mode):
        raise GraphWriteError("canonical input became a symlink or non-regular file")
    if not _same_file_state(expected, current):
        raise GraphWriteError("canonical input changed while it was being rebuilt")

    temp_name: str | None = None
    try:
        fd, temp_name = tempfile.mkstemp(prefix=".kd-graph-", suffix=".tmp", dir=path.parent)
        os.fchmod(fd, stat.S_IMODE(expected.st_mode))
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        current = path.lstat()
        if stat.S_ISLNK(current.st_mode) or not _same_file_state(expected, current):
            raise GraphWriteError("canonical input changed before atomic replacement")
        os.replace(temp_name, path)
        temp_name = None
    finally:
        if temp_name is not None:
            try:
                Path(temp_name).unlink()
            except FileNotFoundError:
                pass


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Recompute derived fields of a .knowledge.json")
    ap.add_argument("file")
    ap.add_argument("--write", action="store_true", help="rewrite the JSON in place")
    ap.add_argument("--emit-md", action="store_true", help="print Concept Map + Mermaid sections")
    args = ap.parse_args(argv)

    path = Path(args.file)
    input_state: os.stat_result | None = None
    try:
        if args.write:
            doc, input_state = _read_for_rewrite(path)
        else:
            doc = strict_json.load_path(path)
    except (strict_json.StrictJsonError, GraphWriteError) as e:
        print(f"{args.file}: could not read strict JSON safely: {e}", file=sys.stderr)
        return 1
    if not isinstance(doc, dict):
        print(f"{args.file}: graph root must be a JSON object", file=sys.stderr)
        return 1

    before = json.loads(json.dumps(doc))
    recompute(doc)

    if args.emit_md:
        print(render_concept_map(doc))
        print(render_mermaid(doc))
        return 0

    if args.write:
        assert input_state is not None
        payload = (
            json.dumps(doc, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        ).encode("utf-8")
        try:
            _atomic_rewrite(path, payload, input_state)
        except (OSError, GraphWriteError) as exc:
            print(f"{args.file}: refusing canonical rewrite: {exc}", file=sys.stderr)
            return 1
        print(f"{args.file}: rewritten. conformance_score = {doc['metadata']['conformance_score']}/100 (semantic accuracy not evaluated)")
        for c in _diff_summary(before, doc):
            print(f"  • {c}")
    else:
        changes = _diff_summary(before, doc)
        if changes:
            print(f"{args.file}: would change (run with --write):")
            for c in changes:
                print(f"  • {c}")
        else:
            print(f"{args.file}: already consistent. conformance_score = {doc['metadata']['conformance_score']}/100 (semantic accuracy not evaluated)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
