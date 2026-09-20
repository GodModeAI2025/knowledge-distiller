#!/usr/bin/env python3
"""Deterministically merge two validated Knowledge Distiller graphs.

The merge is monotone and local: prior payloads are retained, new payloads are appended in
input order, conflicts fail closed unless a fact conflict is explicitly retained, and no
network operation is performed. The CLI always archives the prior bytes and emits JSON plus
Markdown deltas. See ``docs/MERGE.md``.
"""
from __future__ import annotations

import argparse
import bisect
import copy
import hashlib
import json
import os
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_graph as bg  # noqa: E402
import validate_knowledge as vk  # noqa: E402
import strict_json  # noqa: E402


SUPPORTED_SPECS = {"1.0", "1.1"}
DERIVED_METADATA = {
    "quality_score", "conformance_score", "concept_count", "relationship_count",
    "cluster_count", "fact_count",
}
KNOWN_TOP_LEVEL = {
    "@context", "metadata", "clusters", "nodes", "edges", "facts", "chunks",
    "open_questions", "evidence", "claims", "assessments", "fact_conflicts",
}
MARKER = re.compile(r"\[(\d+)\]")
# Same bound as the validator: a marker with more digits than this indexes
# nothing, and converting it would raise instead of being reported.
MAX_CITATION_DIGITS = 9
ARCHIVE_NAME = re.compile(r"\.v(\d{4,})\.[0-9a-f]{12}\.json$")
MAX_INPUT_BYTES = 64 * 1024 * 1024
# Shared with the loader. Every step of the merge walks the graph recursively --
# ``copy.deepcopy``, ``_merge_value``, ``json.dumps`` in ``_canon`` -- so a graph
# that nests deeper than this is refused up front instead of aborting somewhere
# in the middle with an interpreter-level RecursionError.
MAX_STRUCTURE_DEPTH = strict_json.MAX_STRUCTURE_DEPTH
_MISSING = object()


class MergeError(RuntimeError):
    """Base class for safe, user-facing merge failures."""


class MergeValidationError(MergeError):
    """An input or the proposed result violates the producer contract."""


class PayloadConflictError(MergeError):
    """One identity carries two payloads that cannot be represented losslessly."""


class WriteSafetyError(MergeError):
    """A requested output would overwrite or traverse an unsafe target."""


