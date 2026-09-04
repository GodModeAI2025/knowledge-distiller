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
import hashlib
import json
import os
import re
import sys
import tempfile
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_graph as bg  # noqa: E402
import build_md as md_renderer  # noqa: E402
import strict_json  # noqa: E402

REL_PHRASE = bg.EDGE_DISPLAY

_PLAIN_COMPONENT = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9_-])?$")
_GENERATED_PREFIX = "kd-bundle-"
_MAX_PLAIN_COMPONENT_BYTES = 120
_WINDOWS_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


class BundleSecurityError(ValueError):
    """The requested bundle layout could overwrite or escape unsafe paths."""


def _filesystem_key(component: str) -> str:
    """Portable collision key for case-insensitive, Unicode-normalising filesystems."""
    return unicodedata.normalize("NFC", component).casefold()


def _plain_component(identifier: str, reserved: set[str]) -> str | None:
    """Return an ID unchanged when it is a portable, non-reserved path component."""
    if not _PLAIN_COMPONENT.fullmatch(identifier):
        return None
    if len(identifier.encode("utf-8")) > _MAX_PLAIN_COMPONENT_BYTES:
        return None
    if identifier.casefold().startswith(_GENERATED_PREFIX):
        return None
    if identifier.split(".", 1)[0].upper() in _WINDOWS_RESERVED:
        return None
    if _filesystem_key(identifier) in reserved:
        return None
    return identifier


def _hashed_component(identifier: str, kind: str) -> str:
    """Return a deterministic fixed-size component for an arbitrary identifier."""
    digest = hashlib.sha256(identifier.encode("utf-8")).hexdigest()
    return f"{_GENERATED_PREFIX}{kind}-{digest}"


def _path_components(ids, *, kind: str, reserved=()) -> dict[str, str]:
    """Map IDs to deterministic, portable and collision-safe path components.

    Existing simple IDs are deliberately preserved. Unsafe IDs and every member of a
    case/Unicode collision group receive a SHA-256-based component. The generated
    namespace is excluded from plain IDs, and the final collision check makes even a
    theoretical digest collision fail closed rather than overwrite another artifact.
    """
    values = list(ids)
    kind_label = {"c": "cluster", "n": "node"}.get(kind, kind)
    seen: set[str] = set()
    for identifier in values:
        if not isinstance(identifier, str) or not identifier:
            raise BundleSecurityError(
                f"{kind_label} ID must be a non-empty string, got {identifier!r}"
            )
        if identifier in seen:
            raise BundleSecurityError(
                f"duplicate {kind_label} ID {identifier!r} would overwrite a bundle path"
            )
        seen.add(identifier)

    reserved_keys = {_filesystem_key(x) for x in reserved}
    preferred = {
        identifier: _plain_component(identifier, reserved_keys)
        for identifier in values
    }
    groups: dict[str, list[str]] = {}
    for identifier, component in preferred.items():
        if component is not None:
            groups.setdefault(_filesystem_key(component), []).append(identifier)

    result: dict[str, str] = {}
    for identifier in values:
        component = preferred[identifier]
        if component is None or len(groups.get(_filesystem_key(component), [])) > 1:
            component = _hashed_component(identifier, kind)
        result[identifier] = component

    output_keys: dict[str, str] = {}
    for identifier, component in result.items():
        key = _filesystem_key(component)
        other = output_keys.get(key)
        if key in reserved_keys or (other is not None and other != identifier):
            raise BundleSecurityError(
                f"{kind_label} IDs {other!r} and {identifier!r} map to the same bundle path"
            )
        output_keys[key] = identifier
    return result


def _validated_root(out: Path) -> Path:
    """Resolve and validate an output root without following a root-level symlink."""
    out = out.expanduser()
    if out.is_symlink():
        raise BundleSecurityError(f"output root is a symlink; refusing to write bundle: {out}")
    if out.exists() and not out.is_dir():
        raise BundleSecurityError(f"output root exists but is not a directory: {out}")
    return out.resolve(strict=False)


