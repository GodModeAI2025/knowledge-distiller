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
import copy
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_graph as bg  # noqa: E402
import strict_json  # noqa: E402

REL = bg.EDGE_DISPLAY
OPTIONAL_OBJECT_COLLECTIONS = (
    "facts", "chunks", "evidence", "claims", "assessments", "fact_conflicts",
)
CREDENTIAL_QUERY_NAMES = {
    "accesskey", "apikey", "auth", "authorization", "awsaccesskeyid", "clientsecret",
    "credential", "jwt", "password", "passwd", "secret", "secretkey", "sessionid",
    "sig", "signature", "token",
}
CREDENTIAL_QUERY_SUFFIXES = ("token", "apikey", "secret", "password", "credential", "signature")


class RenderInputError(ValueError):
    """The strict JSON value cannot be consumed as a knowledge graph."""


def object_records(value) -> list[dict]:
    """Return only object records from an optional consumer collection."""
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def string_items(value) -> list[str]:
    return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []


def consumer_safe_copy(doc: dict) -> dict:
    """Copy a graph and skip malformed optional collection items for rendering."""
    if not isinstance(doc, dict):
        raise RenderInputError("graph root must be a JSON object")
    result = copy.deepcopy(doc)
    for key in OPTIONAL_OBJECT_COLLECTIONS:
        result[key] = object_records(result.get(key, []))
    if isinstance(result.get("open_questions"), list):
        result["open_questions"] = string_items(result["open_questions"])
    return result


def safe_link(url) -> str | None:
    """Return only browser-safe links; source data is always untrusted."""
    if not isinstance(url, str):
        return None
    try:
        parsed = urlsplit(url)
    except ValueError:
        return None
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        return None
    if parsed.username is not None or parsed.password is not None:
        return None
    for name, _ in parse_qsl(parsed.query, keep_blank_values=True):
        normalized = re.sub(r"[^a-z0-9]+", "", name.lower())
        if normalized in CREDENTIAL_QUERY_NAMES or normalized.endswith(CREDENTIAL_QUERY_SUFFIXES):
            return None
    return url


# The escaping policy for untrusted document text lives in build_graph so that the
# Markdown and Mermaid renderers cannot drift apart. build_md already imports
# build_graph; the reverse would be an import cycle.
md_text = bg.md_text
md_code = bg.md_code
md_url = bg.md_url
md_link = bg.md_link
md_wikilink = bg.md_wikilink


def escaped_cell(text: str) -> str:
    """Table-cell protection for text that is already escaped for Markdown."""
    return " ".join(str(text).replace("|", "\\|").splitlines())


def cell(value) -> str:
    """Escape an untrusted value for a Markdown table cell."""
    # ``value or ""`` is kept from the original so that falsy values still render empty;
    # only the escaping changes here.
    return escaped_cell(md_text(value or ""))


def spatial_label(contexts) -> str:
    items = []
    for context in object_records(contexts):
        place = context.get("place")
        place = place if isinstance(place, dict) else {}
        label = place.get("label") or place.get("id")
        if label:
            item = (f"{md_text(context.get('role', 'place'))}: {md_text(label)} "
                    f"({md_text(context.get('basis', 'unknown'))})")
            if context.get("confidence"):
                item += f" · confidence: {md_text(context['confidence'])}"
            if context.get("evidence"):
                item += f" · evidence: {evidence_refs(context['evidence'])}"
            derivation = context.get("derivation")
            if isinstance(derivation, dict) and derivation.get("summary"):
                item += f" · derivation: {md_text(derivation['summary'])}"
            items.append(item)
    return "; ".join(items)


def evidence_refs(ids) -> str:
    return ", ".join(f"`{md_code(identifier)}`" for identifier in string_items(ids)) or "—"


def json_value(value) -> str:
    """Render one value as exact JSON, preserving whitespace inside strings."""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def json_details(label: str, value) -> list[str]:
    """Return an indented JSON code block that cannot be closed by source text."""
    encoded = json.dumps(value, ensure_ascii=False, indent=2)
    return [f"**{label}:**", "", *(f"    {line}" for line in encoded.splitlines()), ""]


def y(v) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    s = str(v)
    # JSON double-quoted strings are valid YAML scalars and avoid the many semantic
    # traps of plain YAML (booleans, numbers, list punctuation and document markers).
    # Escape YAML's additional Unicode line/control characters while retaining normal
    # non-ASCII prose for readable generated Markdown.
    encoded = json.dumps(s, ensure_ascii=False)
    for codepoint in (*range(0x7F, 0xA0), 0x2028, 0x2029):
        encoded = encoded.replace(chr(codepoint), f"\\u{codepoint:04x}")
    return encoded


