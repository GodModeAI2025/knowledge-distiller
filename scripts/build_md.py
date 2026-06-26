#!/usr/bin/env python3
"""Render the human/Obsidian ``.knowledge.md`` deterministically from a ``.knowledge.json``.

Per ``SPEC.md`` §5 + §8 the markdown view is *derived*: every section here is generated from
the canonical JSON (the model authors knowledge in the JSON; the layout is computed). This
keeps the ``.md`` and ``.json`` from ever drifting apart.

Usage:
    python3 scripts/build_md.py <file.knowledge.json> [-o <out.md>]

Pure standard library.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_graph as bg  # noqa: E402

REL = bg.EDGE_DISPLAY


def y(v) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    s = str(v)
    if s == "" or s.strip() != s or any(c in s for c in ':#[]{}&*!|>\'"%@`'):
        return '"' + s.replace('"', '\\"') + '"'
    return s


def zeitbezug(temporal: dict) -> str:
    if not temporal:
        return "—"
    parts = []
    vf, vu = temporal.get("valid_from"), temporal.get("valid_until")
    if vf:
        parts.append(f"Gültig ab {vf}" + (f" bis {vu}" if vu else ""))
    elif vu:
        parts.append(f"Gültig bis {vu}")
    sd = temporal.get("source_date") or temporal.get("source_period")
    tc = temporal.get("temporal_confidence")
    if sd:
        parts.append(f"Stand {sd}" + (f" ({tc})" if tc else ""))
    elif tc:
        parts.append(f"({tc})")
    return " · ".join(parts) if parts else "—"


def frontmatter(doc: dict) -> str:
    m = doc.get("metadata", {})
    cluster_label = {c.get("id"): c.get("label") for c in doc.get("clusters", []) or []}
    lines = ["---",
             f"title: {y(m.get('title'))}",
             f"distiller_version: {y(m.get('distiller_version'))}",
             f"distiller_spec_version: {y(m.get('distiller_spec_version'))}",
             f"distillation_date: {y(m.get('distillation_date'))}",
             f"domain: {y(m.get('domain'))}",
             f"language: {y(m.get('language'))}",
             f"depth: {y(m.get('depth'))}",
             f"mode: {y(m.get('mode'))}",
             f"quality_score: {y(m.get('quality_score'))}",
             f"temporal_confidence: {y(m.get('temporal_confidence'))}",
             f"concept_count: {y(m.get('concept_count'))}",
             f"relationship_count: {y(m.get('relationship_count'))}",
             f"cluster_count: {y(m.get('cluster_count'))}"]
    lines.append("sources:")
    for s in m.get("sources", []) or []:
        lines.append(f"  - id: {y(s.get('id'))}")
        lines.append(f"    file: {y(s.get('file'))}")
        lines.append(f"    type: {y(s.get('type'))}")
        if s.get("date") is not None:
            lines.append(f"    date: {y(s.get('date'))}")
        if s.get("url") is not None:
            lines.append(f"    url: {y(s.get('url'))}")
    lines.append("clusters:")
    for c in doc.get("clusters", []) or []:
        lines.append(f"  {y(c.get('id'))}:")
        lines.append(f"    label: {y(c.get('label'))}")
        concepts = ", ".join(y(x) for x in c.get("concepts", []) or [])
        lines.append(f"    concepts: [{concepts}]")
    lines.append("---")
    return "\n".join(lines)


def kernwissen(doc: dict) -> str:
    nodes_by_id = {n["id"]: n for n in (doc.get("nodes", []) or [])}
    cluster_label = {c.get("id"): c.get("label") for c in doc.get("clusters", []) or []}
    out_edges: dict[str, list] = {}
    for e in doc.get("edges", []) or []:
        out_edges.setdefault(e.get("source"), []).append(e)

    lines = ["## Kernwissen", ""]
    for n in doc.get("nodes", []) or []:
        nid = n["id"]
        lines.append(f"### {n.get('label', nid)}")
        lines.append("")
        lines.append(f"📊 Confidence: `{n.get('confidence','')}` | "
                     f"🏷️ Cluster: {cluster_label.get(n.get('cluster'), n.get('cluster',''))} | "
                     f"📅 {zeitbezug(n.get('temporal', {}))}")
        lines.append("")
        lines.append(f"**Definition:** {n.get('definition','')}")
        lines.append("")
        lines.append(f"**Warum relevant:** {n.get('relevance','')}")
        lines.append("")
        rels = out_edges.get(nid, [])
        if rels:
            lines.append("**Beziehungen:**")
            for e in rels:
                phrase = REL.get(e.get("type"), f"→ {e.get('type')}:")
                tl = nodes_by_id.get(e.get("target"), {}).get("label", e.get("target"))
                lines.append(f"- {phrase} [[{tl}]]")
            lines.append("")
        lines.append("**Kernaussagen:**")
        for st in n.get("statements", []) or []:
            lines.append(f"- {st}")
        lines.append("")
        lines.append("---")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def facts_table(doc: dict) -> str:
    facts = doc.get("facts", []) or []
    if not facts:
        return ""
    src_n = {s.get("id"): i for i, s in enumerate(doc.get("metadata", {}).get("sources", []) or [], start=1)}
    lines = ["## Fakten & Daten", "", "| Fakt | Wert | Zeitbezug | Konfidenz | Quelle |",
             "|------|------|-----------|-----------|--------|"]
    for f in facts:
        t = f.get("temporal", {}) or {}
        when = t.get("valid_from") or t.get("source_period") or t.get("source_date") or ""
        n = src_n.get(f.get("source"), "")
        lines.append(f"| {f.get('statement','')} | {f.get('value','')} | {when} | "
                     f"{f.get('confidence','')} | [{n}] |")
    return "\n".join(lines) + "\n"


def open_questions(doc: dict) -> str:
    qs = doc.get("open_questions", []) or []
    if not qs:
        return ""
    lines = ["## Offene Fragen", ""]
    for q in qs:
        lines.append(f"- {q}")
    return "\n".join(lines) + "\n"


def chunks_section(doc: dict) -> str:
    chs = doc.get("chunks", []) or []
    if not chs:
        return ""
    lines = ["## Chunks (Embedding-optimiert)", ""]
    for ch in chs:
        lines.append(f"> {ch.get('text','')}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def quellen(doc: dict) -> str:
    lines = ["## Quellen", ""]
    for i, s in enumerate(doc.get("metadata", {}).get("sources", []) or [], start=1):
        label = s.get("file", s.get("id"))
        url = s.get("url")
        link = f"[{label}]({url})" if url else label
        extra = ", ".join(x for x in [s.get("type"), s.get("date")] if x)
        lines.append(f"[{i}] {link}" + (f" — {extra}" if extra else ""))
    return "\n".join(lines) + "\n"


def render(doc: dict) -> str:
    bg.recompute(doc)
    m = doc.get("metadata", {})
    parts = [frontmatter(doc), "",
             bg.render_concept_map(doc), "",
             "---", "",
             kernwissen(doc), "",
             bg.render_mermaid(doc), ""]
    ft = facts_table(doc)
    if ft:
        parts += [ft, ""]
    oq = open_questions(doc)
    if oq:
        parts += [oq, ""]
    ch = chunks_section(doc)
    if ch:
        parts += [ch, ""]
    parts += [quellen(doc), "",
              "---", "",
              f"> Destilliert am {m.get('distillation_date','')} mit Knowledge Distiller "
              f"v{m.get('distiller_version','4.0')} (Spec {m.get('distiller_spec_version','1.0')})",
              f"> Qualitäts-Score: {m.get('quality_score','?')}/100", ""]
    return "\n".join(parts)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Render .knowledge.md from .knowledge.json")
    ap.add_argument("file")
    ap.add_argument("-o", "--out")
    args = ap.parse_args(argv)
    path = Path(args.file)
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"{args.file}: could not read JSON: {e}", file=sys.stderr)
        return 1
    out = Path(args.out) if args.out else path.with_name(path.name.replace(".knowledge.json", ".knowledge.md"))
    out.write_text(render(doc), encoding="utf-8")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