def _safe_target(root: Path, *parts: str) -> Path:
    """Construct a target and prove it stays under root without traversing symlinks."""
    candidate = root.joinpath(*parts)
    cursor = root
    for part in parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise BundleSecurityError(f"bundle target traverses a symlink: {cursor}")

    try:
        candidate.resolve(strict=False).relative_to(root)
    except ValueError as exc:
        raise BundleSecurityError(f"bundle target escapes output root: {candidate}") from exc
    return candidate


def _preflight(root: Path, directories: list[Path], files: list[Path]) -> None:
    """Validate the complete layout before the first output file is changed."""
    dir_keys: set[str] = set()
    file_keys: set[str] = set()

    for directory in directories:
        _safe_target(root, *directory.relative_to(root).parts)
        key = _filesystem_key(str(directory.relative_to(root)))
        if key in dir_keys:
            continue
        dir_keys.add(key)
        if directory.is_symlink():
            raise BundleSecurityError(f"bundle directory is a symlink: {directory}")
        if directory.exists() and not directory.is_dir():
            raise BundleSecurityError(f"bundle directory path is not a directory: {directory}")

    for target in files:
        _safe_target(root, *target.relative_to(root).parts)
        key = _filesystem_key(str(target.relative_to(root)))
        if key in file_keys or key in dir_keys:
            raise BundleSecurityError(f"multiple bundle artifacts would overwrite {target}")
        file_keys.add(key)
        if target.is_symlink():
            raise BundleSecurityError(f"bundle file is a symlink: {target}")
        if target.exists() and not target.is_file():
            raise BundleSecurityError(f"bundle file path is not a regular file: {target}")


def _ensure_directory(root: Path, directory: Path) -> None:
    target = _safe_target(root, *directory.relative_to(root).parts)
    target.mkdir(exist_ok=True)
    if target.is_symlink() or not target.is_dir():
        raise BundleSecurityError(f"could not create a safe bundle directory: {target}")


def _write_text(root: Path, target: Path, content: str) -> None:
    """Atomically replace a generated regular file without following a file symlink."""
    target = _safe_target(root, *target.relative_to(root).parts)
    if target.is_symlink():
        raise BundleSecurityError(f"bundle file became a symlink: {target}")
    if target.exists() and not target.is_file():
        raise BundleSecurityError(f"bundle file path is not a regular file: {target}")

    tmp_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=target.parent,
            prefix=".kd-bundle-", suffix=".tmp", delete=False,
        ) as handle:
            tmp_name = handle.name
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp_name, 0o644)
        os.replace(tmp_name, target)
        tmp_name = None
    finally:
        if tmp_name is not None:
            try:
                Path(tmp_name).unlink()
            except FileNotFoundError:
                pass


def _yaml_scalar(v) -> str:
    return md_renderer.y(v)


def _sources_index(meta: dict) -> dict:
    """source-id -> (1-based number, source dict)."""
    out = {}
    for i, s in enumerate(meta.get("sources", []) or [], start=1):
        out[s.get("id")] = (i, s)
    return out


def _safe_link(url) -> str | None:
    return md_renderer.safe_link(url)


def _cell(value) -> str:
    return str(value or "").replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def _json_details(label: str, value) -> list[str]:
    encoded = json.dumps(value, ensure_ascii=False, indent=2)
    return [f"**{label}:**", "", *(f"    {line}" for line in encoded.splitlines()), ""]


def _spatial_label(contexts) -> str:
    labels = []
    for context in md_renderer.object_records(contexts):
        place = context.get("place")
        place = place if isinstance(place, dict) else {}
        label = place.get("label") or place.get("id")
        if label:
            item = f"{context.get('role', 'place')}: {label} ({context.get('basis', 'unknown')})"
            derivation = context.get("derivation")
            if isinstance(derivation, dict) and derivation.get("summary"):
                item += f" · derivation: {derivation['summary']}"
            labels.append(item)
    return "; ".join(labels)