def zeitbezug(temporal: dict) -> str:
    if not isinstance(temporal, dict) or not temporal:
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
             f"conformance_score: {y(m.get('conformance_score', m.get('quality_score')))}",
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
            # Renderers surface a URL-valued property as a clickable link, so the same
            # scheme allowlist that governs the body has to govern the frontmatter.  A
            # rejected value is kept -- it is still evidence about the source -- but
            # under a key nothing will offer to open.
            safe = safe_link(s.get("url"))
            key = "url" if safe is not None else "url_unsafe"
            lines.append(f"    {key}: {y(safe if safe is not None else s.get('url'))}")
        for field in ("title", "publisher", "version", "retrieved_at", "content_sha256", "license"):
            if s.get(field) is not None:
                lines.append(f"    {field}: {y(s.get(field))}")
        if s.get("authors"):
            lines.append("    authors: [" + ", ".join(y(author) for author in s["authors"]) + "]")
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
    claims_by_node: dict[str, list] = {}
    for claim in object_records(doc.get("claims", [])):
        node_id = claim.get("node")
        if isinstance(node_id, str):
            claims_by_node.setdefault(node_id, []).append(claim)

    lines = ["## Kernwissen", ""]
    for n in doc.get("nodes", []) or []:
        nid = n["id"]
        lines.append(f"### {md_text(n.get('label', nid))}")
        lines.append("")
        lines.append(f"📊 Confidence: `{md_code(n.get('confidence',''))}` | "
                     f"🏷️ Cluster: {md_text(cluster_label.get(n.get('cluster'), n.get('cluster','')))} | "
                     f"📅 {md_text(zeitbezug(n.get('temporal', {})))}")
        if n.get("resource"):
            lines.append(f"🔗 Resource: `{md_code(n.get('resource'))}`")
        if n.get("sources"):
            lines.append(f"📚 Sources: {evidence_refs(n.get('sources'))}")
        if n.get("spatial_contexts"):
            lines.append(f"🌍 Spatial: {spatial_label(n.get('spatial_contexts'))}")
        lines.append("")
        lines.append(f"**Definition:** {md_text(n.get('definition',''))}")
        lines.append("")
        lines.append(f"**Warum relevant:** {md_text(n.get('relevance',''))}")
        lines.append("")
        rels = out_edges.get(nid, [])
        if rels:
            lines.append("**Beziehungen:**")
            for e in rels:
                phrase = REL.get(e.get("type"), f"→ {md_text(e.get('type'))}:")
                tl = nodes_by_id.get(e.get("target"), {}).get("label", e.get("target"))
                detail = ""
                if e.get("explanation"):
                    detail += (f" — {md_text(e.get('explanation'))} "
                               f"({md_text(e.get('origin', 'origin unknown'))})")
                if e.get("evidence"):
                    detail += f" — Evidence: {evidence_refs(e.get('evidence'))}"
                lines.append(f"- {phrase} {md_wikilink(tl)}{detail}")
            lines.append("")
        lines.append("**Kernaussagen:**")
        for st in n.get("statements", []) or []:
            lines.append(f"- {md_text(st)}")
            for claim in claims_by_node.get(nid, []):
                if claim.get("statement") == st:
                    lines.append(f"  - Claim `{md_code(claim.get('id'))}` · confidence "
                                 f"`{md_code(claim.get('confidence'))}` · "
                                 f"origin `{md_code(claim.get('origin'))}` · review "
                                 f"`{md_code(claim.get('review_status', 'unspecified'))}` · evidence "
                                 f"{evidence_refs(claim.get('evidence'))}")
        if n.get("note"):
            lines.extend(["", f"> {md_text(n.get('note'))}"])
        if n.get("evidence"):
            lines.extend(["", f"**Evidence:** {evidence_refs(n.get('evidence'))}"])
        if n.get("temporal"):
            lines.extend(["", *json_details("Temporal record", n.get("temporal"))])
        if n.get("spatial_contexts"):
            lines.extend(json_details("Spatial context records", n.get("spatial_contexts")))
        if n.get("citations"):
            lines.extend(json_details("Citation records", n.get("citations")))
        lines.append("")
        lines.append("---")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def facts_table(doc: dict) -> str:
    facts = object_records(doc.get("facts", []))
    if not facts:
        return ""
    src_n = {s.get("id"): i for i, s in enumerate(doc.get("metadata", {}).get("sources", []) or [], start=1)}
    lines = ["## Fakten & Daten", "", "| ID | Fakt | Wert | Kontext | Zeitbezug | Raum | Konfidenz | Quelle | Origin | Evidence |",
             "|----|------|------|---------|-----------|------|-----------|--------|--------|----------|"]
    for f in facts:
        t = f.get("temporal")
        t = t if isinstance(t, dict) else {}
        when = t.get("valid_from") or t.get("source_period") or t.get("source_date") or ""
        source_id = f.get("source")
        n = src_n.get(source_id, "") if isinstance(source_id, str) else ""
        explanation = f.get("explanation") or ""
        context = f.get("context") or explanation
        lines.append(f"| {cell(f.get('id'))} | {cell(f.get('statement'))} | {cell(f.get('value'))} | {cell(context)} | {cell(when)} | "
                     f"{escaped_cell(spatial_label(f.get('spatial_contexts')))} | {cell(f.get('confidence'))} | [{n}] | "
                     f"{cell(f.get('origin'))} | {cell(', '.join(string_items(f.get('evidence'))))} |")
    lines.extend(["", "### Fact provenance", ""])
    for fact in facts:
        lines.extend([
            f"#### `{md_code(fact.get('id'))}`",
            "",
            f"- Source: `{md_code(fact.get('source'))}`",
            f"- Concept: {json_value(fact.get('concept'))}",
            f"- Metric: {json_value(fact.get('metric'))}",
            f"- Confidence: `{md_code(fact.get('confidence'))}`",
            f"- Origin: `{md_code(fact.get('origin', 'unspecified'))}`",
            f"- Evidence: {evidence_refs(fact.get('evidence'))}",
            "",
        ])
        if fact.get("explanation") is not None:
            lines.extend(json_details("Explanation", fact.get("explanation")))
        if fact.get("temporal") is not None:
            lines.extend(json_details("Temporal record", fact.get("temporal")))
        if fact.get("spatial_contexts") is not None:
            lines.extend(json_details("Spatial context records", fact.get("spatial_contexts")))
        if fact.get("derivation") is not None:
            lines.extend(json_details("Derivation", fact.get("derivation")))
    return "\n".join(lines) + "\n"


