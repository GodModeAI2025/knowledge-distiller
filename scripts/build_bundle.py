#!/usr/bin/env python3
"""Explode a monolithic ``.knowledge.json`` into a scalable directory **bundle** (SPEC §7).

A bundle is one markdown file per concept plus deterministically generated ``index.md``
manifests at every level — giving clean git diffs, per-concept atomic edits and progressive
disclosure for large corpora.

Layout produced:

    <out>/
    ├── index.md                 # bundle manifest (the only file with frontmatter)
    ├── knowledge.json           # the canonical compiled graph (source of truth)
    ├── concepts/
    │   ├── index.md             # lists clusters
    │   └── <cluster-id>/
    │       ├── index.md         # lists concepts, reusing each definition verbatim
    │       └── <node-id>.md     # one concept + "# Citations"
    └── facts.md                 # fact table (if facts exist)

Usage:
    python3 scripts/build_bundle.py <file.knowledge.json> [-o <out-dir>]

Pure standard library.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_graph as bg  # noqa: E402

REL_PHRASE = bg.EDGE_DISPLAY


def _yaml_scalar(v) -> str:
    if v is None:
        return "null"
    if isinstance(v, (int, float)):
        return str(v)
    s = str(v)
    if s == "" or any(ch in s for ch in ":#[]{}&*!|>'\"%@`") or s.strip() != s:
        return '"' + s.replace('"', '\\"') + '"'
    return s


def _sources_index(meta: dict) -> dict:
    """source-id -> (1-based number, source dict)."""
    out = {}
    for i, s in enumerate(meta.get("sources", []) or [], start=1):
        out[s.get("id")] = (i, s)
    return out


def _citation_lines(node: dict, src_idx: dict) -> list[str]:
    lines = ["# Citations", ""]
    cites = node.get("citations") or []
    if cites:
        for c in sorted(cites, key=lambda x: x.get("n", 0)):
            n, sid = c.get("n"), c.get("source")
            label = c.get("label") or (src_idx.get(sid, (0, {}))[1].get("file", sid))
            url = c.get("url") or src_idx.get(sid, (0, {}))[1].get("url")
            loc = f" — {c.get('locator')}" if c.get("locator") else ""
            link = f"[{label}]({url})" if url else label
            lines.append(f"[{n}] {link}{loc}")
    else:
        # fall back to the provenance sources
        for sid in node.get("sources", []) or []:
            n, s = src_idx.get(sid, (0, {}))
            label = s.get("file", sid)
            url = s.get("url")
            link = f"[{label}]({url})" if url else label
            extra = ", ".join(x for x in [s.get("type"), s.get("date")] if x)
            lines.append(f"[{n}] {link}" + (f" — {extra}" if extra else ""))
        if not (node.get("sources")):
            lines.append("_No sources recorded._")
    return lines


def concept_md(node: dict, edges: list, nodes_by_id: dict, src_idx: dict) -> str:
    nid = node.get("id")
    fm = ["---", "type: Concept", f"id: {_yaml_scalar(nid)}", f"label: {_yaml_scalar(node.get('label'))}",
          f"cluster: {_yaml_scalar(node.get('cluster'))}", f"confidence: {_yaml_scalar(node.get('confidence'))}"]
    if node.get("resource"):
        fm.append(f"resource: {_yaml_scalar(node.get('resource'))}")
    temp = node.get("temporal", {}) or {}
    fm.append("temporal:")
    for k in ("source_date", "source_period", "valid_from", "valid_until", "temporal_confidence"):
        if k in temp:
            fm.append(f"  {k}: {_yaml_scalar(temp.get(k))}")
    if node.get("sources"):
        fm.append("sources: [" + ", ".join(_yaml_scalar(s) for s in node["sources"]) + "]")
    fm.append("---")

    body = ["", f"# {node.get('label', nid)}", "",
            f"**Definition:** {node.get('definition', '')}", "",
            f"**Warum relevant:** {node.get('relevance', '')}", ""]
    # relationships (outgoing)
    rels = []
    for e in edges:
        if e.get("source") == nid:
            phrase = REL_PHRASE.get(e.get("type"), f"→ {e.get('type')}:")
            tl = nodes_by_id.get(e.get("target"), {}).get("label", e.get("target"))
            rels.append(f"- {phrase} [[{tl}]]")
    if rels:
        body.append("## Beziehungen")
        body.append("")
        body.extend(rels)
        body.append("")
    # statements
    body.append("## Kernaussagen")
    body.append("")
    for st in node.get("statements", []) or []:
        body.append(f"- {st}")
    body.append("")
    body.extend(_citation_lines(node, src_idx))
    body.append("")
    return "\n".join(fm + body)


def cluster_index_md(cluster: dict, nodes_by_id: dict) -> str:
    lines = [f"# {cluster.get('label', cluster.get('id'))}", ""]
    if cluster.get("description"):
        lines += [cluster["description"], ""]
    for nid in cluster.get("concepts", []) or []:
        n = nodes_by_id.get(nid)
        if not n:
            continue
        lines.append(f"* [{n.get('label', nid)}]({nid}.md) — {n.get('definition', '')}")
    return "\n".join(lines) + "\n"


def concepts_index_md(doc: dict) -> str:
    lines = ["# Concepts", ""]
    for c in doc.get("clusters", []) or []:
        cid = c.get("id")
        desc = f" — {c.get('description')}" if c.get("description") else ""
        n = len(c.get("concepts", []) or [])
        lines.append(f"* [{c.get('label', cid)}]({cid}/index.md){desc} ({n} concepts)")
    return "\n".join(lines) + "\n"


def root_index_md(doc: dict) -> str:
    meta = doc.get("metadata", {})
    fm = ["---", "type: Knowledge Bundle",
          f"title: {_yaml_scalar(meta.get('title'))}",
          f"distiller_version: {_yaml_scalar(meta.get('distiller_version'))}",
          f"distiller_spec_version: {_yaml_scalar(meta.get('distiller_spec_version'))}",
          f"distillation_date: {_yaml_scalar(meta.get('distillation_date'))}",
          f"domain: {_yaml_scalar(meta.get('domain'))}",
          f"quality_score: {_yaml_scalar(meta.get('quality_score'))}",
          f"concept_count: {_yaml_scalar(meta.get('concept_count'))}",
          f"relationship_count: {_yaml_scalar(meta.get('relationship_count'))}",
          f"cluster_count: {_yaml_scalar(meta.get('cluster_count'))}",
          "---", ""]
    body = [f"# {meta.get('title', 'Knowledge Bundle')}", "",
            f"> Distilled {meta.get('distillation_date', '')} · "
            f"{meta.get('concept_count', 0)} concepts · "
            f"{meta.get('relationship_count', 0)} relationships · "
            f"{meta.get('cluster_count', 0)} clusters · quality {meta.get('quality_score', '?')}/100", "",
            "* [Concepts](concepts/index.md)",
            "* [Canonical graph](knowledge.json)"]
    if doc.get("facts"):
        body.append("* [Facts](facts.md)")
    body.append("")
    body.append("## Sources")
    body.append("")
    for i, s in enumerate(meta.get("sources", []) or [], start=1):
        url = s.get("url")
        label = s.get("file", s.get("id"))
        link = f"[{label}]({url})" if url else label
        extra = ", ".join(x for x in [s.get("type"), s.get("date")] if x)
        body.append(f"[{i}] {link}" + (f" — {extra}" if extra else ""))
    return "\n".join(fm + body) + "\n"


def facts_md(doc: dict, src_idx: dict) -> str:
    lines = ["# Fakten & Daten", "", "| Fakt | Wert | Zeitbezug | Konfidenz | Quelle |",
             "|------|------|-----------|-----------|--------|"]
    for f in doc.get("facts", []) or []:
        t = f.get("temporal", {}) or {}
        when = t.get("valid_from") or t.get("source_period") or t.get("source_date") or ""
        n = src_idx.get(f.get("source"), (0, {}))[0]
        lines.append(f"| {f.get('statement','')} | {f.get('value','')} | {when} | {f.get('confidence','')} | [{n}] |")
    return "\n".join(lines) + "\n"


def build(doc: dict, out: Path) -> int:
    bg.recompute(doc)  # ensure rollups are correct before exploding
    nodes_by_id = {n["id"]: n for n in (doc.get("nodes", []) or [])}
    edges = doc.get("edges", []) or []
    src_idx = _sources_index(doc.get("metadata", {}))

    out.mkdir(parents=True, exist_ok=True)
    (out / "knowledge.json").write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (out / "index.md").write_text(root_index_md(doc), encoding="utf-8")

    concepts_dir = out / "concepts"
    concepts_dir.mkdir(exist_ok=True)
    (concepts_dir / "index.md").write_text(concepts_index_md(doc), encoding="utf-8")

    written = 0
    for c in doc.get("clusters", []) or []:
        cdir = concepts_dir / c.get("id")
        cdir.mkdir(exist_ok=True)
        (cdir / "index.md").write_text(cluster_index_md(c, nodes_by_id), encoding="utf-8")
        for nid in c.get("concepts", []) or []:
            n = nodes_by_id.get(nid)
            if not n:
                continue
            (cdir / f"{nid}.md").write_text(concept_md(n, edges, nodes_by_id, src_idx), encoding="utf-8")
            written += 1

    if doc.get("facts"):
        (out / "facts.md").write_text(facts_md(doc, src_idx), encoding="utf-8")

    print(f"bundle written to {out}/ — {written} concept files, "
          f"{len(doc.get('clusters', []))} clusters")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Explode a .knowledge.json into a directory bundle")
    ap.add_argument("file")
    ap.add_argument("-o", "--out", help="output bundle directory (default: <stem> next to input)")
    args = ap.parse_args(argv)

    path = Path(args.file)
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"{args.file}: could not read JSON: {e}", file=sys.stderr)
        return 1

    stem = path.name
    for suffix in (".knowledge.json", ".json"):
        if stem.endswith(suffix):
            stem = stem[: -len(suffix)]
            break
    out = Path(args.out) if args.out else path.parent / stem
    return build(doc, out)


if __name__ == "__main__":
    raise SystemExit(main())