def _citation_lines(node: dict, src_idx: dict) -> list[str]:
    lines = ["# Citations", ""]
    cites = md_renderer.object_records(node.get("citations"))
    if cites:
        for c in sorted(
            cites, key=lambda x: x.get("n") if isinstance(x.get("n"), int) else 0
        ):
            n, sid = c.get("n"), c.get("source")
            source_record = src_idx.get(sid, (0, {}))[1] if isinstance(sid, str) else {}
            label = c.get("label") or source_record.get("file", sid)
            url = _safe_link(c.get("url") or source_record.get("url"))
            loc = f" — {c.get('locator')}" if c.get("locator") else ""
            link = f"[{label}]({url})" if url else label
            lines.append(f"[{n}] {link}{loc}")
            if c.get("selector") is not None:
                lines.extend(_json_details(f"Citation [{n}] selector", c.get("selector")))
    else:
        # fall back to the provenance sources
        for sid in md_renderer.string_items(node.get("sources")):
            n, s = src_idx.get(sid, (0, {}))
            label = s.get("file", sid)
            url = _safe_link(s.get("url"))
            link = f"[{label}]({url})" if url else label
            extra = ", ".join(x for x in [s.get("type"), s.get("date")] if x)
            lines.append(f"[{n}] {link}" + (f" — {extra}" if extra else ""))
        if not (node.get("sources")):
            lines.append("_No sources recorded._")
    return lines


def concept_md(node: dict, edges: list, nodes_by_id: dict, src_idx: dict,
               evidence_by_id: dict | None = None, claims_by_node: dict | None = None) -> str:
    evidence_by_id = evidence_by_id or {}
    claims_by_node = claims_by_node or {}
    nid = node.get("id")
    fm = ["---", "type: Concept", f"id: {_yaml_scalar(nid)}", f"label: {_yaml_scalar(node.get('label'))}",
          f"cluster: {_yaml_scalar(node.get('cluster'))}", f"confidence: {_yaml_scalar(node.get('confidence'))}"]
    if node.get("resource"):
        fm.append(f"resource: {_yaml_scalar(node.get('resource'))}")
    temp = node.get("temporal")
    temp = temp if isinstance(temp, dict) else {}
    fm.append("temporal:")
    for k in ("source_date", "source_period", "valid_from", "valid_until", "temporal_confidence"):
        if k in temp:
            fm.append(f"  {k}: {_yaml_scalar(temp.get(k))}")
    if node.get("sources"):
        fm.append("sources: [" + ", ".join(_yaml_scalar(s) for s in md_renderer.string_items(node["sources"])) + "]")
    if node.get("evidence"):
        fm.append("evidence: [" + ", ".join(_yaml_scalar(s) for s in md_renderer.string_items(node["evidence"])) + "]")
    fm.append("---")

    body = ["", f"# {node.get('label', nid)}", "",
            f"**Definition:** {node.get('definition', '')}", "",
            f"**Warum relevant:** {node.get('relevance', '')}", ""]
    if node.get("spatial_contexts"):
        body.extend([f"**Spatial:** {_spatial_label(node.get('spatial_contexts'))}", ""])
        body.extend(_json_details("Spatial context records", node.get("spatial_contexts")))
    if node.get("note"):
        body.extend([f"> {node.get('note')}", ""])
    # relationships (outgoing)
    rels = []
    for e in edges:
        if e.get("source") == nid:
            phrase = REL_PHRASE.get(e.get("type"), f"→ {e.get('type')}:")
            tl = nodes_by_id.get(e.get("target"), {}).get("label", e.get("target"))
            detail = ""
            if e.get("explanation"):
                detail += f" — {e.get('explanation')} ({e.get('origin', 'origin unknown')})"
            if e.get("evidence"):
                detail += " — Evidence: " + ", ".join(
                    f"`{item}`" for item in md_renderer.string_items(e["evidence"])
                )
            rels.append(f"- {phrase} [[{tl}]]{detail}")
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
        for claim in claims_by_node.get(nid, []):
            if claim.get("statement") == st:
                refs = ", ".join(
                    f"`{item}`" for item in md_renderer.string_items(claim.get("evidence"))
                ) or "—"
                body.append(f"  - Claim `{claim.get('id')}` · confidence `{claim.get('confidence')}` · "
                            f"origin `{claim.get('origin')}` · review "
                            f"`{claim.get('review_status', 'unspecified')}` · evidence {refs}")
                if claim.get("temporal") is not None:
                    body.extend(_json_details(f"Claim {claim.get('id')} temporal record", claim.get("temporal")))
                if claim.get("spatial_contexts") is not None:
                    body.extend(_json_details(f"Claim {claim.get('id')} spatial context records", claim.get("spatial_contexts")))
                if claim.get("derivation") is not None:
                    body.extend(_json_details(f"Claim {claim.get('id')} derivation", claim.get("derivation")))
    body.append("")
    if node.get("evidence"):
        body.extend(["## Evidence", ""])
        for identifier in md_renderer.string_items(node.get("evidence")):
            record = evidence_by_id.get(identifier, {})
            selector = record.get("selector")
            selector = selector if isinstance(selector, dict) else {}
            location = selector.get("exact") or selector.get("fragment") or selector.get("json_pointer") or selector.get("page") or selector.get("type")
            body.append(f"- `{identifier}` — source `{record.get('source', '?')}` — {record.get('support', '?')} — {_cell(location)}")
        body.append("")
    body.extend(_citation_lines(node, src_idx))
    body.append("")
    return "\n".join(fm + body)