def _canon(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha_document(doc: dict) -> str:
    return _sha_bytes((_canon(doc) + "\n").encode("utf-8"))


def _stable_union(left: list, right: list) -> list:
    result = copy.deepcopy(left)
    seen = {_canon(item) for item in left}
    for item in right:
        key = _canon(item)
        if key not in seen:
            result.append(copy.deepcopy(item))
            seen.add(key)
    return result


def _exceeds_structure_depth(value: object, limit: int = MAX_STRUCTURE_DEPTH) -> bool:
    """Report whether ``value`` nests deeper than ``limit`` containers.

    Deliberately iterative: a recursive probe would be the very failure it exists
    to prevent. A self-referential structure -- impossible from JSON, reachable
    through the Python API -- terminates here as "too deep" rather than looping.
    """
    stack: list[tuple[object, int]] = [(value, 0)]
    while stack:
        item, depth = stack.pop()
        if not isinstance(item, (dict, list)):
            continue
        if depth >= limit:
            return True
        children = item.values() if isinstance(item, dict) else item
        for child in children:
            if isinstance(child, (dict, list)):
                stack.append((child, depth + 1))
    return False


def _require_bounded_depth(doc: object, label: str) -> None:
    if _exceeds_structure_depth(doc):
        raise MergeValidationError(
            f"{label}: structure nesting exceeds the safe depth limit of "
            f"{MAX_STRUCTURE_DEPTH}"
        )


def _merge_value(left: object, right: object, where: str, depth: int = 0) -> object:
    """Additively combine compatible values or fail instead of choosing one silently."""
    if depth >= MAX_STRUCTURE_DEPTH:
        raise MergeValidationError(
            f"{where}: structure nesting exceeds the safe depth limit of "
            f"{MAX_STRUCTURE_DEPTH}"
        )
    if _canon(left) == _canon(right):
        return copy.deepcopy(left)
    if left is None or left == "":
        return copy.deepcopy(right)
    if right is None or right == "":
        return copy.deepcopy(left)
    if isinstance(left, list) and isinstance(right, list):
        return _stable_union(left, right)
    if isinstance(left, dict) and isinstance(right, dict):
        result = copy.deepcopy(left)
        for key, value in right.items():
            if key not in result:
                result[key] = copy.deepcopy(value)
            else:
                result[key] = _merge_value(result[key], value, f"{where}.{key}", depth + 1)
        return result
    raise PayloadConflictError(
        f"{where}: conflicting payloads {left!r} and {right!r}; merge refused"
    )


def _merge_record(
    left: dict,
    right: dict,
    where: str,
    *,
    ignore: set[str] | None = None,
    immutable_lists: set[str] | None = None,
) -> dict:
    result = copy.deepcopy(left)
    ignored = ignore or set()
    locked = immutable_lists or set()
    for key, value in right.items():
        if key in ignored:
            continue
        if key not in result:
            result[key] = copy.deepcopy(value)
            continue
        if key in locked and _canon(result[key]) != _canon(value):
            raise PayloadConflictError(
                f"{where}.{key}: conflicting ordered payload; merge refused"
            )
        result[key] = _merge_value(result[key], value, f"{where}.{key}")
    return result


def _full_validation(doc: dict, *, prev: dict | None = None) -> dict:
    report = vk.Report()
    schema_checked = vk.schema_validate(doc, report)
    core = vk.validate(doc, prev=prev, ran_schema=schema_checked)
    report.errors.extend(core.errors)
    report.warnings.extend(core.warnings)
    return {
        "ok": not report.errors,
        "schema_checked": schema_checked,
        "errors": report.errors,
        "warnings": report.warnings,
        "conformance_score": report.conformance_score(),
        "semantic_accuracy_evaluated": False,
    }


def _require_valid(doc: dict, label: str, *, prev: dict | None = None) -> dict:
    result = _full_validation(doc, prev=prev)
    if not result["ok"]:
        details = "\n".join(f"  - {error}" for error in result["errors"][:20])
        extra = len(result["errors"]) - 20
        if extra > 0:
            details += f"\n  - ... and {extra} more error(s)"
        raise MergeValidationError(f"{label} failed validation:\n{details}")
    version = doc.get("metadata", {}).get("distiller_spec_version")
    if version not in SUPPORTED_SPECS:
        raise MergeValidationError(
            f"{label} targets unsupported distiller_spec_version {version!r}; "
            "merge supports only 1.0 and 1.1"
        )
    return result


def _track(tracker: dict, section: str, collection: str, identity: str) -> None:
    """Append ``identity`` to a change list once, in first-seen order.

    The list is the reported order and stays a list. Membership is answered by a
    set held in the tracker's private ``_seen`` scratch, because scanning the
    list would make recording N changes cost N squared -- the same shape as the
    comparison this module already indexes away.
    """
    values = tracker[section].setdefault(collection, [])
    seen_by_collection = tracker.setdefault("_seen", {})
    seen = seen_by_collection.get((section, collection))
    if seen is None:
        seen = seen_by_collection[(section, collection)] = set(values)
    if identity in seen:
        return
    seen.add(identity)
    values.append(identity)


def _merge_sources(base: list, incoming: list, tracker: dict) -> list:
    result = copy.deepcopy(base)
    by_id = {item["id"]: index for index, item in enumerate(result)}
    for source in incoming:
        sid = source["id"]
        if sid not in by_id:
            by_id[sid] = len(result)
            result.append(copy.deepcopy(source))
            _track(tracker, "added", "sources", sid)
            continue
        index = by_id[sid]
        merged = _merge_record(result[index], source, f"source[{sid}]")
        if _canon(merged) != _canon(result[index]):
            result[index] = merged
            _track(tracker, "enriched", "sources", sid)
    return result


def _merge_metadata(base: dict, incoming: dict, sources: list) -> dict:
    result = copy.deepcopy(base)
    result["sources"] = copy.deepcopy(sources)
    for key, value in incoming.items():
        if key in DERIVED_METADATA | {"sources", "mode", "distillation_date", "distiller_spec_version"}:
            continue
        if key not in result:
            result[key] = copy.deepcopy(value)
        else:
            result[key] = _merge_value(result[key], value, f"metadata.{key}")

    versions = {
        str(base.get("distiller_spec_version")),
        str(incoming.get("distiller_spec_version")),
    }
    if not versions <= SUPPORTED_SPECS:
        raise MergeValidationError(f"unsupported format versions in merge: {sorted(versions)!r}")
    result["distiller_spec_version"] = "1.1" if "1.1" in versions else "1.0"
    result["mode"] = "merge"
    dates = [value for value in (base.get("distillation_date"), incoming.get("distillation_date")) if value]
    if dates:
        result["distillation_date"] = max(str(value) for value in dates)
    return result


def _remap_citations(doc: dict, merged_sources: list) -> None:
    incoming_sources = doc.get("metadata", {}).get("sources", []) or []
    incoming_order = [source["id"] for source in incoming_sources]
    output_positions = {source["id"]: index for index, source in enumerate(merged_sources, 1)}

    def remap_text(text: object) -> object:
        if not isinstance(text, str):
            return text

        def replacement(match: re.Match) -> str:
            digits = match.group(1)
            if len(digits) > MAX_CITATION_DIGITS:
                raise MergeValidationError(
                    f"incoming citation marker has more than {MAX_CITATION_DIGITS} digits"
                )
            old = int(digits)
            if old < 1 or old > len(incoming_order):
                raise MergeValidationError(f"incoming citation marker [{old}] is out of range")
            return f"[{output_positions[incoming_order[old - 1]]}]"

        return MARKER.sub(replacement, text)

    for node in doc.get("nodes", []) or []:
        node["statements"] = [remap_text(value) for value in (node.get("statements", []) or [])]
        for citation in node.get("citations", []) or []:
            source = citation.get("source")
            if source not in output_positions:
                raise MergeValidationError(f"incoming citation source {source!r} was not merged")
            citation["n"] = output_positions[source]
    for claim in doc.get("claims", []) or []:
        claim["statement"] = remap_text(claim.get("statement"))


def _merge_clusters(base: list, incoming: list, tracker: dict) -> list:
    result = copy.deepcopy(base)
    by_id = {item["id"]: index for index, item in enumerate(result)}
    for cluster in incoming:
        cid = cluster["id"]
        if cid not in by_id:
            by_id[cid] = len(result)
            result.append(copy.deepcopy(cluster))
            _track(tracker, "added", "clusters", cid)
            continue
        index = by_id[cid]
        merged = _merge_record(result[index], cluster, f"cluster[{cid}]")
        if _canon(merged) != _canon(result[index]):
            result[index] = merged
            _track(tracker, "enriched", "clusters", cid)
    return result


def _node_maps(nodes: list) -> tuple[dict[str, int], dict[str, int]]:
    by_id: dict[str, int] = {}
    by_resource: dict[str, int] = {}
    for index, node in enumerate(nodes):
        nid = node["id"]
        if nid in by_id:
            raise PayloadConflictError(f"duplicate node id {nid!r} makes identity ambiguous")
        by_id[nid] = index
        resource = node.get("resource")
        if resource:
            if resource in by_resource:
                other = nodes[by_resource[resource]]["id"]
                raise PayloadConflictError(
                    f"resource {resource!r} identifies both {other!r} and {nid!r}"
                )
            by_resource[resource] = index
    return by_id, by_resource


def _merge_nodes(base: list, incoming: list, tracker: dict) -> tuple[list, dict[str, str]]:
    result = copy.deepcopy(base)
    by_id, by_resource = _node_maps(result)
    aliases: dict[str, str] = {}

    for original in incoming:
        node = copy.deepcopy(original)
        incoming_id = node["id"]
        resource = node.get("resource")
        index = by_resource.get(resource) if resource else None
        if index is None and incoming_id in by_id:
            index = by_id[incoming_id]
            old_resource = result[index].get("resource")
            if old_resource and resource and old_resource != resource:
                raise PayloadConflictError(
                    f"node id {incoming_id!r} has conflicting resources "
                    f"{old_resource!r} and {resource!r}"
                )

        if index is None:
            if incoming_id in by_id:
                raise PayloadConflictError(f"node id collision for {incoming_id!r}")
            aliases[incoming_id] = incoming_id
            by_id[incoming_id] = len(result)
            if resource:
                if resource in by_resource:
                    raise PayloadConflictError(f"duplicate node resource {resource!r}")
                by_resource[resource] = len(result)
            result.append(node)
            _track(tracker, "added", "nodes", incoming_id)
            continue

        output_id = result[index]["id"]
        aliases[incoming_id] = output_id
        if incoming_id != output_id:
            tracker["node_aliases"][incoming_id] = output_id
        node["id"] = output_id
        merged = _merge_record(result[index], node, f"node[{output_id}]")
        if _canon(merged) != _canon(result[index]):
            old_resource = result[index].get("resource")
            result[index] = merged
            _track(tracker, "enriched", "nodes", output_id)
            if merged.get("resource") and not old_resource:
                by_resource[merged["resource"]] = index
                tracker["resource_enrichments"][output_id] = merged["resource"]
    return result, aliases


def _remap_node_references(doc: dict, aliases: dict[str, str]) -> None:
    def mapped(value: object) -> object:
        return aliases.get(value, value) if isinstance(value, str) else value

    for edge in doc.get("edges", []) or []:
        edge["source"] = mapped(edge.get("source"))
        edge["target"] = mapped(edge.get("target"))
        if "id" in edge:
            # IDs are a computed projection of endpoints, so aliasing must update them too.
            edge["id"] = bg.canonical_edge_id(edge)
    for fact in doc.get("facts", []) or []:
        if "concept" in fact:
            fact["concept"] = mapped(fact.get("concept"))
    for claim in doc.get("claims", []) or []:
        claim["node"] = mapped(claim.get("node"))
    for chunk in doc.get("chunks", []) or []:
        chunk["concepts"] = [mapped(value) for value in (chunk.get("concepts", []) or [])]


def _edge_key(edge: dict) -> tuple[str, str, str]:
    source, target = edge.get("source"), edge.get("target")
    if edge.get("type") == "tension":
        source, target = sorted((source, target))
    return str(source), str(target), str(edge.get("type"))


def _edge_label(edge: dict) -> str:
    source, target, kind = _edge_key(edge)
    return f"{source}|{kind}|{target}"


def _require_canonical_edge_ids(edges: list, label: str) -> None:
    for index, edge in enumerate(edges):
        if "id" in edge and edge.get("id") != bg.canonical_edge_id(edge):
            raise PayloadConflictError(
                f"{label} edge[{index}].id {edge.get('id')!r} is not the canonical derived id "
                f"{bg.canonical_edge_id(edge)!r}"
            )


def _merge_edges(base: list, incoming: list, tracker: dict) -> list:
    result = copy.deepcopy(base)
    by_key: dict[tuple[str, str, str], int] = {}
    explicit_ids: dict[str, tuple[str, str, str]] = {}
    for index, item in enumerate(result):
        key = _edge_key(item)
        if key in by_key:
            raise PayloadConflictError(f"base graph has duplicate edge identity {key!r}")
        by_key[key] = index
        explicit = item.get("id")
        if isinstance(explicit, str):
            if explicit in explicit_ids and explicit_ids[explicit] != key:
                raise PayloadConflictError(f"base graph has duplicate edge id {explicit!r}")
            explicit_ids[explicit] = key
    for edge in incoming:
        key = _edge_key(edge)
        explicit = edge.get("id")
        if explicit and explicit in explicit_ids and explicit_ids[explicit] != key:
            raise PayloadConflictError(
                f"edge id {explicit!r} refers to both {explicit_ids[explicit]!r} and {key!r}"
            )
        if key not in by_key:
            by_key[key] = len(result)
            result.append(copy.deepcopy(edge))
            if explicit:
                explicit_ids[explicit] = key
            _track(tracker, "added", "edges", _edge_label(edge))
            continue
        index = by_key[key]
        merged = _merge_record(
            result[index], edge, f"edge[{_edge_label(edge)}]", ignore={"id"}
        )
        if _canon(merged) != _canon(result[index]):
            result[index] = merged
            _track(tracker, "enriched", "edges", _edge_label(edge))
    return result


def _conflicting_fact_id(fact_id: str, fact: dict) -> str:
    digest = hashlib.sha256(_canon(fact).encode("utf-8")).hexdigest()
    return f"{fact_id}--conflict-{digest}"


def _logical_fact_key(fact: dict) -> tuple | None:
    concept, metric = fact.get("concept"), fact.get("metric")
    if not concept or not metric:
        return None
    return concept, metric, _canon(fact.get("temporal") or {})


def _generated_conflict(left: dict, right: dict) -> dict:
    facts = sorted((left["id"], right["id"]))
    digest = hashlib.sha256(_canon(facts).encode("utf-8")).hexdigest()
    evidence = _stable_union(left.get("evidence", []) or [], right.get("evidence", []) or [])
    return {
        "id": f"fact-conflict-{digest}",
        "facts": facts,
        "relation": "tension",
        "reason": "Conflicting fact payloads were retained during deterministic merge; review required.",
        "evidence": evidence,
    }


def _merge_facts(
    base: list,
    incoming: list,
    tracker: dict,
    *,
    conflict_mode: str,
) -> tuple[list, dict[str, str], list[dict]]:
    result = copy.deepcopy(base)
    by_id = {item["id"]: index for index, item in enumerate(result)}
    aliases: dict[str, str] = {}
    generated: list[dict] = []
    generated_pairs: set[frozenset] = set()

    # Positions in ``result`` grouped by logical fact key, so that finding the
    # facts a newcomer contradicts is a lookup instead of a scan over everything
    # merged so far, and ``_logical_fact_key`` is computed once per record rather
    # than twice per pair. Buckets stay sorted, so the facts are still visited in
    # ``result`` order and the conflict records keep their observable sequence.
    hashed: dict[tuple, list[int]] = {}
    unhashable: list[tuple[tuple, list[int]]] = []
    keys: list[tuple | None] = []

    def bucket(key: tuple) -> list[int]:
        """The position list for ``key``, created on first use.

        The validator requires ``concept`` and ``metric`` to be strings, so an
        unhashable key is only reachable through the Python API. It gets the old
        linear answer from a side list. No JSON value that cannot be hashed can
        equal one that can -- a list or dict never equals a string or a number --
        but a non-JSON type could (``{1} == frozenset({1})``), so once any
        unhashable key exists both stores are consulted rather than assumed
        disjoint. Both scans stay empty for anything a document can express.
        """
        try:
            hash(key)
        except TypeError:
            hashable = False
        else:
            hashable = True
        for existing, positions in unhashable:
            if existing == key:
                return positions
        if hashable:
            return hashed.setdefault(key, [])
        for existing, positions in hashed.items():
            if existing == key:
                return positions
        positions = []
        unhashable.append((key, positions))
        return positions

    def index_fact(position: int) -> None:
        key = _logical_fact_key(result[position])
        keys.append(key)
        if key is not None:
            bisect.insort(bucket(key), position)

    def reindex_fact(position: int) -> None:
        key = _logical_fact_key(result[position])
        if key == keys[position]:
            return
        if keys[position] is not None:
            bucket(keys[position]).remove(position)
        keys[position] = key
        if key is not None:
            bisect.insort(bucket(key), position)

    def contradicted_by(fact: dict) -> list[dict]:
        key = _logical_fact_key(fact)
        if key is None:
            return []
        value = fact.get("value")
        return [
            result[position]
            for position in bucket(key)
            if result[position].get("value") != value
        ]

    for position in range(len(result)):
        index_fact(position)

    def note_conflict(left: dict, right: dict) -> None:
        pair = frozenset((left["id"], right["id"]))
        if pair not in generated_pairs:
            generated_pairs.add(pair)
            generated.append(_generated_conflict(left, right))

    for original in incoming:
        fact = copy.deepcopy(original)
        fid = fact["id"]
        if fid in by_id:
            index = by_id[fid]
            try:
                merged = _merge_record(result[index], fact, f"fact[{fid}]")
            except PayloadConflictError:
                if conflict_mode != "record":
                    raise
                retained_id = _conflicting_fact_id(fid, fact)
                fact["id"] = retained_id
                aliases[fid] = retained_id
                if retained_id in by_id:
                    if _canon(result[by_id[retained_id]]) != _canon(fact):
                        raise PayloadConflictError(
                            f"generated fact id {retained_id!r} collides with another payload"
                        )
                    retained = result[by_id[retained_id]]
                else:
                    for prior in contradicted_by(fact):
                        note_conflict(prior, fact)
                    by_id[retained_id] = len(result)
                    result.append(fact)
                    index_fact(len(result) - 1)
                    retained = fact
                    _track(tracker, "added", "facts", retained_id)
                note_conflict(result[index], retained)
                continue
            aliases[fid] = fid
            if _canon(merged) != _canon(result[index]):
                result[index] = merged
                reindex_fact(index)
                _track(tracker, "enriched", "facts", fid)
            continue

        aliases[fid] = fid
        for prior in contradicted_by(fact):
            note_conflict(prior, fact)
        by_id[fid] = len(result)
        result.append(fact)
        index_fact(len(result) - 1)
        _track(tracker, "added", "facts", fid)
    return result, aliases, generated


def _merge_id_collection(
    name: str,
    base: list,
    incoming: list,
    tracker: dict,
    *,
    immutable_lists: set[str] | None = None,
) -> list:
    result = copy.deepcopy(base)
    by_id: dict[str, int] = {}
    for index, item in enumerate(result):
        rid = item["id"]
        if rid in by_id:
            raise PayloadConflictError(f"base graph has duplicate {name} id {rid!r}")
        by_id[rid] = index
    for record in incoming:
        rid = record["id"]
        if rid not in by_id:
            by_id[rid] = len(result)
            result.append(copy.deepcopy(record))
            _track(tracker, "added", name, rid)
            continue
        index = by_id[rid]
        merged = _merge_record(
            result[index], record, f"{name}[{rid}]", immutable_lists=immutable_lists
        )
        if _canon(merged) != _canon(result[index]):
            result[index] = merged
            _track(tracker, "enriched", name, rid)
    return result


def _normalize_conflict(conflict: dict, fact_aliases: dict[str, str]) -> dict:
    """Rewrite fact aliases without changing the conflict's immutable reference order."""
    result = copy.deepcopy(conflict)
    refs = [fact_aliases.get(value, value) for value in (result.get("facts", []) or [])]
    result["facts"] = refs
    return result


def _add_generated_conflicts(existing: list, generated: list, tracker: dict) -> list:
    result = copy.deepcopy(existing)
    by_pair = {
        (item.get("relation"), frozenset(item.get("facts", []))): index
        for index, item in enumerate(result)
    }
    by_id = {item["id"]: index for index, item in enumerate(result)}
    # The recorded list keeps its order; membership comes from a set, because a
    # scan per generated conflict would be quadratic in a number that is itself
    # quadratic in the facts.
    recorded = tracker["recorded_fact_conflicts"]
    recorded_ids = set(recorded)
    for conflict in generated:
        pair = (conflict["relation"], frozenset(conflict["facts"]))
        if pair in by_pair:
            index = by_pair[pair]
            existing_id = result[index]["id"]
            if existing_id not in recorded_ids:
                recorded_ids.add(existing_id)
                recorded.append(existing_id)
            merged_evidence = _stable_union(
                result[index].get("evidence", []) or [], conflict.get("evidence", []) or []
            )
            if merged_evidence != (result[index].get("evidence", []) or []):
                result[index]["evidence"] = merged_evidence
                _track(tracker, "enriched", "fact_conflicts", result[index]["id"])
            continue
        cid = conflict["id"]
        if cid in by_id and _canon(result[by_id[cid]]) != _canon(conflict):
            raise PayloadConflictError(f"generated fact conflict id {cid!r} collides")
        if cid not in by_id:
            by_id[cid] = len(result)
            by_pair[pair] = len(result)
            result.append(copy.deepcopy(conflict))
            _track(tracker, "added", "fact_conflicts", cid)
            if cid not in recorded_ids:
                recorded_ids.add(cid)
                recorded.append(cid)
    return result


def _merge_unknown_top_level(base: dict, incoming: dict, output: dict) -> None:
    for key in sorted((set(base) | set(incoming)) - KNOWN_TOP_LEVEL):
        left = base.get(key, _MISSING)
        right = incoming.get(key, _MISSING)
        if left is _MISSING:
            output[key] = copy.deepcopy(right)
        elif right is _MISSING:
            output[key] = copy.deepcopy(left)
        else:
            output[key] = _merge_value(left, right, key)


def _identity_audit_baseline(base: dict, resource_enrichments: dict[str, str]) -> dict:
    """Align identity-only null→resource enrichment for the validator's prev lookup.

    The merge algorithm has already preserved the complete prior node payload.  The
    validator keys a prior node by ``id`` when resource is null but the enriched output by
    ``resource``; this narrow baseline projection lets its payload audit follow the same
    node rather than misreporting that it was dropped.
    """
    baseline = copy.deepcopy(base)
    for node in baseline.get("nodes", []) or []:
        if not node.get("resource") and node.get("id") in resource_enrichments:
            node["resource"] = resource_enrichments[node["id"]]
    return baseline


def merge_documents(
    base: dict,
    incoming: dict,
    *,
    fact_conflicts: str = "error",
) -> tuple[dict, dict]:
    """Return ``(merged_graph, machine_diff)`` without mutating either input."""
    if fact_conflicts not in {"error", "record"}:
        raise ValueError("fact_conflicts must be 'error' or 'record'")
    _require_bounded_depth(base, "base graph")
    _require_bounded_depth(incoming, "incoming graph")
    base_doc = copy.deepcopy(base)
    incoming_doc = copy.deepcopy(incoming)
    base_input_hash = _sha_document(base_doc)
    incoming_input_hash = _sha_document(incoming_doc)
    base_validation = _require_valid(base_doc, "base graph")
    incoming_validation = _require_valid(incoming_doc, "incoming graph")
    _require_canonical_edge_ids(base_doc.get("edges", []) or [], "base")

    tracker = {
        "added": {},
        "enriched": {},
        "node_aliases": {},
        "resource_enrichments": {},
        "fact_aliases": {},
        "recorded_fact_conflicts": [],
        # Scratch for ``_track``: membership sets beside the change lists above.
        # Never read into the diff; the underscore marks it as not reportable.
        "_seen": {},
    }
    sources = _merge_sources(
        base_doc["metadata"].get("sources", []),
        incoming_doc["metadata"].get("sources", []),
        tracker,
    )
    _remap_citations(incoming_doc, sources)

    output: dict = {}
    if "@context" in base_doc or "@context" in incoming_doc:
        output["@context"] = _merge_value(
            base_doc.get("@context", {}), incoming_doc.get("@context", {}), "@context"
        )
    output["metadata"] = _merge_metadata(base_doc["metadata"], incoming_doc["metadata"], sources)
    output["clusters"] = _merge_clusters(
        base_doc.get("clusters", []), incoming_doc.get("clusters", []), tracker
    )
    output["nodes"], aliases = _merge_nodes(
        base_doc.get("nodes", []), incoming_doc.get("nodes", []), tracker
    )
    _remap_node_references(incoming_doc, aliases)
    _require_canonical_edge_ids(incoming_doc.get("edges", []) or [], "incoming")
    output["edges"] = _merge_edges(
        base_doc.get("edges", []), incoming_doc.get("edges", []), tracker
    )

    facts, fact_aliases, generated_conflicts = _merge_facts(
        base_doc.get("facts", []), incoming_doc.get("facts", []), tracker,
        conflict_mode=fact_conflicts,
    )
    if "facts" in base_doc or "facts" in incoming_doc or facts:
        output["facts"] = facts
    tracker["fact_aliases"] = {
        old: new for old, new in fact_aliases.items() if old != new
    }

    for name in ("evidence", "claims", "assessments"):
        incoming_records = incoming_doc.get(name, []) or []
        if name == "claims":
            # Node aliases and citation-number remapping have already been applied.
            incoming_records = incoming_doc.get(name, []) or []
        merged = _merge_id_collection(
            name, base_doc.get(name, []) or [], incoming_records, tracker
        )
        if name in base_doc or name in incoming_doc or merged:
            output[name] = merged

    incoming_conflicts = [
        _normalize_conflict(item, fact_aliases)
        for item in (incoming_doc.get("fact_conflicts", []) or [])
    ]
    conflicts = _merge_id_collection(
        "fact_conflicts",
        base_doc.get("fact_conflicts", []) or [],
        incoming_conflicts,
        tracker,
        immutable_lists={"facts"},
    )
    conflicts = _add_generated_conflicts(conflicts, generated_conflicts, tracker)
    if "fact_conflicts" in base_doc or "fact_conflicts" in incoming_doc or conflicts:
        output["fact_conflicts"] = conflicts

    chunks = _merge_id_collection(
        "chunks", base_doc.get("chunks", []) or [], incoming_doc.get("chunks", []) or [], tracker
    )
    if "chunks" in base_doc or "chunks" in incoming_doc or chunks:
        output["chunks"] = chunks
    questions = _stable_union(
        base_doc.get("open_questions", []) or [], incoming_doc.get("open_questions", []) or []
    )
    if "open_questions" in base_doc or "open_questions" in incoming_doc or questions:
        output["open_questions"] = questions
    _merge_unknown_top_level(base_doc, incoming_doc, output)

    if any(output.get(name) for name in ("evidence", "claims", "assessments", "fact_conflicts")):
        output["metadata"]["distiller_spec_version"] = "1.1"
    bg.recompute(output)
    audit_baseline = _identity_audit_baseline(base_doc, tracker["resource_enrichments"])
    merged_validation = _require_valid(output, "merged graph", prev=audit_baseline)

    collections = (
        "sources", "clusters", "nodes", "edges", "facts", "chunks", "evidence",
        "claims", "assessments", "fact_conflicts",
    )
    base_counts = {
        name: len(base_doc["metadata"].get("sources", [])) if name == "sources"
        else len(base_doc.get(name, []) or [])
        for name in collections
    }
    incoming_counts = {
        name: len(incoming_doc["metadata"].get("sources", [])) if name == "sources"
        else len(incoming_doc.get(name, []) or [])
        for name in collections
    }
    output_counts = {
        name: len(output["metadata"].get("sources", [])) if name == "sources"
        else len(output.get(name, []) or [])
        for name in collections
    }
    diff = {
        "format": "knowledge-distiller-merge-diff",
        "version": "1.0",
        "network_access": False,
        "semantic_accuracy_evaluated": False,
        "fact_conflict_policy": fact_conflicts,
        "hashes": {
            "base_canonical_sha256": base_input_hash,
            "incoming_canonical_sha256": incoming_input_hash,
            "output_canonical_sha256": _sha_document(output),
        },
        "spec_version": output["metadata"]["distiller_spec_version"],
        "counts": {"base": base_counts, "incoming": incoming_counts, "output": output_counts},
        "added": tracker["added"],
        "enriched": tracker["enriched"],
        "aliases": {
            "nodes": tracker["node_aliases"],
            "facts": tracker["fact_aliases"],
            "resource_enrichments": tracker["resource_enrichments"],
        },
        "recorded_fact_conflicts": tracker["recorded_fact_conflicts"],
        "validation": {
            "base": base_validation,
            "incoming": incoming_validation,
            "merged": merged_validation,
        },
    }
    return output, diff


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _read_json(path: Path) -> tuple[dict, bytes]:
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise MergeError(f"could not inspect {path}: {exc}") from exc
    if size > MAX_INPUT_BYTES:
        raise MergeError(f"input exceeds {MAX_INPUT_BYTES} byte safety limit: {path}")
    try:
        raw = path.read_bytes()
        value = strict_json.loads(raw, source=str(path))
    except (OSError, strict_json.StrictJsonError) as exc:
        raise MergeError(f"could not read JSON from {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise MergeError(f"graph must be a JSON object: {path}")
    return value, raw


def _prepare_target(path: Path, *, force: bool) -> Path:
    path = path.expanduser().absolute()
    if path.is_symlink():
        raise WriteSafetyError(f"refusing symlink output target: {path}")
    if path.exists() and not path.is_file():
        raise WriteSafetyError(f"output target is not a regular file: {path}")
    if path.exists() and not force:
        raise WriteSafetyError(f"output already exists (use --force explicitly): {path}")
    if path.parent.is_symlink():
        raise WriteSafetyError(f"refusing symlink output directory: {path.parent}")
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.parent.is_dir():
        raise WriteSafetyError(f"output parent is not a directory: {path.parent}")
    return path


def _atomic_write(path: Path, data: bytes) -> None:
    tmp_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".merge-", delete=False) as handle:
            tmp_name = handle.name
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
            # On the descriptor, not on the name: the mode belongs to
            # the file just written, not to whatever carries that name
            # by the time the call runs.
            os.fchmod(handle.fileno(), 0o644)
        os.replace(tmp_name, path)
        tmp_name = None
    finally:
        if tmp_name is not None:
            try:
                Path(tmp_name).unlink()
            except FileNotFoundError:
                pass


def _atomic_create(path: Path, data: bytes) -> None:
    """Publish an immutable archive only if its final name does not yet exist."""
    tmp_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".archive-", delete=False) as handle:
            tmp_name = handle.name
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
            # On the descriptor, not on the name: the mode belongs to
            # the file just written, not to whatever carries that name
            # by the time the call runs.
            os.fchmod(handle.fileno(), 0o644)
        try:
            os.link(tmp_name, path)
        except FileExistsError as exc:
            raise WriteSafetyError(f"archive target already exists: {path}") from exc
    finally:
        if tmp_name is not None:
            try:
                Path(tmp_name).unlink()
            except FileNotFoundError:
                pass


