#!/usr/bin/env python3
"""Recompute the *derived* parts of a Knowledge Distiller graph deterministically.

Per ``SPEC.md`` §8, the model writes knowledge (definitions, relevance, statements) but all
derived artifacts MUST be computed by code so they can never drift:

* ``metadata.concept_count`` / ``relationship_count`` / ``cluster_count`` / ``fact_count``
* ``cluster.concepts[]`` (from each ``node.cluster``)
* canonical, order-stable ``edge.id`` (§8.1)
* ``metadata.quality_score`` (from ``validate_knowledge.py``)
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
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
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

    # quality score from the validator (schema + structural checks)
    ran_schema = vk.schema_validate(doc, vk.Report())  # detect availability without polluting
    rep = vk.validate(doc, ran_schema=ran_schema)
    if ran_schema:
        srep = vk.Report()
        vk.schema_validate(doc, srep)
        rep.errors = srep.errors + rep.errors
        rep.warnings = srep.warnings + rep.warnings
    meta["quality_score"] = rep.quality_score()

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
    for f in ("concept_count", "relationship_count", "cluster_count", "fact_count", "quality_score"):
        if mb.get(f) != ma.get(f):
            changes.append(f"metadata.{f}: {mb.get(f)} -> {ma.get(f)}")
    for cb, ca in zip(before.get("clusters", []), after.get("clusters", [])):
        if cb.get("concepts") != ca.get("concepts"):
            changes.append(f"clusters[{ca.get('id')}].concepts recomputed ({len(ca.get('concepts', []))} members)")
    return changes


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Recompute derived fields of a .knowledge.json")
    ap.add_argument("file")
    ap.add_argument("--write", action="store_true", help="rewrite the JSON in place")
    ap.add_argument("--emit-md", action="store_true", help="print Concept Map + Mermaid sections")
    args = ap.parse_args(argv)

    path = Path(args.file)
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"{args.file}: could not read JSON: {e}", file=sys.stderr)
        return 1

    before = json.loads(json.dumps(doc))
    recompute(doc)

    if args.emit_md:
        print(render_concept_map(doc))
        print(render_mermaid(doc))
        return 0

    if args.write:
        path.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"{args.file}: rewritten. quality_score = {doc['metadata']['quality_score']}/100")
        for c in _diff_summary(before, doc):
            print(f"  • {c}")
    else:
        changes = _diff_summary(before, doc)
        if changes:
            print(f"{args.file}: would change (run with --write):")
            for c in changes:
                print(f"  • {c}")
        else:
            print(f"{args.file}: already consistent. quality_score = {doc['metadata']['quality_score']}/100")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