def cluster_index_md(cluster: dict, nodes_by_id: dict, node_paths: dict[str, str] | None = None) -> str:
    node_paths = node_paths or {nid: nid for nid in nodes_by_id}
    lines = [f"# {cluster.get('label', cluster.get('id'))}", ""]
    if cluster.get("description"):
        lines += [cluster["description"], ""]
    for nid in cluster.get("concepts", []) or []:
        n = nodes_by_id.get(nid)
        if not n:
            continue
        lines.append(
            f"* [{n.get('label', nid)}]({node_paths[nid]}.md) — {n.get('definition', '')}"
        )
    return "\n".join(lines) + "\n"


def concepts_index_md(doc: dict, cluster_paths: dict[str, str] | None = None) -> str:
    cluster_paths = cluster_paths or {
        c.get("id"): c.get("id") for c in (doc.get("clusters", []) or [])
    }
    lines = ["# Concepts", ""]
    for c in doc.get("clusters", []) or []:
        cid = c.get("id")
        desc = f" — {c.get('description')}" if c.get("description") else ""
        n = len(c.get("concepts", []) or [])
        lines.append(
            f"* [{c.get('label', cid)}]({cluster_paths[cid]}/index.md){desc} ({n} concepts)"
        )
    return "\n".join(lines) + "\n"


def root_index_md(doc: dict) -> str:
    meta = doc.get("metadata", {})
    fm = ["---", "type: Knowledge Bundle",
          f"title: {_yaml_scalar(meta.get('title'))}",
          f"distiller_version: {_yaml_scalar(meta.get('distiller_version'))}",
          f"distiller_spec_version: {_yaml_scalar(meta.get('distiller_spec_version'))}",
          f"distillation_date: {_yaml_scalar(meta.get('distillation_date'))}",
          f"domain: {_yaml_scalar(meta.get('domain'))}",
          f"conformance_score: {_yaml_scalar(meta.get('conformance_score', meta.get('quality_score')))}",
          f"quality_score: {_yaml_scalar(meta.get('quality_score'))}",
          f"concept_count: {_yaml_scalar(meta.get('concept_count'))}",
          f"relationship_count: {_yaml_scalar(meta.get('relationship_count'))}",
          f"cluster_count: {_yaml_scalar(meta.get('cluster_count'))}",
          "---", ""]
    body = [f"# {meta.get('title', 'Knowledge Bundle')}", "",
            f"> Distilled {meta.get('distillation_date', '')} · "
            f"{meta.get('concept_count', 0)} concepts · "
            f"{meta.get('relationship_count', 0)} relationships · "
            f"{meta.get('cluster_count', 0)} clusters · conformance "
            f"{meta.get('conformance_score', meta.get('quality_score', '?'))}/100 "
            f"(semantic accuracy not evaluated)", "",
            "* [Concepts](concepts/index.md)",
            "* [Canonical graph](knowledge.json)"]
    if doc.get("facts"):
        body.append("* [Facts](facts.md)")
    if doc.get("evidence"):
        body.append("* [Evidence](evidence.md)")
    if doc.get("assessments"):
        body.append("* [Assessments](assessments.md)")
    if doc.get("fact_conflicts"):
        body.append("* [Fact conflicts](fact-conflicts.md)")
    body.append("")
    body.append("## Sources")
    body.append("")
    for i, s in enumerate(meta.get("sources", []) or [], start=1):
        url = _safe_link(s.get("url"))
        label = s.get("file", s.get("id"))
        link = f"[{label}]({url})" if url else label
        extra = ", ".join(x for x in [s.get("type"), s.get("date")] if x)
        body.append(f"[{i}] {link}" + (f" — {extra}" if extra else ""))
    return "\n".join(fm + body) + "\n"