def open_questions(doc: dict) -> str:
    qs = doc.get("open_questions", []) or []
    if not qs:
        return ""
    lines = ["## Offene Fragen", ""]
    for q in qs:
        lines.append(f"- {md_text(q)}")
    return "\n".join(lines) + "\n"


def chunks_section(doc: dict) -> str:
    chs = object_records(doc.get("chunks", []))
    if not chs:
        return ""
    lines = ["## Chunks (Embedding-optimiert)", ""]
    for ch in chs:
        kind = ch.get("kind", "source_claims")
        default = ch.get("include_in_default_retrieval", kind != "inference")
        lines.append(f"### `{md_code(ch.get('id', 'chunk'))}`")
        lines.append("")
        lines.append(f"- Kind: `{md_code(kind)}`")
        lines.append(f"- Concepts: {evidence_refs(ch.get('concepts'))}")
        lines.append(f"- Temporal scope: {json_value(ch.get('temporal_scope'))}")
        lines.append(f"- Token estimate: {json_value(ch.get('token_estimate'))}")
        lines.append(f"- Origin: `{md_code(ch.get('origin', 'unspecified'))}`")
        lines.append(f"- Evidence: {evidence_refs(ch.get('evidence'))}")
        lines.append(f"- Include in default retrieval: `{str(bool(default)).lower()}`")
        lines.append("")
        if ch.get("spatial_contexts"):
            lines.append(f"Spatial summary: {spatial_label(ch.get('spatial_contexts'))}")
            lines.append("")
        lines.extend(json_details("Text", ch.get("text", "")))
        if ch.get("spatial_contexts") is not None:
            lines.extend(json_details("Spatial context records", ch.get("spatial_contexts")))
        if ch.get("derivation") is not None:
            lines.extend(json_details("Derivation", ch.get("derivation")))
    return "\n".join(lines).rstrip() + "\n"


def quellen(doc: dict) -> str:
    lines = ["## Quellen", ""]
    metadata = doc.get("metadata") if isinstance(doc.get("metadata"), dict) else {}
    for i, s in enumerate(object_records(metadata.get("sources", [])), start=1):
        label = s.get("file", s.get("id"))
        url = safe_link(s.get("url"))
        # ``safe_link`` decides WHETHER a link may be emitted; it does not make the
        # label or the target safe to interpolate.  A label of ``x](javascript:...)[y``
        # closed the brackets and produced a working link of its own.
        link = md_link(label, url) if url else md_text(label)
        extra = ", ".join(md_text(x) for x in [s.get("type"), s.get("date"), s.get("publisher"), s.get("content_sha256")] if x)
        lines.append(f"### [{i}] {link}")
        lines.append("")
        lines.append(f"- ID: `{md_code(s.get('id'))}`")
        if extra:
            lines.append(f"- Summary: {extra}")
        for field in ("title", "version", "retrieved_at", "license"):
            if s.get(field) is not None:
                lines.append(f"- {field}: {json_value(s.get(field))}")
        lines.append("")
        if s.get("authors") is not None:
            lines.extend(json_details("Authors", s.get("authors")))
        if s.get("agents") is not None:
            lines.extend(json_details("Source agents", s.get("agents")))
    return "\n".join(lines) + "\n"