def _archive_prefix(base_path: Path) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", base_path.name).strip(".-") or "knowledge"
    if len(safe.encode("utf-8")) > 80:
        digest = hashlib.sha256(base_path.name.encode("utf-8")).hexdigest()[:12]
        safe = safe[:60] + "-" + digest
    return safe


def _archive_prior(
    base_path: Path,
    raw: bytes,
    versions_dir: Path,
    *,
    max_versions: int,
) -> Path:
    if isinstance(max_versions, bool) or not isinstance(max_versions, int) or not 1 <= max_versions <= 5:
        raise WriteSafetyError("max_versions must be an integer between 1 and 5")
    versions_dir = versions_dir.expanduser().absolute()
    if versions_dir.is_symlink():
        raise WriteSafetyError(f"refusing symlink versions directory: {versions_dir}")
    versions_dir.mkdir(parents=True, exist_ok=True)
    if not versions_dir.is_dir():
        raise WriteSafetyError(f"versions path is not a directory: {versions_dir}")
    prefix = _archive_prefix(base_path)
    active: list[tuple[int, Path]] = []
    for path in versions_dir.glob(f"{prefix}.v*.json"):
        match = ARCHIVE_NAME.search(path.name)
        if match and path.is_file() and not path.is_symlink():
            active.append((int(match.group(1)), path))

    retired = versions_dir / "retired"
    while len(active) >= max_versions:
        active.sort(key=lambda item: item[0])
        _, oldest = active.pop(0)
        if retired.is_symlink():
            raise WriteSafetyError(f"refusing symlink retired archive directory: {retired}")
        retired.mkdir(exist_ok=True)
        destination = retired / oldest.name
        if destination.exists() or destination.is_symlink():
            raise WriteSafetyError(f"retired archive target already exists: {destination}")
        os.replace(oldest, destination)

    sequences = [number for number, _ in active]
    if retired.is_dir() and not retired.is_symlink():
        for path in retired.glob(f"{prefix}.v*.json"):
            match = ARCHIVE_NAME.search(path.name)
            if match:
                sequences.append(int(match.group(1)))
    sequence = max(sequences, default=0) + 1
    digest = _sha_bytes(raw)[:12]
    target = versions_dir / f"{prefix}.v{sequence:04d}.{digest}.json"
    _atomic_create(target, raw)
    return target