def facts_md(doc: dict, src_idx: dict) -> str:
    lines = ["# Fakten & Daten", "", "| Fakt | Wert | Kontext | Zeitbezug | Raum | Konfidenz | Quelle | Evidence |",
             "|------|------|---------|-----------|------|-----------|--------|----------|"]
    for f in md_renderer.object_records(doc.get("facts", [])):
        t = f.get("temporal")
        t = t if isinstance(t, dict) else {}
        when = t.get("valid_from") or t.get("source_period") or t.get("source_date") or ""
        source_id = f.get("source")
        n = src_idx.get(source_id, (0, {}))[0] if isinstance(source_id, str) else 0
        lines.append("| " + " | ".join(_cell(value) for value in (
            f.get("statement"), f.get("value"), f.get("context") or f.get("explanation"), when,
            _spatial_label(f.get("spatial_contexts")), f.get("confidence"), f"[{n}]",
            ", ".join(md_renderer.string_items(f.get("evidence"))),
        )) + " |")
    return "\n".join(lines) + "\n"


def evidence_md(doc: dict) -> str:
    lines = ["# Evidence", ""]
    for record in md_renderer.object_records(doc.get("evidence", [])):
        lines.extend([
            f"## `{record.get('id')}`",
            "",
            f"- Source: `{record.get('source')}`",
            f"- Support: `{record.get('support')}`",
            f"- Attribution basis: `{record.get('attribution_basis')}`",
            f"- Review status: `{record.get('review_status', 'unspecified')}`",
            "",
        ])
        lines.extend(_json_details("Selector", record.get("selector")))
        if record.get("excerpt") is not None:
            lines.extend(_json_details("Excerpt", record.get("excerpt")))
        if record.get("excerpt_sha256") is not None:
            lines.extend([f"**Excerpt SHA-256:** `{record.get('excerpt_sha256')}`", ""])
        if record.get("derivation") is not None:
            lines.extend(_json_details("Derivation", record.get("derivation")))
    return "\n".join(lines) + "\n"


def assessments_md(doc: dict) -> str:
    lines = ["# Assessments", "", "| ID | Dimension | Scope | Value | Assessor | Method | Evidence |",
             "|----|-----------|-------|-------|----------|--------|----------|"]
    for record in md_renderer.object_records(doc.get("assessments", [])):
        lines.append("| " + " | ".join(_cell(value) for value in (
            record.get("id"), record.get("dimension"), record.get("scope"), record.get("value"),
            record.get("assessor"), record.get("method"),
            ", ".join(md_renderer.string_items(record.get("evidence"))),
        )) + " |")
    return "\n".join(lines) + "\n"


def conflicts_md(doc: dict) -> str:
    lines = ["# Fact Conflicts", ""]
    for record in md_renderer.object_records(doc.get("fact_conflicts", [])):
        facts = ", ".join(
            f"`{item}`" for item in md_renderer.string_items(record.get("facts"))
        )
        lines.append(f"- `{record.get('id')}`: `{record.get('relation')}` between {facts} — {record.get('reason')}")
    return "\n".join(lines) + "\n"


