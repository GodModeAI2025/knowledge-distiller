#!/usr/bin/env python3
"""Deterministic, offline exports for Knowledge Distiller graphs.

The exporters deliberately use only the Python standard library.  They accept
both Spec 1.0 graphs and forward-compatible graphs containing the 1.1 claim,
evidence, spatial, assessment, and derivation fields.  Every format carries a
canonical JSON copy of the source graph so fields unknown to this script are
not silently discarded.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile
from typing import Any, Iterable, Mapping, Sequence

import strict_json


FORMAT_VERSION = "1"
CANVAS_EXTENSION_KEY = "x-knowledge-distiller"


class ExportError(ValueError):
    """Raised when an input cannot be exported without ambiguity."""


def canonical_json(value: Any) -> str:
    """Return the canonical JSON representation used for hashes and payloads."""

    try:
        rendered = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        # Reject unpaired surrogates rather than failing later during hashing or
        # file output with an implementation-specific Unicode error.
        rendered.encode("utf-8")
        return rendered
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise ExportError(f"value is not valid finite JSON: {exc}") from exc


def graph_sha256(graph: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json(graph).encode("utf-8")).hexdigest()


def stable_id(kind: str, identity: str) -> str:
    """Create a portable, stable identifier without exposing unsafe input."""

    digest = hashlib.sha256(f"{kind}\0{identity}".encode("utf-8")).hexdigest()[:24]
    safe_kind = re.sub(r"[^a-z0-9-]", "-", kind.lower()).strip("-") or "item"
    return f"kd-{safe_kind}-{digest}"


def load_graph(path: Path | str) -> dict[str, Any]:
    source = Path(path)
    try:
        value = strict_json.load_path(source)
    except strict_json.StrictJsonError as exc:
        raise ExportError(f"invalid strict JSON in {source}: {exc}") from exc
    if not isinstance(value, dict):
        raise ExportError("the input graph must be a JSON object")
    # Exercise canonical serialization early to reject unsupported values.
    canonical_json(value)
    return value


def _object_list(container: Mapping[str, Any], key: str) -> list[dict[str, Any]]:
    value = container.get(key, [])
    if value is None:
        return []
    if not isinstance(value, list):
        raise ExportError(f"{key!r} must be an array")
    result: list[dict[str, Any]] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise ExportError(f"{key}[{index}] must be an object")
        result.append(item)
    return result


def _identity(item: Mapping[str, Any], kind: str, *, require_id: bool = True) -> str:
    raw_id = item.get("id")
    if isinstance(raw_id, str) and raw_id:
        return raw_id
    if require_id:
        raise ExportError(f"{kind} entry is missing a non-empty string id")
    return canonical_json(item)


def _sorted_unique(
    items: Sequence[dict[str, Any]], kind: str, *, require_id: bool = True
) -> list[dict[str, Any]]:
    seen: set[str] = set()
    decorated: list[tuple[str, str, dict[str, Any]]] = []
    for item in items:
        identity = _identity(item, kind, require_id=require_id)
        if identity in seen:
            raise ExportError(f"duplicate {kind} id: {identity!r}")
        seen.add(identity)
        decorated.append((identity, canonical_json(item), item))
    return [item for _, _, item in sorted(decorated, key=lambda row: (row[0], row[1]))]


def _edge_identity(edge: Mapping[str, Any]) -> str:
    raw_id = edge.get("id")
    if isinstance(raw_id, str) and raw_id:
        return raw_id
    source = edge.get("source")
    target = edge.get("target")
    edge_type = edge.get("type")
    if not all(isinstance(part, str) and part for part in (source, target, edge_type)):
        raise ExportError("edge must have id or non-empty source, target, and type")
    if edge_type == "tension" and target < source:
        source, target = target, source
    return f"{source}__{edge_type}__{target}"


def _sorted_edges(graph: Mapping[str, Any]) -> list[dict[str, Any]]:
    edges = _object_list(graph, "edges")
    seen: set[str] = set()
    decorated: list[tuple[str, str, dict[str, Any]]] = []
    for edge in edges:
        identity = _edge_identity(edge)
        if identity in seen:
            raise ExportError(f"duplicate edge identity: {identity!r}")
        seen.add(identity)
        decorated.append((identity, canonical_json(edge), edge))
    return [edge for _, _, edge in sorted(decorated, key=lambda row: (row[0], row[1]))]


def _metadata(graph: Mapping[str, Any]) -> dict[str, Any]:
    value = graph.get("metadata", {})
    if not isinstance(value, dict):
        raise ExportError("'metadata' must be an object")
    return value


def _sources(graph: Mapping[str, Any]) -> list[dict[str, Any]]:
    return _sorted_unique(_object_list(_metadata(graph), "sources"), "source")


def _ref_ids(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        if isinstance(item, str) and item:
            result.append(item)
        elif isinstance(item, dict) and isinstance(item.get("id"), str) and item["id"]:
            result.append(item["id"])
    return sorted(set(result))


# ---------------------------------------------------------------------------
# Cypher


def cypher_string(value: str) -> str:
    """Encode a Cypher string literal without allowing statement injection."""

    escaped: list[str] = ["'"]
    replacements = {
        "\\": "\\\\",
        "'": "\\'",
        "\n": "\\n",
        "\r": "\\r",
        "\t": "\\t",
        "\b": "\\b",
        "\f": "\\f",
    }
    for char in value:
        if char in replacements:
            escaped.append(replacements[char])
        elif ord(char) < 0x20 or char in {"\u0085", "\u2028", "\u2029"}:
            escaped.append(f"\\u{ord(char):04x}")
        else:
            escaped.append(char)
    escaped.append("'")
    return "".join(escaped)


def _cypher_literal(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        # Neo4j integers are signed 64-bit. Preserve larger JSON integers as
        # strings rather than emitting a script the database cannot parse.
        if -(2**63) <= value <= 2**63 - 1:
            return str(value)
        return cypher_string(str(value))
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ExportError("Cypher properties cannot contain non-finite numbers")
        return repr(value)
    if isinstance(value, str):
        return cypher_string(value)
    if isinstance(value, list) and all(
        item is None or isinstance(item, (bool, int, float, str)) for item in value
    ):
        return "[" + ", ".join(_cypher_literal(item) for item in value) + "]"
    # Neo4j properties cannot contain maps or heterogeneous nested structures.
    return cypher_string(canonical_json(value))


def _cypher_map(properties: Mapping[str, Any]) -> str:
    pairs = []
    for key in sorted(properties):
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            raise ExportError(f"unsafe internal Cypher property name: {key!r}")
        pairs.append(f"{key}: {_cypher_literal(properties[key])}")
    return "{" + ", ".join(pairs) + "}"


_ENTITY_LABELS = {
    "graph": "KDGraph",
    "source": "KDSource",
    "cluster": "KDCluster",
    "concept": "KDConcept",
    "edge": "KDEdge",
    "evidence": "KDEvidence",
    "claim": "KDClaim",
    "fact": "KDFact",
    "chunk": "KDChunk",
    "assessment": "KDAssessment",
    "conflict": "KDConflict",
    "question": "KDQuestion",
}


_REL_TYPES = {
    "ABOUT",
    "CLAIM_OF",
    "DERIVED_FROM_SOURCE",
    "FROM_CONCEPT",
    "HAS_ASSESSMENT",
    "HAS_CHUNK",
    "HAS_CLAIM",
    "HAS_CLUSTER",
    "HAS_CONCEPT",
    "HAS_CONFLICT",
    "HAS_EDGE",
    "HAS_EVIDENCE",
    "HAS_FACT",
    "HAS_QUESTION",
    "HAS_SOURCE",
    "IN_CLUSTER",
    "KD_RELATIONSHIP",
    "SUPPORTED_BY",
    "TO_CONCEPT",
}


def _node_id(kind: str, raw_identity: str) -> str:
    return stable_id(kind, raw_identity)


def _entity_properties(kind: str, item: Mapping[str, Any]) -> dict[str, Any]:
    properties: dict[str, Any] = {
        "entity_kind": kind,
        "raw_json": canonical_json(item),
    }
    raw_id = item.get("id")
    if isinstance(raw_id, str):
        properties["original_id"] = raw_id

    scalar_keys = (
        "assessed_at",
        "assessor",
        "cluster",
        "confidence",
        "context",
        "date",
        "definition",
        "description",
        "dimension",
        "excerpt",
        "explanation",
        "file",
        "include_in_default_retrieval",
        "kind",
        "label",
        "method",
        "metric",
        "node",
        "origin",
        "relevance",
        "review_status",
        "scope",
        "source",
        "statement",
        "support",
        "target",
        "text",
        "title",
        "token_estimate",
        "type",
        "url",
        "value",
        "weight",
        "attribution_basis",
    )
    for key in scalar_keys:
        value = item.get(key)
        if value is None or isinstance(value, (bool, int, float, str)):
            if key in item and value is not None:
                properties[key] = value

    complex_keys = (
        "agents",
        "citations",
        "claim_ids",
        "concepts",
        "derivation",
        "evidence",
        "origin_detail",
        "selector",
        "sources",
        "spatial_contexts",
        "statements",
        "temporal",
    )
    for key in complex_keys:
        if key in item:
            properties[f"{key}_json"] = canonical_json(item[key])
    return properties


def _emit_node(
    lines: list[str], kind: str, identity: str, properties: Mapping[str, Any]
) -> str:
    label = _ENTITY_LABELS[kind]
    kd_id = _node_id(kind, identity)
    props = dict(properties)
    props["kd_id"] = kd_id
    lines.append(
        f"MERGE (n:KDEntity {{kd_id: {cypher_string(kd_id)}}}) "
        f"SET n:{label} SET n += {_cypher_map(props)};"
    )
    return kd_id


def _emit_link(
    lines: list[str],
    source_id: str,
    target_id: str,
    rel_type: str,
    identity: str,
    properties: Mapping[str, Any] | None = None,
) -> None:
    if rel_type not in _REL_TYPES:
        raise ExportError(f"unsafe internal relationship type: {rel_type}")
    rel_id = stable_id(f"rel-{rel_type.lower()}", identity)
    props = dict(properties or {})
    props["kd_id"] = rel_id
    lines.append(
        f"MATCH (a:KDEntity {{kd_id: {cypher_string(source_id)}}}), "
        f"(b:KDEntity {{kd_id: {cypher_string(target_id)}}}) "
        f"MERGE (a)-[r:{rel_type} {{kd_id: {cypher_string(rel_id)}}}]->(b) "
        f"SET r += {_cypher_map(props)};"
    )


def _collection_specs(graph: Mapping[str, Any]) -> list[tuple[str, list[dict[str, Any]]]]:
    return [
        ("source", _sources(graph)),
        ("cluster", _sorted_unique(_object_list(graph, "clusters"), "cluster")),
        ("concept", _sorted_unique(_object_list(graph, "nodes"), "concept")),
        ("edge", _sorted_edges(graph)),
        ("evidence", _sorted_unique(_object_list(graph, "evidence"), "evidence")),
        ("claim", _sorted_unique(_object_list(graph, "claims"), "claim")),
        ("fact", _sorted_unique(_object_list(graph, "facts"), "fact")),
        ("chunk", _sorted_unique(_object_list(graph, "chunks"), "chunk")),
        ("assessment", _sorted_unique(_object_list(graph, "assessments"), "assessment")),
        ("conflict", _sorted_unique(_object_list(graph, "fact_conflicts"), "conflict")),
    ]


def render_cypher(graph: Mapping[str, Any]) -> str:
    """Render an idempotent Neo4j 5 Cypher statement script."""

    digest = graph_sha256(graph)
    metadata = _metadata(graph)
    collections = _collection_specs(graph)
    questions_value = graph.get("open_questions", [])
    if questions_value is None:
        questions_value = []
    if not isinstance(questions_value, list):
        raise ExportError("'open_questions' must be an array")

    lines = [
        "// Knowledge Distiller deterministic Cypher export",
        "// Constant labels and relationship types prevent input-driven Cypher injection.",
        "CREATE CONSTRAINT kd_entity_id IF NOT EXISTS FOR (n:KDEntity) REQUIRE n.kd_id IS UNIQUE;",
    ]
    graph_identity = digest
    graph_id = _emit_node(
        lines,
        "graph",
        graph_identity,
        {
            "entity_kind": "graph",
            "export_format_version": FORMAT_VERSION,
            "source_graph_json": canonical_json(graph),
            "source_sha256": digest,
            "spec_version": metadata.get("distiller_spec_version", "unknown"),
            "title": metadata.get("title", "Untitled graph"),
        },
    )

    ids: dict[tuple[str, str], str] = {}
    for kind, items in collections:
        for item in items:
            identity = _edge_identity(item) if kind == "edge" else _identity(item, kind)
            kd_id = _emit_node(lines, kind, identity, _entity_properties(kind, item))
            ids[(kind, identity)] = kd_id
            _emit_link(
                lines,
                graph_id,
                kd_id,
                {
                    "source": "HAS_SOURCE",
                    "cluster": "HAS_CLUSTER",
                    "concept": "HAS_CONCEPT",
                    "edge": "HAS_EDGE",
                    "evidence": "HAS_EVIDENCE",
                    "claim": "HAS_CLAIM",
                    "fact": "HAS_FACT",
                    "chunk": "HAS_CHUNK",
                    "assessment": "HAS_ASSESSMENT",
                    "conflict": "HAS_CONFLICT",
                }[kind],
                f"{graph_id}\0{kind}\0{identity}",
            )

    for index, question in enumerate(questions_value):
        identity = canonical_json({"index": index, "value": question})
        item = {"id": f"question-{index + 1}", "value": question}
        question_id = _emit_node(lines, "question", identity, _entity_properties("question", item))
        _emit_link(
            lines,
            graph_id,
            question_id,
            "HAS_QUESTION",
            f"{graph_id}\0question\0{identity}",
        )

    # Structural concept membership.
    for concept in dict(collections)["concept"]:
        concept_identity = _identity(concept, "concept")
        cluster = concept.get("cluster")
        if isinstance(cluster, str) and ("cluster", cluster) in ids:
            _emit_link(
                lines,
                ids[("concept", concept_identity)],
                ids[("cluster", cluster)],
                "IN_CLUSTER",
                f"concept:{concept_identity}\0cluster:{cluster}",
            )
        for evidence_id in _ref_ids(concept.get("evidence")):
            if ("evidence", evidence_id) in ids:
                _emit_link(
                    lines,
                    ids[("concept", concept_identity)],
                    ids[("evidence", evidence_id)],
                    "SUPPORTED_BY",
                    f"concept:{concept_identity}\0evidence:{evidence_id}",
                )

    # Claims remain first-class entities, allowing evidence and derivation metadata.
    for evidence in dict(collections)["evidence"]:
        evidence_identity = _identity(evidence, "evidence")
        source_ref = evidence.get("source")
        if isinstance(source_ref, str) and ("source", source_ref) in ids:
            _emit_link(
                lines,
                ids[("evidence", evidence_identity)],
                ids[("source", source_ref)],
                "DERIVED_FROM_SOURCE",
                f"evidence:{evidence_identity}\0source:{source_ref}",
            )

    for claim in dict(collections)["claim"]:
        claim_identity = _identity(claim, "claim")
        claim_id = ids[("claim", claim_identity)]
        node_ref = claim.get("node")
        if isinstance(node_ref, str) and ("concept", node_ref) in ids:
            _emit_link(
                lines,
                claim_id,
                ids[("concept", node_ref)],
                "CLAIM_OF",
                f"claim:{claim_identity}\0concept:{node_ref}",
            )
        for evidence_id in _ref_ids(claim.get("evidence")):
            if ("evidence", evidence_id) in ids:
                _emit_link(
                    lines,
                    claim_id,
                    ids[("evidence", evidence_id)],
                    "SUPPORTED_BY",
                    f"claim:{claim_identity}\0evidence:{evidence_id}",
                )

    # Edges are both first-class nodes and navigable graph relationships.  The
    # source-provided type is a property, never executable Cypher syntax.
    for edge in dict(collections)["edge"]:
        edge_identity = _edge_identity(edge)
        edge_id = ids[("edge", edge_identity)]
        source = edge.get("source")
        target = edge.get("target")
        if isinstance(source, str) and ("concept", source) in ids:
            _emit_link(
                lines,
                edge_id,
                ids[("concept", source)],
                "FROM_CONCEPT",
                f"edge:{edge_identity}\0from:{source}",
            )
        if isinstance(target, str) and ("concept", target) in ids:
            _emit_link(
                lines,
                edge_id,
                ids[("concept", target)],
                "TO_CONCEPT",
                f"edge:{edge_identity}\0to:{target}",
            )
        if (
            isinstance(source, str)
            and isinstance(target, str)
            and ("concept", source) in ids
            and ("concept", target) in ids
        ):
            _emit_link(
                lines,
                ids[("concept", source)],
                ids[("concept", target)],
                "KD_RELATIONSHIP",
                edge_identity,
                {
                    "edge_id": edge_identity,
                    "label": edge.get("label", ""),
                    "raw_json": canonical_json(edge),
                    "relationship_type": edge.get("type", "related"),
                },
            )
        for evidence_id in _ref_ids(edge.get("evidence")):
            if ("evidence", evidence_id) in ids:
                _emit_link(
                    lines,
                    edge_id,
                    ids[("evidence", evidence_id)],
                    "SUPPORTED_BY",
                    f"edge:{edge_identity}\0evidence:{evidence_id}",
                )

    for fact in dict(collections)["fact"]:
        fact_identity = _identity(fact, "fact")
        source = fact.get("source")
        if isinstance(source, str) and ("source", source) in ids:
            _emit_link(
                lines,
                ids[("fact", fact_identity)],
                ids[("source", source)],
                "DERIVED_FROM_SOURCE",
                f"fact:{fact_identity}\0source:{source}",
            )
        concept_ref = fact.get("concept")
        if isinstance(concept_ref, str) and ("concept", concept_ref) in ids:
            _emit_link(
                lines,
                ids[("fact", fact_identity)],
                ids[("concept", concept_ref)],
                "ABOUT",
                f"fact:{fact_identity}\0concept:{concept_ref}",
            )
        for evidence_id in _ref_ids(fact.get("evidence")):
            if ("evidence", evidence_id) in ids:
                _emit_link(
                    lines,
                    ids[("fact", fact_identity)],
                    ids[("evidence", evidence_id)],
                    "SUPPORTED_BY",
                    f"fact:{fact_identity}\0evidence:{evidence_id}",
                )

    for chunk in dict(collections)["chunk"]:
        chunk_identity = _identity(chunk, "chunk")
        for concept_ref in _ref_ids(chunk.get("concepts")):
            if ("concept", concept_ref) in ids:
                _emit_link(
                    lines,
                    ids[("chunk", chunk_identity)],
                    ids[("concept", concept_ref)],
                    "ABOUT",
                    f"chunk:{chunk_identity}\0concept:{concept_ref}",
                )
        for evidence_id in _ref_ids(chunk.get("evidence")):
            if ("evidence", evidence_id) in ids:
                _emit_link(
                    lines,
                    ids[("chunk", chunk_identity)],
                    ids[("evidence", evidence_id)],
                    "SUPPORTED_BY",
                    f"chunk:{chunk_identity}\0evidence:{evidence_id}",
                )

    for assessment in dict(collections)["assessment"]:
        assessment_identity = _identity(assessment, "assessment")
        for evidence_id in _ref_ids(assessment.get("evidence")):
            if ("evidence", evidence_id) in ids:
                _emit_link(
                    lines,
                    ids[("assessment", assessment_identity)],
                    ids[("evidence", evidence_id)],
                    "SUPPORTED_BY",
                    f"assessment:{assessment_identity}\0evidence:{evidence_id}",
                )

    for conflict in dict(collections)["conflict"]:
        conflict_identity = _identity(conflict, "conflict")
        for fact_ref in _ref_ids(conflict.get("facts")):
            if ("fact", fact_ref) in ids:
                _emit_link(
                    lines,
                    ids[("conflict", conflict_identity)],
                    ids[("fact", fact_ref)],
                    "ABOUT",
                    f"conflict:{conflict_identity}\0fact:{fact_ref}",
                )

    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# CTXT


def _ctxt_records(graph: Mapping[str, Any]) -> list[dict[str, Any]]:
    chunks = _sorted_unique(_object_list(graph, "chunks"), "chunk")
    nodes = _sorted_unique(_object_list(graph, "nodes"), "concept")
    node_by_id = {_identity(node, "concept"): node for node in nodes}
    if chunks:
        records: list[dict[str, Any]] = []
        for chunk in chunks:
            concept_ids = _ref_ids(chunk.get("concepts"))
            labels = [
                str(node_by_id[item].get("label", item))
                for item in concept_ids
                if item in node_by_id
            ]
            records.append(
                {
                    "id": _identity(chunk, "chunk"),
                    "title": " / ".join(labels) or _identity(chunk, "chunk"),
                    "kind": chunk.get("kind", "source_claims"),
                    "concepts": concept_ids,
                    "evidence": _ref_ids(chunk.get("evidence")),
                    "origin": chunk.get("origin"),
                    "spatial_contexts": chunk.get("spatial_contexts", []),
                    "derivation": chunk.get("derivation"),
                    "text": chunk.get("text", ""),
                    "raw": chunk,
                    "synthetic": False,
                }
            )
        return records

    # Spec 1.0 permits graphs without chunks.  A readable context is derived
    # from each concept while the raw concept and whole graph remain embedded.
    records = []
    for node in nodes:
        node_id = _identity(node, "concept")
        text_parts = [
            value
            for value in (node.get("definition"), node.get("relevance"))
            if isinstance(value, str) and value
        ]
        statements = node.get("statements", [])
        if isinstance(statements, list):
            text_parts.extend(item for item in statements if isinstance(item, str) and item)
        records.append(
            {
                "id": f"context:{node_id}",
                "title": node.get("label", node_id),
                "kind": "derived_concept_context",
                "concepts": [node_id],
                "evidence": _ref_ids(node.get("evidence")),
                "origin": node.get("origin"),
                "spatial_contexts": node.get("spatial_contexts", []),
                "derivation": node.get("derivation"),
                "text": "\n\n".join(text_parts),
                "raw": node,
                "synthetic": True,
            }
        )
    return records


def _ctxt_json(value: Any) -> str:
    """Canonical JSON constrained to one physical CTXT line."""

    return (
        canonical_json(value)
        .replace("\u0085", "\\u0085")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def render_ctxt(graph: Mapping[str, Any]) -> str:
    """Render the documented KD-CTXT/1 line-oriented context format."""

    metadata = _metadata(graph)
    digest = graph_sha256(graph)
    parts = [
        "#!KD-CTXT/1\n",
        f"graph-sha256: {digest}\n",
        f"spec-version-json: {_ctxt_json(metadata.get('distiller_spec_version', 'unknown'))}\n",
        f"title-json: {_ctxt_json(metadata.get('title', 'Untitled graph'))}\n",
        f"graph-json: {_ctxt_json(graph)}\n",
    ]
    for record in _ctxt_records(graph):
        text_value = record.get("text", "")
        if not isinstance(text_value, str):
            text_value = str(text_value)
        # The readable body uses LF. record-json remains the exact authoritative
        # representation, including original line endings and unknown fields.
        readable_text = text_value.replace("\r\n", "\n").replace("\r", "\n")
        readable_bytes = readable_text.encode("utf-8")
        parts.extend(
            [
                "===\n",
                f"id-json: {_ctxt_json(record['id'])}\n",
                f"title-json: {_ctxt_json(record['title'])}\n",
                f"kind-json: {_ctxt_json(record['kind'])}\n",
                f"concepts-json: {_ctxt_json(record['concepts'])}\n",
                f"evidence-json: {_ctxt_json(record['evidence'])}\n",
                f"origin-json: {_ctxt_json(record['origin'])}\n",
                f"spatial-contexts-json: {_ctxt_json(record['spatial_contexts'])}\n",
                f"derivation-json: {_ctxt_json(record['derivation'])}\n",
                f"record-json: {_ctxt_json(record)}\n",
                f"body-utf8-bytes: {len(readable_bytes)}\n",
                f"body-sha256: {hashlib.sha256(readable_bytes).hexdigest()}\n",
                "---\n",
                readable_text,
                # One framing LF follows the byte-counted body. It is not part
                # of body-utf8-bytes or body-sha256.
                "\n",
            ]
        )
    return "".join(parts)


# ---------------------------------------------------------------------------
# Obsidian Canvas / JSON Canvas


def _markdown_escape(value: Any) -> str:
    text = str(value)
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    for char in ("\\", "`", "*", "_", "{", "}", "[", "]", "(", ")", "#", "+", ".", "!", "|", "~", "-"):
        text = text.replace(char, "\\" + char)
    return text


def _concept_canvas_text(
    node: Mapping[str, Any],
    claims: Sequence[Mapping[str, Any]],
    evidence_by_id: Mapping[str, Mapping[str, Any]],
) -> str:
    label = _markdown_escape(node.get("label", node.get("id", "Concept")))
    lines = [f"# {label}"]
    for heading, key in (("Definition", "definition"), ("Relevance", "relevance")):
        value = node.get(key)
        if isinstance(value, str) and value:
            lines.extend(["", f"**{heading}:** {_markdown_escape(value)}"])
    confidence = node.get("confidence")
    if confidence is not None:
        lines.extend(["", f"**Confidence:** {_markdown_escape(confidence)}"])

    statements = node.get("statements")
    if isinstance(statements, list) and statements:
        lines.extend(["", "## Statements"])
        lines.extend(f"- {_markdown_escape(item)}" for item in statements)

    spatial = node.get("spatial_contexts")
    if isinstance(spatial, list) and spatial:
        lines.extend(["", "## Spatial context"])
        lines.extend(f"- {_markdown_escape(canonical_json(item))}" for item in spatial)

    if claims:
        lines.extend(["", "## Claims"])
        for claim in claims:
            statement = claim.get("statement", claim.get("id", "claim"))
            lines.append(f"- {_markdown_escape(statement)}")
            if claim.get("derivation") is not None:
                lines.append(
                    f"  - Derivation: {_markdown_escape(canonical_json(claim['derivation']))}"
                )

    evidence_ids = set(_ref_ids(node.get("evidence")))
    for claim in claims:
        evidence_ids.update(_ref_ids(claim.get("evidence")))
    if evidence_ids:
        lines.extend(["", "## Evidence"])
        for evidence_id in sorted(evidence_ids):
            evidence = evidence_by_id.get(evidence_id)
            if evidence is None:
                lines.append(f"- {_markdown_escape(evidence_id)}")
                continue
            summary = (
                evidence.get("excerpt")
                or evidence.get("quote")
                or evidence.get("statement")
                or evidence.get("text")
            )
            if summary:
                lines.append(
                    f"- {_markdown_escape(evidence_id)}: {_markdown_escape(summary)}"
                )
            else:
                lines.append(f"- {_markdown_escape(evidence_id)}")
    return "\n".join(lines)


def render_canvas(graph: Mapping[str, Any]) -> str:
    """Render a deterministic Obsidian-compatible JSON Canvas document."""

    digest = graph_sha256(graph)
    clusters = _sorted_unique(_object_list(graph, "clusters"), "cluster")
    concepts = _sorted_unique(_object_list(graph, "nodes"), "concept")
    edges = _sorted_edges(graph)
    evidence = _sorted_unique(_object_list(graph, "evidence"), "evidence")
    claims = _sorted_unique(_object_list(graph, "claims"), "claim")

    concept_ids = {_identity(item, "concept") for item in concepts}
    claims_by_node: dict[str, list[dict[str, Any]]] = {}
    for claim in claims:
        node_ref = claim.get("node")
        if isinstance(node_ref, str):
            claims_by_node.setdefault(node_ref, []).append(claim)
    evidence_by_id = {_identity(item, "evidence"): item for item in evidence}

    cluster_by_id = {_identity(item, "cluster"): item for item in clusters}
    cluster_order = list(cluster_by_id)
    uncategorized = any(
        not isinstance(node.get("cluster"), str) or node.get("cluster") not in cluster_by_id
        for node in concepts
    )
    if uncategorized or (concepts and not cluster_order):
        cluster_order.append("__unclustered__")

    concepts_by_cluster: dict[str, list[dict[str, Any]]] = {item: [] for item in cluster_order}
    for node in concepts:
        cluster = node.get("cluster")
        bucket = cluster if isinstance(cluster, str) and cluster in cluster_by_id else "__unclustered__"
        concepts_by_cluster.setdefault(bucket, []).append(node)

    canvas_nodes: list[dict[str, Any]] = []
    concept_canvas_ids: dict[str, str] = {}
    for column, cluster_id in enumerate(cluster_order):
        items = concepts_by_cluster.get(cluster_id, [])
        x = column * 440
        if cluster_id == "__unclustered__":
            cluster = {"id": cluster_id, "label": "Unclustered"}
        else:
            cluster = cluster_by_id[cluster_id]

        projected_items: list[tuple[dict[str, Any], str, str, int, list[dict[str, Any]]]] = []
        for node in items:
            node_id = _identity(node, "concept")
            canvas_id = stable_id("canvas-concept", node_id)
            concept_canvas_ids[node_id] = canvas_id
            node_claims = sorted(
                claims_by_node.get(node_id, []),
                key=lambda item: (_identity(item, "claim"), canonical_json(item)),
            )
            text = _concept_canvas_text(node, node_claims, evidence_by_id)
            line_count = text.count("\n") + 1
            height = max(220, min(760, 100 + line_count * 24))
            projected_items.append((node, canvas_id, text, height, node_claims))

        y_cursor = 40
        positioned_items: list[
            tuple[dict[str, Any], str, str, int, int, list[dict[str, Any]]]
        ] = []
        for node, canvas_id, text, height, node_claims in projected_items:
            positioned_items.append((node, canvas_id, text, height, y_cursor, node_claims))
            y_cursor += height + 40
        group_height = max(260, y_cursor + 20)
        canvas_nodes.append(
            {
                "id": stable_id("canvas-cluster", cluster_id),
                "type": "group",
                "x": x - 30,
                "y": -40,
                "width": 380,
                "height": group_height,
                "label": str(cluster.get("label", cluster_id)),
                "color": str((column % 6) + 1),
                CANVAS_EXTENSION_KEY: {"kind": "cluster", "raw": cluster},
            }
        )
        for node, canvas_id, text, height, y, node_claims in positioned_items:
            canvas_nodes.append(
                {
                    "id": canvas_id,
                    "type": "text",
                    "x": x,
                    "y": y,
                    "width": 320,
                    "height": height,
                    "text": text,
                    CANVAS_EXTENSION_KEY: {
                        "kind": "concept",
                        "raw": node,
                        "claims": node_claims,
                    },
                }
            )

    canvas_edges: list[dict[str, Any]] = []
    for edge in edges:
        source = edge.get("source")
        target = edge.get("target")
        if not isinstance(source, str) or not isinstance(target, str):
            continue
        if source not in concept_canvas_ids or target not in concept_canvas_ids:
            continue
        identity = _edge_identity(edge)
        canvas_edges.append(
            {
                "id": stable_id("canvas-edge", identity),
                "fromNode": concept_canvas_ids[source],
                "toNode": concept_canvas_ids[target],
                "toEnd": "arrow" if edge.get("type") != "tension" else "none",
                "label": str(edge.get("label") or edge.get("type") or "related"),
                CANVAS_EXTENSION_KEY: {"kind": "relationship", "raw": edge},
            }
        )

    document = {
        "nodes": sorted(canvas_nodes, key=lambda item: item["id"]),
        "edges": sorted(canvas_edges, key=lambda item: item["id"]),
        CANVAS_EXTENSION_KEY: {
            "format": "Knowledge Distiller Canvas Extension",
            "format_version": FORMAT_VERSION,
            "source_sha256": digest,
            "source_graph": graph,
            "visible_concepts": sorted(concept_ids),
        },
    }
    return json.dumps(
        document,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        indent=2,
    ) + "\n"


# ---------------------------------------------------------------------------
# File/CLI interface


def _atomic_write(path: Path, content: str) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    except OSError as exc:
        raise ExportError(f"cannot prepare output {path}: {exc}") from exc
    temporary_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    except Exception as exc:
        try:
            temporary_path.unlink(missing_ok=True)
        finally:
            if isinstance(exc, OSError):
                raise ExportError(f"cannot write output {path}: {exc}") from exc
            raise


def export_files(
    graph: Mapping[str, Any],
    output_dir: Path | str,
    basename: str,
    formats: Iterable[str] = ("cypher", "ctxt", "canvas"),
    *,
    protected_paths: Iterable[Path | str] = (),
) -> dict[str, Path]:
    selected = set(formats)
    unknown = selected - {"cypher", "ctxt", "canvas"}
    if unknown:
        raise ExportError(f"unknown export format(s): {', '.join(sorted(unknown))}")
    if (
        not basename
        or basename in {".", ".."}
        or "/" in basename
        or "\\" in basename
        or "\0" in basename
    ):
        raise ExportError("basename must be a safe single path component")
    destination = Path(output_dir)
    renderers = {
        "cypher": render_cypher,
        "ctxt": render_ctxt,
        "canvas": render_canvas,
    }
    planned = {
        export_format: destination / f"{basename}.{export_format}"
        for export_format in ("cypher", "ctxt", "canvas")
        if export_format in selected
    }
    protected = [Path(path) for path in protected_paths]
    for export_format, output in planned.items():
        for protected_path in protected:
            try:
                same_target = output.resolve(strict=False) == protected_path.resolve(strict=True)
                if output.exists():
                    same_target = same_target or os.path.samefile(output, protected_path)
            except OSError as exc:
                raise ExportError(
                    f"cannot validate {export_format} output against protected input: {exc}"
                ) from exc
            if same_target:
                raise ExportError(
                    f"{export_format} output must be different from the canonical input"
                )

    outputs: dict[str, Path] = {}
    for export_format in ("cypher", "ctxt", "canvas"):
        if export_format not in selected:
            continue
        output = planned[export_format]
        _atomic_write(output, renderers[export_format](graph))
        outputs[export_format] = output
    return outputs


def _default_basename(path: Path) -> str:
    name = path.name
    return name[:-5] if name.lower().endswith(".json") else name


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build deterministic Cypher, CTXT, and Obsidian Canvas exports."
    )
    parser.add_argument("input", type=Path, help="Knowledge Distiller .knowledge.json input")
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Destination directory (default: directory containing the input)",
    )
    parser.add_argument(
        "--format",
        dest="formats",
        action="append",
        choices=("all", "cypher", "ctxt", "canvas"),
        help="Format to write; repeat for several (default: all)",
    )
    parser.add_argument(
        "--basename",
        help="Output basename without extension (default: input name without .json)",
    )
    args = parser.parse_args(argv)

    try:
        if not args.input.name.endswith(".knowledge.json"):
            raise ExportError("input must end with .knowledge.json")
        graph = load_graph(args.input)
        requested = args.formats or ["all"]
        formats = ("cypher", "ctxt", "canvas") if "all" in requested else requested
        output_dir = args.output_dir or args.input.parent
        basename = args.basename or _default_basename(args.input)
        outputs = export_files(
            graph, output_dir, basename, formats, protected_paths=(args.input,)
        )
    except ExportError as exc:
        parser.error(str(exc))

    for export_format in ("cypher", "ctxt", "canvas"):
        if export_format in outputs:
            print(f"{export_format}: {outputs[export_format]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