def evidence_section(doc: dict) -> str:
    records = object_records(doc.get("evidence", []))
    if not records:
        return ""
    lines = ["## Evidence", ""]
    for record in records:
        lines.extend([
            f"### `{md_code(record.get('id'))}`",
            "",
            f"- Source: `{md_code(record.get('source'))}`",
            f"- Support: `{md_code(record.get('support'))}`",
            f"- Attribution basis: `{md_code(record.get('attribution_basis'))}`",
            f"- Review status: `{md_code(record.get('review_status', 'unspecified'))}`",
            "",
        ])
        lines.extend(json_details("Selector", record.get("selector")))
        if record.get("excerpt") is not None:
            lines.extend(json_details("Excerpt", record.get("excerpt")))
        if record.get("excerpt_sha256") is not None:
            lines.append(f"**Excerpt SHA-256:** `{md_code(record.get('excerpt_sha256'))}`")
            lines.append("")
        if record.get("derivation") is not None:
            lines.extend(json_details("Derivation", record.get("derivation")))
    return "\n".join(lines) + "\n"


def claims_section(doc: dict) -> str:
    records = object_records(doc.get("claims", []))
    if not records:
        return ""
    lines = ["## Claims", ""]
    for record in records:
        lines.extend([
            f"### `{md_code(record.get('id'))}`",
            "",
            f"- Node: `{md_code(record.get('node'))}`",
            f"- Confidence: `{md_code(record.get('confidence'))}`",
            f"- Origin: `{md_code(record.get('origin'))}`",
            f"- Review status: `{md_code(record.get('review_status', 'unspecified'))}`",
            f"- Evidence: {evidence_refs(record.get('evidence'))}",
            "",
        ])
        lines.extend(json_details("Statement", record.get("statement")))
        if record.get("temporal") is not None:
            lines.extend(json_details("Temporal record", record.get("temporal")))
        if record.get("spatial_contexts") is not None:
            lines.extend(json_details("Spatial context records", record.get("spatial_contexts")))
        if record.get("derivation") is not None:
            lines.extend(json_details("Derivation", record.get("derivation")))
    return "\n".join(lines) + "\n"


def edges_section(doc: dict) -> str:
    records = object_records(doc.get("edges", []))
    if not records:
        return ""
    lines = ["## Relationship provenance", ""]
    for record in records:
        edge_id = record.get("id") or bg.canonical_edge_id(record)
        lines.extend([
            f"### `{edge_id}`",
            "",
            f"- Endpoints: `{md_code(record.get('source'))}` → `{md_code(record.get('target'))}`",
            f"- Type: `{md_code(record.get('type'))}`",
            f"- Weight: `{md_code(record.get('weight'))}`",
            f"- Confidence: `{md_code(record.get('confidence'))}`",
            f"- Origin: `{md_code(record.get('origin', 'unspecified'))}`",
            f"- Evidence: {evidence_refs(record.get('evidence'))}",
            "",
        ])
        for label, field in (("Label", "label"), ("Explanation", "explanation")):
            if record.get(field) is not None:
                lines.extend(json_details(label, record.get(field)))
        if record.get("temporal") is not None:
            lines.extend(json_details("Temporal record", record.get("temporal")))
        if record.get("spatial_contexts") is not None:
            lines.extend(json_details("Spatial context records", record.get("spatial_contexts")))
        if record.get("derivation") is not None:
            lines.extend(json_details("Derivation", record.get("derivation")))
    return "\n".join(lines) + "\n"


def assessments_section(doc: dict) -> str:
    records = object_records(doc.get("assessments", []))
    if not records:
        return ""
    lines = ["## Assessments", "", "| ID | Dimension | Scope | Value | Assessor | Method | Assessed at | Evidence |",
             "|----|-----------|-------|-------|----------|--------|-------------|----------|"]
    for record in records:
        lines.append("| " + " | ".join(cell(value) for value in (
            record.get("id"), record.get("dimension"), record.get("scope"), record.get("value"),
            record.get("assessor"), record.get("method"), record.get("assessed_at"),
            ", ".join(string_items(record.get("evidence"))),
        )) + " |")
    return "\n".join(lines) + "\n"