def _default_audit_paths(output_path: Path) -> tuple[Path, Path]:
    """Return collision-resistant, predictable siblings for both required deltas."""
    name = output_path.name
    base = name[:-5] if name.lower().endswith(".json") else name
    return (
        output_path.with_name(f"{base}.diff.json"),
        output_path.with_name(f"{base}.diff.md"),
    )


def render_markdown_diff(diff: dict) -> str:
    """Render a deterministic human-readable projection of the machine delta."""
    collections = (
        "sources", "clusters", "nodes", "edges", "facts", "chunks", "evidence",
        "claims", "assessments", "fact_conflicts",
    )
    lines = [
        "# Knowledge Distiller merge delta",
        "",
        "This report is a deterministic projection of the companion JSON delta. It records",
        "format conformance and payload changes; it does not evaluate semantic accuracy.",
        "",
        "## Content hashes",
        "",
        f"- Base: `{diff['hashes']['base_canonical_sha256']}`",
        f"- Incoming: `{diff['hashes']['incoming_canonical_sha256']}`",
        f"- Output: `{diff['hashes']['output_canonical_sha256']}`",
        "",
        "## Counts",
        "",
        "| Collection | Base | Incoming | Output |",
        "|---|---:|---:|---:|",
    ]
    for collection in collections:
        lines.append(
            f"| {collection} | {diff['counts']['base'][collection]} | "
            f"{diff['counts']['incoming'][collection]} | {diff['counts']['output'][collection]} |"
        )

    lines.extend(["", "## Changes", ""])
    for section in ("added", "enriched"):
        lines.extend([f"### {section.title()}", ""])
        records = diff.get(section, {})
        if not records:
            lines.extend(["None.", ""])
            continue
        for collection in sorted(records):
            encoded = json.dumps(records[collection], ensure_ascii=False, separators=(",", ":"))
            lines.append(f"- {collection}: {encoded}")
        lines.append("")

    lines.extend([
        "### Aliases",
        "",
        "    " + json.dumps(diff.get("aliases", {}), ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        "",
        "### Recorded fact conflicts",
        "",
        "    " + json.dumps(diff.get("recorded_fact_conflicts", []), ensure_ascii=False, separators=(",", ":")),
        "",
        "## Validation",
        "",
        f"- Base valid: `{str(diff['validation']['base']['ok']).lower()}`",
        f"- Incoming valid: `{str(diff['validation']['incoming']['ok']).lower()}`",
        f"- Output valid: `{str(diff['validation']['merged']['ok']).lower()}`",
        "- Semantic accuracy evaluated: `false`",
        "- Network access: `false`",
        "",
        "## Canonical machine delta",
        "",
    ])
    canonical = json.dumps(diff, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    lines.append("    " + canonical)
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Safely merge two Knowledge Distiller graphs")
    parser.add_argument("base", help="validated prior .knowledge.json")
    parser.add_argument("incoming", help="validated graph to add")
    parser.add_argument("-o", "--output", required=True, help="explicit merged output path")
    parser.add_argument(
        "--diff-report",
        help="machine-readable JSON delta path (default: <output-without-.json>.diff.json)",
    )
    parser.add_argument(
        "--markdown-diff",
        help="human-readable Markdown delta path (default: <output-without-.json>.diff.md)",
    )
    parser.add_argument(
        "--fact-conflicts", choices=("error", "record"), default="error",
        help="retain same-id fact payload conflicts explicitly (default: error)",
    )
    parser.add_argument(
        "--archive", action="store_true",
        help="deprecated compatibility flag; prior bytes are always archived",
    )
    parser.add_argument("--versions-dir", help="custom directory for the required prior-version archive")
    parser.add_argument(
        "--max-versions", type=int, default=5,
        help="active archives before retirement, between 1 and 5 (default: 5)",
    )
    parser.add_argument("--force", action="store_true", help="allow replacing an existing regular output")
    args = parser.parse_args(argv)

    if not 1 <= args.max_versions <= 5:
        parser.error("--max-versions must be between 1 and 5")

    base_path = Path(args.base).expanduser().absolute()
    incoming_path = Path(args.incoming).expanduser().absolute()
    output_path = Path(args.output).expanduser().absolute()
    default_json_diff, default_markdown_diff = _default_audit_paths(output_path)
    diff_path = (
        Path(args.diff_report).expanduser().absolute()
        if args.diff_report else default_json_diff
    )
    markdown_diff_path = (
        Path(args.markdown_diff).expanduser().absolute()
        if args.markdown_diff else default_markdown_diff
    )
    versions_dir = (
        Path(args.versions_dir).expanduser().absolute()
        if args.versions_dir else output_path.parent / "versions"
    )

    try:
        if output_path == incoming_path:
            raise WriteSafetyError("output must not overwrite the incoming graph")
        if output_path == base_path and not args.force:
            raise WriteSafetyError(
                "in-place merge requires --force; the prior bytes will be archived automatically"
            )
        artifact_paths = {output_path, diff_path, markdown_diff_path}
        if len(artifact_paths) != 3:
            raise WriteSafetyError("output, JSON delta and Markdown delta must use different paths")
        if diff_path in {base_path, incoming_path} or markdown_diff_path in {base_path, incoming_path}:
            raise WriteSafetyError("delta reports must not overwrite either input graph")
        base_doc, base_raw = _read_json(base_path)
        incoming_doc, _ = _read_json(incoming_path)
        merged, diff = merge_documents(
            base_doc, incoming_doc, fact_conflicts=args.fact_conflicts
        )

        prepared_output = _prepare_target(output_path, force=args.force)
        prepared_diff = _prepare_target(diff_path, force=args.force)
        prepared_markdown_diff = _prepare_target(markdown_diff_path, force=args.force)
        _archive_prior(
            base_path, base_raw, versions_dir, max_versions=args.max_versions
        )
        _atomic_write(prepared_output, _json_bytes(merged))
        _atomic_write(prepared_diff, _json_bytes(diff))
        _atomic_write(prepared_markdown_diff, render_markdown_diff(diff).encode("utf-8"))
    except MergeError as exc:
        print(f"merge failed: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"merge failed during local file operation: {exc}", file=sys.stderr)
        return 1

    print(
        f"merged {base_path.name} + {incoming_path.name} -> {prepared_output} "
        f"({len(merged.get('nodes', []))} nodes, {len(merged.get('edges', []))} edges); "
        f"audit: {prepared_diff.name}, {prepared_markdown_diff.name}, {versions_dir}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