def build(doc: dict, out: Path) -> int:
    doc = md_renderer.consumer_safe_copy(doc)
    bg.recompute(doc)  # ensure rollups are correct before exploding
    nodes_by_id = {n["id"]: n for n in (doc.get("nodes", []) or [])}
    edges = doc.get("edges", []) or []
    src_idx = _sources_index(doc.get("metadata", {}))
    evidence_by_id = {
        item.get("id"): item
        for item in md_renderer.object_records(doc.get("evidence", []))
        if isinstance(item.get("id"), str)
    }
    claims_by_node: dict[str, list] = {}
    for claim in md_renderer.object_records(doc.get("claims", [])):
        node_id = claim.get("node")
        if isinstance(node_id, str):
            claims_by_node.setdefault(node_id, []).append(claim)

    clusters = doc.get("clusters", []) or []
    nodes = doc.get("nodes", []) or []
    cluster_paths = _path_components(
        (c.get("id") for c in clusters), kind="c", reserved={"index.md"},
    )
    node_paths = _path_components(
        (n.get("id") for n in nodes), kind="n", reserved={"index"},
    )

    root = _validated_root(Path(out))
    concepts_dir = _safe_target(root, "concepts")
    directories = [root, concepts_dir]
    files = [
        _safe_target(root, "knowledge.json"),
        _safe_target(root, "index.md"),
        _safe_target(root, "concepts", "index.md"),
    ]
    for c in clusters:
        cdir = _safe_target(root, "concepts", cluster_paths[c["id"]])
        directories.append(cdir)
        files.append(_safe_target(root, "concepts", cluster_paths[c["id"]], "index.md"))
        for nid in c.get("concepts", []) or []:
            if nid in nodes_by_id:
                files.append(
                    _safe_target(root, "concepts", cluster_paths[c["id"]], f"{node_paths[nid]}.md")
                )
    if doc.get("facts"):
        files.append(_safe_target(root, "facts.md"))
    if doc.get("evidence"):
        files.append(_safe_target(root, "evidence.md"))
    if doc.get("assessments"):
        files.append(_safe_target(root, "assessments.md"))
    if doc.get("fact_conflicts"):
        files.append(_safe_target(root, "fact-conflicts.md"))

    _preflight(root, directories, files)
    root.mkdir(parents=True, exist_ok=True)
    _ensure_directory(root, concepts_dir)
    for directory in directories[2:]:
        _ensure_directory(root, directory)

    _write_text(root, _safe_target(root, "knowledge.json"),
                json.dumps(doc, ensure_ascii=False, indent=2) + "\n")
    _write_text(root, _safe_target(root, "index.md"), root_index_md(doc))
    _write_text(root, _safe_target(root, "concepts", "index.md"),
                concepts_index_md(doc, cluster_paths))

    written = 0
    for c in clusters:
        cdir = _safe_target(root, "concepts", cluster_paths[c["id"]])
        _write_text(root, cdir / "index.md", cluster_index_md(c, nodes_by_id, node_paths))
        for nid in c.get("concepts", []) or []:
            n = nodes_by_id.get(nid)
            if not n:
                continue
            _write_text(root, cdir / f"{node_paths[nid]}.md",
                        concept_md(n, edges, nodes_by_id, src_idx, evidence_by_id, claims_by_node))
            written += 1

    if doc.get("facts"):
        _write_text(root, _safe_target(root, "facts.md"), facts_md(doc, src_idx))
    if doc.get("evidence"):
        _write_text(root, _safe_target(root, "evidence.md"), evidence_md(doc))
    if doc.get("assessments"):
        _write_text(root, _safe_target(root, "assessments.md"), assessments_md(doc))
    if doc.get("fact_conflicts"):
        _write_text(root, _safe_target(root, "fact-conflicts.md"), conflicts_md(doc))

    print(f"bundle written to {root}/ — {written} concept files, "
          f"{len(clusters)} clusters")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Explode a .knowledge.json into a directory bundle")
    ap.add_argument("file")
    ap.add_argument("-o", "--out", help="output bundle directory (default: <name>.bundle next to input)")
    args = ap.parse_args(argv)

    path = Path(args.file)
    try:
        doc = strict_json.load_path(path)
    except strict_json.StrictJsonError as e:
        print(f"{args.file}: could not read strict JSON: {e}", file=sys.stderr)
        return 1

    stem = path.name
    for suffix in (".knowledge.json", ".json"):
        if stem.endswith(suffix):
            stem = stem[: -len(suffix)]
            break
    out = Path(args.out) if args.out else path.parent / f"{stem}.bundle"
    try:
        return build(doc, out)
    except md_renderer.RenderInputError as e:
        print(f"{args.file}: refusing bundle build: {e}", file=sys.stderr)
        return 2
    except BundleSecurityError as e:
        print(f"{out}: refusing unsafe bundle write: {e}", file=sys.stderr)
        return 2
    except OSError as e:
        print(f"{out}: could not write bundle: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