def conflicts_section(doc: dict) -> str:
    records = object_records(doc.get("fact_conflicts", []))
    if not records:
        return ""
    lines = ["## Fact Conflicts", ""]
    for record in records:
        lines.append(f"- `{md_code(record.get('id'))}`: `{md_code(record.get('relation'))}` between "
                     f"{', '.join('`' + item + '`' for item in string_items(record.get('facts')))} — "
                     f"{record.get('reason')} — Evidence: {evidence_refs(record.get('evidence'))}")
    return "\n".join(lines) + "\n"


def render(doc: dict) -> str:
    doc = consumer_safe_copy(doc)
    bg.recompute(doc)
    m = doc.get("metadata", {})
    parts = [frontmatter(doc), "",
             bg.render_concept_map(doc), "",
             "---", "",
             kernwissen(doc), "",
             bg.render_mermaid(doc), ""]
    edges = edges_section(doc)
    if edges:
        parts += [edges, ""]
    ft = facts_table(doc)
    if ft:
        parts += [ft, ""]
    oq = open_questions(doc)
    if oq:
        parts += [oq, ""]
    ch = chunks_section(doc)
    if ch:
        parts += [ch, ""]
    ev = evidence_section(doc)
    if ev:
        parts += [ev, ""]
    claims = claims_section(doc)
    if claims:
        parts += [claims, ""]
    assessments = assessments_section(doc)
    if assessments:
        parts += [assessments, ""]
    conflicts = conflicts_section(doc)
    if conflicts:
        parts += [conflicts, ""]
    parts += [quellen(doc), "",
              "---", "",
              f"> Destilliert am {md_text(m.get('distillation_date',''))} mit Knowledge Distiller "
              f"v{md_text(m.get('distiller_version','4.0'))} (Spec {md_text(m.get('distiller_spec_version','1.0'))})",
              f"> Conformance-Score: {md_text(m.get('conformance_score', m.get('quality_score','?')))}/100",
              "> Semantische Richtigkeit: nicht durch den Validator bewertet", ""]
    return "\n".join(parts)


class MarkdownOutputError(ValueError):
    """The requested derived Markdown target is not safe for this input."""


def _output_path(input_path: Path, explicit_output: str | None) -> Path:
    """Resolve a distinct Markdown target and never fall back to the input name."""
    if explicit_output is None:
        if not input_path.name.endswith(".knowledge.json"):
            raise MarkdownOutputError(
                "input must end with .knowledge.json when --out is omitted"
            )
        output = input_path.with_name(
            input_path.name.removesuffix(".knowledge.json") + ".knowledge.md"
        )
    else:
        output = Path(explicit_output)

    if output.suffix.lower() != ".md":
        raise MarkdownOutputError("output must end with .md")
    if output.is_symlink():
        raise MarkdownOutputError(f"output is a symlink; refusing to replace it: {output}")
    if output.exists() and not output.is_file():
        raise MarkdownOutputError(f"output is not a regular file: {output}")

    try:
        same_target = output.resolve(strict=False) == input_path.resolve(strict=True)
        if output.exists():
            same_target = same_target or os.path.samefile(input_path, output)
    except OSError as exc:
        raise MarkdownOutputError(f"could not validate output target: {exc}") from exc
    if same_target:
        raise MarkdownOutputError("output must be different from the input file")
    return output


def _write_markdown(output: Path, content: str) -> None:
    """Atomically replace one validated Markdown target."""
    parent = output.parent
    if not parent.exists() or not parent.is_dir():
        raise MarkdownOutputError(f"output directory does not exist: {parent}")
    temp_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=parent,
            prefix=".kd-markdown-",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temp_name = handle.name
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_name, 0o644)
        os.replace(temp_name, output)
        temp_name = None
    finally:
        if temp_name is not None:
            try:
                Path(temp_name).unlink()
            except FileNotFoundError:
                pass


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Render .knowledge.md from .knowledge.json")
    ap.add_argument("file")
    ap.add_argument("-o", "--out")
    args = ap.parse_args(argv)
    path = Path(args.file)
    try:
        out = _output_path(path, args.out)
        doc = strict_json.load_path(path)
        _write_markdown(out, render(doc))
    except (MarkdownOutputError, RenderInputError, strict_json.StrictJsonError, OSError) as e:
        print(f"{args.file}: refusing Markdown build: {e}", file=sys.stderr)
        return 2
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
