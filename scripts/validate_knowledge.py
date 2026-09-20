#!/usr/bin/env python3
"""Validate a Knowledge Distiller ``.knowledge.json`` deterministically.

The standard-library checks in this module enforce the complete core contract. The optional
``jsonschema`` package is an additional mirror check, never a prerequisite for meaningful
validation. ``quality_score`` is retained as a compatibility alias for the more accurate
name ``conformance_score``; neither score claims semantic truth or extraction accuracy.
The CLI rejects ambiguous/non-portable JSON before applying either validation layer.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import sys
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

import strict_json

EDGE_TYPES = {"uses", "enables", "based-on", "part-of", "tension", "replaces", "extends", "example-of"}
CONFIDENCE = {"high", "medium", "low"}
TEMPORAL_CONFIDENCE = {"explicit", "inferred", "unknown"}
ORIGINS = {"source_stated", "paraphrased", "synthesized", "rule_derived", "model_inferred", "human_added"}
REVIEW_STATUSES = {"unreviewed", "reviewed", "rejected"}
KNOWN_SPEC_VERSIONS = {"1.0", "1.1"}
KNOWN_TOP_KEYS = {
    "@context", "metadata", "clusters", "nodes", "edges", "facts", "chunks",
    "open_questions", "evidence", "claims", "assessments", "fact_conflicts",
}

KNOWN_METADATA_KEYS = {
    "title", "distiller_version", "distiller_spec_version", "sources", "distillation_date",
    "domain", "language", "depth", "mode", "quality_score", "conformance_score",
    "temporal_confidence", "concept_count", "relationship_count", "cluster_count", "fact_count",
}
KNOWN_SOURCE_KEYS = {
    "id", "file", "type", "date", "url", "title", "authors", "publisher", "version",
    "retrieved_at", "content_sha256", "license", "agents",
}
KNOWN_AGENT_KEYS = {"id", "label", "type", "role"}
KNOWN_CLUSTER_KEYS = {"id", "label", "description", "concepts"}
KNOWN_NODE_KEYS = {
    "id", "label", "cluster", "confidence", "definition", "relevance", "resource", "statements",
    "temporal", "sources", "citations", "note", "evidence", "claim_ids", "spatial_contexts",
}
KNOWN_TEMPORAL_KEYS = {"source_date", "source_period", "valid_from", "valid_until", "temporal_confidence"}
KNOWN_CITATION_KEYS = {"n", "source", "locator", "label", "url", "selector"}
KNOWN_EDGE_KEYS = {
    "id", "source", "target", "type", "label", "weight", "confidence", "temporal",
    "evidence", "origin", "explanation", "derivation", "spatial_contexts",
}
KNOWN_FACT_KEYS = {
    "id", "statement", "value", "temporal", "confidence", "source", "context", "concept",
    "metric", "evidence", "origin", "explanation", "derivation", "spatial_contexts",
}
KNOWN_CHUNK_KEYS = {
    "id", "text", "concepts", "temporal_scope", "token_estimate", "kind", "evidence",
    "origin", "derivation", "include_in_default_retrieval", "spatial_contexts",
}
KNOWN_EVIDENCE_KEYS = {
    "id", "source", "selector", "support", "attribution_basis", "excerpt", "excerpt_sha256",
    "derivation", "review_status",
}
KNOWN_SELECTOR_KEYS = {
    "type", "exact", "prefix", "suffix", "start", "end", "page", "fragment", "sheet",
    "cell_range", "xpath", "json_pointer", "file", "symbol",
}
KNOWN_CLAIM_KEYS = {
    "id", "node", "statement", "confidence", "origin", "evidence", "temporal",
    "spatial_contexts", "derivation", "review_status",
}
KNOWN_ASSESSMENT_KEYS = {"id", "dimension", "scope", "value", "assessor", "method", "assessed_at", "evidence"}
KNOWN_CONFLICT_KEYS = {"id", "facts", "relation", "reason", "evidence"}
KNOWN_SPATIAL_KEYS = {"role", "place", "basis", "confidence", "evidence", "derivation"}
KNOWN_PLACE_KEYS = {"id", "label", "kind", "identifiers", "geometry", "precision", "sensitive", "redacted"}
KNOWN_PLACE_ID_KEYS = {"scheme", "value"}
KNOWN_DERIVATION_KEYS = {"kind", "activity", "inputs", "rule", "summary", "review_status", "evidence"}

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schema" / "knowledge.schema.json"

CITATION_MARKER = re.compile(r"\[(\d+)\]")
# A citation number indexes metadata.sources, so a longer digit run is not a
# number that is merely out of range: converting it would raise before it
# could be judged. It is reported and never converted.
MAX_CITATION_DIGITS = 9
# The recursive audits below share the loader's structural bound, so a document
# the loader accepted is always shallow enough for them to finish.
MAX_STRUCTURE_DEPTH = strict_json.MAX_STRUCTURE_DEPTH
WIKILINK = re.compile(r"\[\[([^\]]+)\]\]")
SHA256 = re.compile(r"^(?:sha256:)?[A-Fa-f0-9]{64}$")
ISO_DATE = re.compile(
    r"^(?:FY[0-9]{4}|[0-9]{4}(?:-(?:Q[1-4]|[0-9]{2}(?:-[0-9]{2})?))?)"
    r"(?:/(?:FY[0-9]{4}|[0-9]{4}(?:-(?:Q[1-4]|[0-9]{2}(?:-[0-9]{2})?))?))?$"
)
SPEC_VERSION = re.compile(r"^[0-9]+\.[0-9]+$")
EVIDENCE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
NODE_ID = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
EMOJI = re.compile(
    "[" "\U0001F000-\U0001FAFF" "\U00002600-\U000027BF" "\U0001F1E6-\U0001F1FF" "\U00002190-\U000021FF" "]"
)
CHUNK_FORBIDDEN = {
    "wikilink": re.compile(r"\[\[|\]\]"),
    "bold/italic": re.compile(r"\*\*|__"),
    "code fence/inline": re.compile(r"`"),
    "arrow": re.compile(r"→|↔|⟶|->|<->"),
    "heading marker": re.compile(r"(?m)^\s{0,3}#{1,6}\s"),
}
SAFE_LINK_SCHEMES = {"http", "https"}
SAFE_ID_SCHEMES = {"http", "https", "urn", "iso", "wikidata"}
CREDENTIAL_QUERY_NAMES = {
    "accesskey", "apikey", "auth", "authorization", "awsaccesskeyid", "clientsecret",
    "credential", "jwt", "password", "passwd", "secret", "secretkey", "sessionid",
    "sig", "signature", "token",
}
CREDENTIAL_QUERY_SUFFIXES = ("token", "apikey", "secret", "password", "credential", "signature")
PRIVATE_REASONING_FIELDS = {
    "reasoning",
    "chainofthought",
    "reasoningtrace",
    "chainofthoughttrace",
    "hiddenreasoning",
    "hiddenthoughts",
    "hiddenscratchpad",
    "internalreasoning",
    "internalscratchpad",
    "privatereasoning",
    "privatescratchpad",
    "scratchpad",
    "cot",
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

    def conformance_score(self) -> int:
        """Syntactic/contract score only; semantic accuracy is deliberately out of scope."""
        return max(0, 100 - 10 * len(self.errors) - 2 * len(self.warnings))

    def quality_score(self) -> int:
        """Backward-compatible alias; do not interpret as semantic quality."""
        return self.conformance_score()


def _require(obj: object, keys: list[str], where: str, rep: Report) -> bool:
    if not isinstance(obj, dict):
        rep.err(f"{where}: expected object")
        return False
    for key in keys:
        if key not in obj:
            rep.err(f"{where}: missing required field '{key}'")
    return True


def _as_list(value: object, where: str, rep: Report) -> list:
    if value is None:
        rep.err(f"{where}: expected array")
        return []
    if not isinstance(value, list):
        rep.err(f"{where}: expected array")
        return []
    return value


def _warn_unknown(obj: object, known: set[str], where: str, rep: Report) -> None:
    if not isinstance(obj, dict):
        return
    for key in obj:
        if key not in known:
            if key == "authoritative_source":
                rep.warn(f"{where}.{key}: legacy intrinsic authority score is unsupported; use a provenance-backed assessment")
            elif _normalized_field_name(key) in PRIVATE_REASONING_FIELDS:
                # The recursive private-field audit emits the producer-contract error once.
                continue
            else:
                rep.warn(f"{where}: unknown extra key '{key}' (tolerated by consumers, ignored by this producer contract)")


def _normalized_field_name(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value).lower())


def _check_private_reasoning_fields(value: object, where: str, rep: Report) -> None:
    """Reject private reasoning/scratchpad payloads even inside extension objects.

    Unknown extension keys normally remain forward-compatible. Private chain-of-thought is a
    security exception: producers must use bounded ``derivation`` and ``explanation`` records.
    """
    seen: set[int] = set()
    too_deep: list[str] = []

    def visit(item: object, path: str, depth: int) -> None:
        if isinstance(item, (dict, list)):
            identity = id(item)
            if identity in seen:
                return
            seen.add(identity)
            if depth >= MAX_STRUCTURE_DEPTH:
                # Report the bound and stop descending instead of letting the walk
                # run the interpreter out of stack. Core validation still completes.
                if not too_deep:
                    too_deep.append(path)
                return
        if isinstance(item, dict):
            for key, child in item.items():
                child_path = f"{path}.{key}" if path else str(key)
                if _normalized_field_name(key) in PRIVATE_REASONING_FIELDS:
                    rep.err(
                        f"{child_path}: private reasoning/scratchpad fields are forbidden; "
                        "use bounded explanation/derivation metadata"
                    )
                visit(child, child_path, depth + 1)
        elif isinstance(item, list):
            for index, child in enumerate(item):
                visit(child, f"{path}[{index}]", depth + 1)

    visit(value, where, 0)
    if too_deep:
        rep.err(
            f"{too_deep[0]}: structure nesting exceeds the safe depth limit of "
            f"{MAX_STRUCTURE_DEPTH}; nothing below it was audited for private "
            "reasoning fields"
        )


def _check_unique_id(obj: object, seen: set[str], where: str, rep: Report) -> str | None:
    if not isinstance(obj, dict):
        rep.err(f"{where}: expected object")
        return None
    identifier = obj.get("id")
    if not isinstance(identifier, str) or not identifier:
        rep.err(f"{where}: missing 'id'")
        return None
    if identifier in seen:
        rep.err(f"{where}: duplicate id {identifier!r}")
    seen.add(identifier)
    return identifier


def _check_hash(value: object, where: str, rep: Report) -> None:
    if value is not None and (not isinstance(value, str) or not SHA256.fullmatch(value)):
        rep.err(f"{where}: expected a 64-hex SHA-256 value, optionally prefixed with 'sha256:'")


def _check_string(value: object, where: str, rep: Report, *, nonempty: bool = False, nullable: bool = False) -> None:
    if value is None and nullable:
        return
    if not isinstance(value, str) or (nonempty and not value):
        qualifier = "non-empty " if nonempty else ""
        rep.err(f"{where}: expected {qualifier}string")


def _check_enum(value: object, allowed: set[str], where: str, rep: Report, *, nullable: bool = False) -> None:
    if value is None and nullable:
        return
    if not isinstance(value, str) or value not in allowed:
        rep.err(f"{where}: invalid value {value!r}; expected one of {sorted(allowed)}")


def _check_integer(value: object, where: str, rep: Report, *, minimum: int = 0, maximum: int | None = None) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum or (maximum is not None and value > maximum):
        bounds = f"{minimum}..{maximum}" if maximum is not None else f">= {minimum}"
        rep.err(f"{where}: expected integer {bounds}")


def _check_iso_date(value: object, where: str, rep: Report, *, nullable: bool = True) -> None:
    if value is None and nullable:
        return
    if not isinstance(value, str) or not ISO_DATE.fullmatch(value):
        rep.err(f"{where}: expected a supported ISO-like date/period")


def _check_datetime(value: object, where: str, rep: Report) -> None:
    if value is None:
        return
    if not isinstance(value, str) or "T" not in value:
        rep.err(f"{where}: expected an ISO 8601 date-time")
        return
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        rep.err(f"{where}: expected an ISO 8601 date-time")
        return
    if parsed.tzinfo is None:
        rep.err(f"{where}: date-time must include a timezone")


def _listed_in_cluster(node_id: object, concepts: "set[str] | None") -> bool:
    """Membership in a cluster's concept set, for an id of any shape.

    The set keeps this check linear over the whole graph. An id that is not
    even hashable — hostile input reaches this far — is simply not listed,
    which is the answer a list comparison gave before.
    """
    if not concepts:
        return False
    try:
        return node_id in concepts
    except TypeError:
        return False


def _check_string_list(value: object, where: str, rep: Report, *, nonempty_items: bool = False) -> list:
    items = _as_list(value, where, rep)
    valid: list[str] = []
    for index, item in enumerate(items):
        _check_string(item, f"{where}[{index}]", rep, nonempty=nonempty_items)
        if isinstance(item, str) and (item or not nonempty_items):
            valid.append(item)
    return valid


def _check_url(value: object, where: str, rep: Report, *, identifier: bool = False) -> None:
    if value in (None, ""):
        return
    if not isinstance(value, str):
        rep.err(f"{where}: expected string URL/URI")
        return
    if value != value.strip() or any(ord(char) < 32 or ord(char) == 127 for char in value):
        rep.err(f"{where}: URI contains whitespace or control characters")
        return
    try:
        parsed = urlsplit(value)
    except ValueError:
        rep.err(f"{where}: malformed URL/URI")
        return
    allowed = SAFE_ID_SCHEMES if identifier else SAFE_LINK_SCHEMES
    scheme = parsed.scheme.lower()
    if not scheme or scheme not in allowed:
        rep.err(f"{where}: unsafe or unsupported URI scheme {parsed.scheme!r}; allowed: {sorted(allowed)}")
    if scheme in SAFE_LINK_SCHEMES and not parsed.hostname:
        rep.err(f"{where}: HTTP(S) URL requires a host")
    if parsed.username is not None or parsed.password is not None:
        rep.err(f"{where}: URL authority contains credentials; redact them before storing")
    for name, _ in parse_qsl(parsed.query, keep_blank_values=True):
        normalized_name = re.sub(r"[^a-z0-9]+", "", name.lower())
        if (
            normalized_name in CREDENTIAL_QUERY_NAMES
            or normalized_name.endswith(CREDENTIAL_QUERY_SUFFIXES)
        ):
            rep.err(f"{where}: URL query contains a credential; redact tokens before storing")


def _check_selector(selector: object, where: str, rep: Report) -> None:
    if not _require(selector, ["type"], where, rep):
        return
    _warn_unknown(selector, KNOWN_SELECTOR_KEYS, where, rep)
    stype = selector.get("type")
    needs = {
        "TextQuoteSelector": ("exact",),
        "TextPositionSelector": ("start", "end"),
        "PageSelector": ("page",),
        "FragmentSelector": ("fragment",),
        "CsvSelector": ("sheet", "cell_range"),
        "SvgSelector": ("xpath",),
        "JsonPointerSelector": ("json_pointer",),
        "CodeSelector": ("file", "symbol"),
    }
    if not isinstance(stype, str) or stype not in needs:
        rep.err(f"{where}.type: unsupported selector type {stype!r}")
        return
    _require(selector, list(needs[stype]), where, rep)
    if stype == "TextPositionSelector":
        start, end = selector.get("start"), selector.get("end")
        if (not isinstance(start, int) or isinstance(start, bool) or not isinstance(end, int)
                or isinstance(end, bool) or start < 0 or end < start):
            rep.err(f"{where}: TextPositionSelector requires 0 <= start <= end")
    elif stype == "PageSelector":
        page = selector.get("page")
        if not isinstance(page, int) or isinstance(page, bool) or page < 1:
            rep.err(f"{where}.page: expected integer >= 1")
    elif stype == "TextQuoteSelector":
        _check_string(selector.get("exact"), f"{where}.exact", rep)
        for field in ("prefix", "suffix"):
            if field in selector:
                _check_string(selector.get(field), f"{where}.{field}", rep)
    else:
        string_fields = {
            "FragmentSelector": ("fragment",),
            "CsvSelector": ("sheet", "cell_range"),
            "SvgSelector": ("xpath",),
            "JsonPointerSelector": ("json_pointer",),
            "CodeSelector": ("file", "symbol"),
        }
        for field in string_fields.get(stype, ()):
            _check_string(selector.get(field), f"{where}.{field}", rep)


def _check_evidence_refs(refs: object, evidence_ids: set[str], where: str, rep: Report) -> list:
    result = _check_string_list(refs, where, rep, nonempty_items=True)
    for ref in result:
        if ref not in evidence_ids:
            rep.err(f"{where}: evidence {ref!r} does not resolve to top-level evidence")
    return result


def _check_derivation(value: object, evidence_ids: set[str], where: str, rep: Report) -> None:
    if value is None:
        return
    if not _require(value, ["kind", "activity", "inputs", "summary", "review_status"], where, rep):
        return
    _warn_unknown(value, KNOWN_DERIVATION_KEYS, where, rep)
    _check_enum(
        value.get("kind"), {"rule_derived", "model_inferred", "synthesized", "human_added"},
        f"{where}.kind", rep,
    )
    _check_enum(value.get("review_status"), REVIEW_STATUSES, f"{where}.review_status", rep)
    _check_string(value.get("activity"), f"{where}.activity", rep, nonempty=True)
    _check_string(value.get("summary"), f"{where}.summary", rep, nonempty=True)
    if "rule" in value:
        _check_string(value.get("rule"), f"{where}.rule", rep, nullable=True)
    inputs = _check_string_list(value.get("inputs"), f"{where}.inputs", rep, nonempty_items=True)
    if not inputs:
        rep.err(f"{where}.inputs: derivations require at least one explicit input")
    _check_evidence_refs(value.get("evidence", []), evidence_ids, f"{where}.evidence", rep)


def _check_spatial_contexts(value: object, evidence_ids: set[str], where: str, rep: Report) -> None:
    contexts = _as_list(value, where, rep)
    for index, context in enumerate(contexts):
        cw = f"{where}[{index}]"
        if not _require(context, ["role", "place", "basis", "confidence", "evidence"], cw, rep):
            continue
        _warn_unknown(context, KNOWN_SPATIAL_KEYS, cw, rep)
        _check_enum(
            context.get("role"),
            {"jurisdiction", "market_scope", "event_location", "mentioned_location", "origin", "destination"},
            f"{cw}.role", rep,
        )
        _check_enum(
            context.get("basis"),
            {"source_explicit", "parser_derived", "model_inferred", "human_added"},
            f"{cw}.basis", rep,
        )
        _check_enum(context.get("confidence"), CONFIDENCE, f"{cw}.confidence", rep)
        refs = _check_evidence_refs(context.get("evidence"), evidence_ids, f"{cw}.evidence", rep)
        if not refs:
            rep.err(f"{cw}.evidence: spatial context requires explicit provenance; unknown context must remain absent")
        derivation = context.get("derivation")
        if isinstance(context.get("basis"), str) and context.get("basis") in {"parser_derived", "model_inferred"} and not derivation:
            rep.err(f"{cw}.derivation: {context.get('basis')} spatial context requires an explicit derivation")
        _check_derivation(derivation, evidence_ids, f"{cw}.derivation", rep)
        place = context.get("place")
        if not _require(place, ["id", "label", "kind"], f"{cw}.place", rep):
            continue
        _warn_unknown(place, KNOWN_PLACE_KEYS, f"{cw}.place", rep)
        _check_string(place.get("label"), f"{cw}.place.label", rep, nonempty=True)
        _check_string(place.get("id"), f"{cw}.place.id", rep, nonempty=True)
        _check_url(place.get("id"), f"{cw}.place.id", rep, identifier=True)
        _check_enum(
            place.get("kind"),
            {"country", "region", "city", "site", "address", "market", "organization_defined"},
            f"{cw}.place.kind", rep,
        )
        if "precision" in place:
            _check_enum(
                place.get("precision"),
                {"exact", "address", "site", "city", "region", "country", "coarse"},
                f"{cw}.place.precision", rep,
            )
        for j, ident in enumerate(_as_list(place.get("identifiers", []), f"{cw}.place.identifiers", rep)):
            _require(ident, ["scheme", "value"], f"{cw}.place.identifiers[{j}]", rep)
            _warn_unknown(ident, KNOWN_PLACE_ID_KEYS, f"{cw}.place.identifiers[{j}]", rep)
            if isinstance(ident, dict):
                _check_string(ident.get("scheme"), f"{cw}.place.identifiers[{j}].scheme", rep, nonempty=True)
                _check_string(ident.get("value"), f"{cw}.place.identifiers[{j}].value", rep, nonempty=True)
        if "geometry" in place and place.get("geometry") is not None and not isinstance(place.get("geometry"), dict):
            rep.err(f"{cw}.place.geometry: expected object or null")
        for field in ("sensitive", "redacted"):
            if field in place and not isinstance(place.get(field), bool):
                rep.err(f"{cw}.place.{field}: expected boolean")
        if place.get("sensitive") is True and place.get("redacted") is not True:
            rep.err(f"{cw}.place: sensitive precise location must be redacted")
        if place.get("redacted") is True:
            if isinstance(place.get("kind"), str) and place.get("kind") in {"address", "site"}:
                rep.err(f"{cw}.place.kind: a redacted place must use a coarser kind than address/site")
            precision = place.get("precision")
            if precision is None:
                rep.err(f"{cw}.place.precision: a redacted place must declare its coarse precision")
            elif isinstance(precision, str) and precision in {"exact", "address", "site"}:
                rep.err(f"{cw}.place.precision: a redacted place cannot retain exact/address/site precision")
            if "geometry" in place:
                rep.err(f"{cw}.place.geometry: a redacted place must omit geometry")
            if "identifiers" in place:
                rep.err(f"{cw}.place.identifiers: a redacted place must omit external identifiers")
        if place.get("kind") == "address" and place.get("redacted") is not True:
            rep.warn(f"{cw}.place: exact addresses may expose personal data; redact or reduce precision")


def _check_originated(obj: dict, evidence_ids: set[str], where: str, rep: Report) -> None:
    refs = _check_evidence_refs(obj.get("evidence", []), evidence_ids, f"{where}.evidence", rep)
    origin = obj.get("origin")
    derivation = obj.get("derivation")
    if origin is not None:
        _check_enum(origin, ORIGINS, f"{where}.origin", rep)
    if isinstance(origin, str) and origin in {"source_stated", "paraphrased"} and not refs:
        rep.err(f"{where}.evidence: {origin} content requires source evidence")
    if isinstance(origin, str) and origin in {"model_inferred", "rule_derived", "synthesized"} and not derivation:
        rep.err(f"{where}.derivation: {origin} content requires an explicit derivation")
    if obj.get("explanation") is not None and origin is None:
        rep.warn(f"{where}: explanation has no origin; distinguish source statement from inference")
    _check_derivation(derivation, evidence_ids, f"{where}.derivation", rep)


def schema_validate(doc: dict, rep: Report) -> bool:
    """Run the JSON Schema mirror when available. Core checks never depend on it."""
    try:
        import jsonschema  # type: ignore
    except Exception:
        return False
    try:
        schema = strict_json.load_path(SCHEMA_PATH)
        validator = jsonschema.Draft202012Validator(schema, format_checker=jsonschema.FormatChecker())
    except Exception as exc:  # pragma: no cover
        rep.warn(f"schema: could not load {SCHEMA_PATH.name} ({exc}); core validation still ran")
        return False
    try:
        errors = sorted(validator.iter_errors(doc), key=lambda item: list(item.path))
    except BaseException as exc:
        # Some jsonschema/referencing builds surface a deeply nested RecursionError through
        # pyo3_runtime.PanicException, which inherits BaseException rather than Exception.
        # Do not intercept process-control exceptions or unrelated validator failures.
        recursion_failure = isinstance(exc, RecursionError) or (
            exc.__class__.__name__ == "PanicException" and "RecursionError" in str(exc)
        )
        if not recursion_failure or isinstance(exc, (KeyboardInterrupt, SystemExit, GeneratorExit)):
            raise
        rep.warn("schema: validation recursion limit exceeded; dependency-free core validation still ran")
        return False
    for error in errors:
        path = "/".join(str(part) for part in error.path) or "<root>"
        rep.err(f"schema[{path}]: {error.message}")
    return True


def _check_stored_scores(meta: object, rep: Report) -> None:
    """Verify persisted scores against diagnostics without self-penalising the comparison."""
    if not isinstance(meta, dict):
        return
    expected = rep.conformance_score()
    quality = meta.get("quality_score")
    conformance = meta.get("conformance_score")
    quality_valid = isinstance(quality, int) and not isinstance(quality, bool)
    conformance_valid = isinstance(conformance, int) and not isinstance(conformance, bool)

    if quality_valid and conformance_valid and quality != conformance:
        rep.err(
            "metadata.quality_score and metadata.conformance_score must be equal "
            "compatibility aliases"
        )
    if quality_valid and quality != expected:
        rep.err(
            f"metadata.quality_score: stored {quality} but diagnostics require {expected}"
        )
    if conformance_valid and conformance != expected:
        rep.err(
            f"metadata.conformance_score: stored {conformance} but diagnostics require {expected}"
        )


def validate(
    doc: dict,
    prev: dict | None = None,
    ran_schema: bool = False,
    *,
    check_stored_scores: bool = True,
) -> Report:
    """Run the dependency-free producer contract and optional merge audit."""
    del ran_schema  # retained for API compatibility; core checks are always complete
    rep = Report()
    if not isinstance(doc, dict):
        rep.err("<root>: document must be a JSON object")
        return rep

    _check_private_reasoning_fields(doc, "", rep)

    _require(doc, ["metadata", "clusters", "nodes", "edges"], "<root>", rep)
    _warn_unknown(doc, KNOWN_TOP_KEYS, "<root>", rep)
    if "@context" in doc and not isinstance(doc.get("@context"), dict):
        rep.err("@context: expected object")
    meta = doc.get("metadata", {})
    if not _require(
        meta,
        ["title", "distiller_version", "distiller_spec_version", "sources", "distillation_date",
         "domain", "language", "depth", "mode", "quality_score", "concept_count",
         "relationship_count", "cluster_count"],
        "metadata", rep,
    ):
        meta = {}
    _warn_unknown(meta, KNOWN_METADATA_KEYS, "metadata", rep)
    _check_string(meta.get("title"), "metadata.title", rep, nonempty=True)
    if meta.get("distiller_version") not in (None, "4.0"):
        rep.err(f"metadata.distiller_version: expected '4.0', got {meta.get('distiller_version')!r}")
    spec_version = meta.get("distiller_spec_version")
    _check_string(spec_version, "metadata.distiller_spec_version", rep, nonempty=True)
    if isinstance(spec_version, str) and not SPEC_VERSION.fullmatch(spec_version):
        rep.err("metadata.distiller_spec_version: expected '<major>.<minor>' using decimal integers")
    elif isinstance(spec_version, str) and spec_version not in KNOWN_SPEC_VERSIONS:
        rep.warn(f"metadata.distiller_spec_version: unknown version {spec_version!r} (best-effort consumption)")
    if spec_version == "1.1" and "conformance_score" not in meta:
        rep.warn("metadata.conformance_score: Spec 1.1 SHOULD expose the non-semantic score name")
    _check_enum(meta.get("depth"), {"quick", "standard", "deep"}, "metadata.depth", rep)
    _check_enum(meta.get("mode"), {"fresh", "merge", "batch"}, "metadata.mode", rep)
    _check_iso_date(meta.get("distillation_date"), "metadata.distillation_date", rep, nullable=False)
    _check_string(meta.get("domain"), "metadata.domain", rep)
    _check_string(meta.get("language"), "metadata.language", rep, nonempty=True)
    if isinstance(meta.get("language"), str) and len(meta["language"]) < 2:
        rep.err("metadata.language: expected at least two characters")
    if "temporal_confidence" in meta:
        _check_enum(meta.get("temporal_confidence"), TEMPORAL_CONFIDENCE, "metadata.temporal_confidence", rep)
    for field in ("quality_score", "conformance_score"):
        if field in meta:
            _check_integer(meta.get(field), f"metadata.{field}", rep, maximum=100)
    for field in ("concept_count", "relationship_count", "cluster_count", "fact_count"):
        if field in meta:
            _check_integer(meta.get(field), f"metadata.{field}", rep)

    source_ids: set[str] = set()
    ordered_sources: list[str] = []
    sources = _as_list(meta.get("sources"), "metadata.sources", rep)
    if not sources:
        rep.err("metadata.sources: expected at least one source")
    for index, source in enumerate(sources):
        where = f"metadata.sources[{index}]"
        if not _require(source, ["id", "file", "type"], where, rep):
            continue
        _warn_unknown(source, KNOWN_SOURCE_KEYS, where, rep)
        sid = _check_unique_id(source, source_ids, where, rep)
        if sid:
            ordered_sources.append(sid)
        file_value = source.get("file")
        _check_string(file_value, f"{where}.file", rep, nonempty=True)
        _check_string(source.get("type"), f"{where}.type", rep)
        if isinstance(file_value, str) and Path(file_value).is_absolute():
            rep.warn(f"{where}.file: absolute local path may leak host information; store a basename or redacted logical path")
        if "date" in source:
            _check_iso_date(source.get("date"), f"{where}.date", rep)
        _check_url(source.get("url"), f"{where}.url", rep)
        _check_hash(source.get("content_sha256"), f"{where}.content_sha256", rep)
        for field in ("title", "publisher", "version", "license"):
            if field in source:
                _check_string(source.get(field), f"{where}.{field}", rep)
        if "authors" in source:
            _check_string_list(source.get("authors"), f"{where}.authors", rep, nonempty_items=True)
        if "retrieved_at" in source:
            if source.get("retrieved_at") is None:
                rep.err(f"{where}.retrieved_at: expected an ISO 8601 date-time")
            else:
                _check_datetime(source.get("retrieved_at"), f"{where}.retrieved_at", rep)
        for j, agent in enumerate(_as_list(source.get("agents", []), f"{where}.agents", rep)):
            aw = f"{where}.agents[{j}]"
            _require(agent, ["id", "label", "type", "role"], aw, rep)
            _warn_unknown(agent, KNOWN_AGENT_KEYS, aw, rep)
            if isinstance(agent, dict):
                _check_string(agent.get("id"), f"{aw}.id", rep, nonempty=True)
                _check_string(agent.get("label"), f"{aw}.label", rep, nonempty=True)
                _check_enum(agent.get("type"), {"person", "organization", "software"}, f"{aw}.type", rep)
                _check_enum(
                    agent.get("role"),
                    {"author", "publisher", "creator", "editor", "data_producer", "host", "extractor"},
                    f"{aw}.role", rep,
                )

    evidence_records = _as_list(doc.get("evidence", []), "evidence", rep)
    evidence_ids: set[str] = set()
    for index, evidence in enumerate(evidence_records):
        evidence_id = _check_unique_id(evidence, evidence_ids, f"evidence[{index}]", rep)
        if evidence_id is not None and not EVIDENCE_ID.fullmatch(evidence_id):
            rep.err(f"evidence[{index}].id: invalid evidence id")
    for index, evidence in enumerate(evidence_records):
        where = f"evidence[{index}]"
        if not _require(evidence, ["id", "source", "selector", "support", "attribution_basis"], where, rep):
            continue
        _warn_unknown(evidence, KNOWN_EVIDENCE_KEYS, where, rep)
        evidence_source = evidence.get("source")
        _check_string(evidence_source, f"{where}.source", rep, nonempty=True)
        if isinstance(evidence_source, str) and evidence_source not in source_ids:
            rep.err(f"{where}.source: {evidence.get('source')!r} does not resolve to metadata.sources")
        _check_selector(evidence.get("selector"), f"{where}.selector", rep)
        _check_enum(evidence.get("support"), {"supports", "contradicts", "contextualizes", "mentions"}, f"{where}.support", rep)
        _check_enum(
            evidence.get("attribution_basis"),
            {"source_explicit", "parser_derived", "model_inferred", "human_added"},
            f"{where}.attribution_basis", rep,
        )
        if "excerpt" in evidence:
            _check_string(evidence.get("excerpt"), f"{where}.excerpt", rep)
        _check_hash(evidence.get("excerpt_sha256"), f"{where}.excerpt_sha256", rep)
        excerpt, digest = evidence.get("excerpt"), evidence.get("excerpt_sha256")
        if isinstance(excerpt, str) and isinstance(digest, str):
            actual = hashlib.sha256(excerpt.encode("utf-8")).hexdigest()
            if digest.removeprefix("sha256:").lower() != actual:
                rep.err(f"{where}.excerpt_sha256: does not match excerpt")
        if "review_status" in evidence:
            _check_enum(evidence.get("review_status"), REVIEW_STATUSES, f"{where}.review_status", rep)
        derivation = evidence.get("derivation")
        if (
            isinstance(evidence.get("attribution_basis"), str)
            and evidence.get("attribution_basis") in {"parser_derived", "model_inferred"}
            and not derivation
        ):
            rep.err(
                f"{where}.derivation: {evidence.get('attribution_basis')} attribution requires an explicit derivation"
            )
        _check_derivation(derivation, evidence_ids, f"{where}.derivation", rep)

    clusters = _as_list(doc.get("clusters"), "clusters", rep)
    cluster_ids: set[str] = set()
    # Membership only, once per node: a set keeps that linear in the node count.
    cluster_concepts: dict[str, set[str]] = {}
    for index, cluster in enumerate(clusters):
        where = f"clusters[{index}]"
        if not _require(cluster, ["id", "label", "concepts"], where, rep):
            continue
        _warn_unknown(cluster, KNOWN_CLUSTER_KEYS, where, rep)
        cid = _check_unique_id(cluster, cluster_ids, where, rep)
        _check_string(cluster.get("label"), f"{where}.label", rep, nonempty=True)
        if "description" in cluster:
            _check_string(cluster.get("description"), f"{where}.description", rep)
        concepts = _check_string_list(cluster.get("concepts"), f"{where}.concepts", rep)
        if cid:
            cluster_concepts[cid] = set(concepts)
        if not concepts:
            rep.warn(f"clusters[{cid or index}]: has no concepts")

    nodes = _as_list(doc.get("nodes"), "nodes", rep)
    node_ids: set[str] = set()
    resource_owner: dict[str, str] = {}
    for index, node in enumerate(nodes):
        if isinstance(node, dict) and isinstance(node.get("id"), str):
            if node["id"] in node_ids:
                rep.err(f"nodes[{index}]: duplicate node id {node['id']!r}")
            node_ids.add(node["id"])

    claims = _as_list(doc.get("claims", []), "claims", rep)
    claim_ids: set[str] = set()
    for index, claim in enumerate(claims):
        claim_id = _check_unique_id(claim, claim_ids, f"claims[{index}]", rep)
        if claim_id is not None and not EVIDENCE_ID.fullmatch(claim_id):
            rep.err(f"claims[{index}].id: invalid claim id")

    for index, node in enumerate(nodes):
        nid = node.get("id") if isinstance(node, dict) else None
        where = f"nodes[{nid or index}]"
        if not _require(node, ["id", "label", "cluster", "confidence", "definition", "relevance", "statements", "temporal", "sources"], where, rep):
            continue
        _warn_unknown(node, KNOWN_NODE_KEYS, where, rep)
        if not NODE_ID.fullmatch(str(nid)):
            rep.err(f"{where}.id: expected kebab-case stable id")
        _check_string(node.get("label"), f"{where}.label", rep, nonempty=True)
        _check_string(node.get("cluster"), f"{where}.cluster", rep, nonempty=True)
        _check_string(node.get("definition"), f"{where}.definition", rep, nonempty=True)
        _check_string(node.get("relevance"), f"{where}.relevance", rep)
        if "note" in node:
            _check_string(node.get("note"), f"{where}.note", rep)
        _check_enum(node.get("confidence"), CONFIDENCE, f"{where}.confidence", rep)
        cluster = node.get("cluster")
        if isinstance(cluster, str) and cluster not in cluster_ids:
            rep.err(f"{where}.cluster: {cluster!r} does not resolve to a declared cluster")
        elif isinstance(cluster, str) and not _listed_in_cluster(nid, cluster_concepts.get(cluster)):
            rep.err(f"{where}: not listed in cluster {cluster!r}.concepts (run build_graph to fix)")
        statements = _check_string_list(node.get("statements"), f"{where}.statements", rep)
        if not statements:
            rep.err(f"{where}.statements: expected at least one statement")
        for sid in _check_string_list(node.get("sources"), f"{where}.sources", rep):
            if sid not in source_ids:
                rep.err(f"{where}.sources: {sid!r} does not resolve to metadata.sources")
        temporal = node.get("temporal")
        if _require(temporal, ["source_date", "temporal_confidence"], f"{where}.temporal", rep):
            _warn_unknown(temporal, KNOWN_TEMPORAL_KEYS, f"{where}.temporal", rep)
            _check_enum(
                temporal.get("temporal_confidence"), TEMPORAL_CONFIDENCE,
                f"{where}.temporal.temporal_confidence", rep,
            )
            for field in ("source_date", "source_period", "valid_from", "valid_until"):
                if field in temporal:
                    _check_iso_date(temporal.get(field), f"{where}.temporal.{field}", rep)
        cited_numbers: set[int] = set()
        for j, citation in enumerate(_as_list(node.get("citations", []), f"{where}.citations", rep)):
            cw = f"{where}.citations[{j}]"
            if not _require(citation, ["n", "source"], cw, rep):
                continue
            _warn_unknown(citation, KNOWN_CITATION_KEYS, cw, rep)
            number = citation.get("n")
            source = citation.get("source")
            _check_string(source, f"{cw}.source", rep, nonempty=True)
            if not isinstance(number, int) or isinstance(number, bool) or number < 1 or number > len(ordered_sources):
                rep.err(f"{cw}.n: out of range (1..{len(ordered_sources)} sources)")
            else:
                cited_numbers.add(number)
                if source != ordered_sources[number - 1]:
                    rep.err(f"{cw}: citation number [{number}] resolves to {ordered_sources[number - 1]!r}, not {source!r}")
            if isinstance(source, str) and source not in source_ids:
                rep.err(f"{cw}.source: {source!r} does not resolve to metadata.sources")
            for field in ("locator", "label"):
                if field in citation:
                    _check_string(citation.get(field), f"{cw}.{field}", rep, nullable=True)
            _check_url(citation.get("url"), f"{cw}.url", rep)
            if citation.get("selector") is not None:
                _check_selector(citation.get("selector"), f"{cw}.selector", rep)
        marker_numbers: set[int] = set()
        for statement in statements:
            if not isinstance(statement, str):
                continue
            for match in CITATION_MARKER.findall(statement):
                if len(match) > MAX_CITATION_DIGITS:
                    rep.err(f"{where}: citation marker has more than {MAX_CITATION_DIGITS} digits")
                    continue
                marker_numbers.add(int(match))
        for number in marker_numbers:
            if number < 1 or number > len(ordered_sources):
                rep.err(f"{where}: citation marker [{number}] out of range (1..{len(ordered_sources)} sources)")
        if not cited_numbers and not marker_numbers:
            rep.warn(f"{where}: has no citations")
        resource = node.get("resource")
        _check_url(resource, f"{where}.resource", rep, identifier=True)
        if isinstance(resource, str) and resource:
            if resource in resource_owner and resource_owner[resource] != nid:
                rep.err(f"{where}.resource: duplicate resource identity also used by node {resource_owner[resource]!r}")
            else:
                resource_owner[resource] = str(nid)
        _check_evidence_refs(node.get("evidence", []), evidence_ids, f"{where}.evidence", rep)
        for claim_id in _check_string_list(node.get("claim_ids", []), f"{where}.claim_ids", rep):
            if claim_id not in claim_ids:
                rep.err(f"{where}.claim_ids: {claim_id!r} does not resolve to claims")
        _check_spatial_contexts(node.get("spatial_contexts", []), evidence_ids, f"{where}.spatial_contexts", rep)

    node_by_id = {
        node["id"]: node
        for node in nodes
        if isinstance(node, dict) and isinstance(node.get("id"), str)
    }
    for index, claim in enumerate(claims):
        where = f"claims[{claim.get('id', index) if isinstance(claim, dict) else index}]"
        if not _require(claim, ["id", "node", "statement", "confidence", "origin", "evidence"], where, rep):
            continue
        _warn_unknown(claim, KNOWN_CLAIM_KEYS, where, rep)
        claim_node = claim.get("node")
        _check_string(claim_node, f"{where}.node", rep, nonempty=True)
        _check_string(claim.get("statement"), f"{where}.statement", rep, nonempty=True)
        node = node_by_id.get(claim_node) if isinstance(claim_node, str) else None
        if not isinstance(claim_node, str):
            pass
        elif node is None:
            rep.err(f"{where}.node: {claim.get('node')!r} does not resolve to a node")
        elif (
            isinstance(claim.get("statement"), str)
            and (
                not isinstance(node.get("statements"), list)
                or claim.get("statement") not in node.get("statements")
            )
        ):
            rep.err(f"{where}.statement: missing from nodes[{claim.get('node')}].statements compatibility projection")
        _check_enum(claim.get("confidence"), CONFIDENCE, f"{where}.confidence", rep)
        _check_originated(claim, evidence_ids, where, rep)
        if claim.get("temporal") is not None:
            temporal = claim.get("temporal")
            if _require(temporal, ["source_date", "temporal_confidence"], f"{where}.temporal", rep):
                _warn_unknown(temporal, KNOWN_TEMPORAL_KEYS, f"{where}.temporal", rep)
                _check_enum(temporal.get("temporal_confidence"), TEMPORAL_CONFIDENCE, f"{where}.temporal.temporal_confidence", rep)
                for field in ("source_date", "source_period", "valid_from", "valid_until"):
                    if field in temporal:
                        _check_iso_date(temporal.get(field), f"{where}.temporal.{field}", rep)
        _check_spatial_contexts(claim.get("spatial_contexts", []), evidence_ids, f"{where}.spatial_contexts", rep)
        if "review_status" in claim:
            _check_enum(claim.get("review_status"), REVIEW_STATUSES, f"{where}.review_status", rep)

    edges = _as_list(doc.get("edges"), "edges", rep)
    edge_ids: set[str] = set()
    for index, edge in enumerate(edges):
        where = f"edges[{index}]"
        if not _require(edge, ["source", "target", "type", "weight", "confidence"], where, rep):
            continue
        _warn_unknown(edge, KNOWN_EDGE_KEYS, where, rep)
        if "id" in edge:
            _check_string(edge.get("id"), f"{where}.id", rep)
            edge_id = edge.get("id")
            if isinstance(edge_id, str):
                if edge_id in edge_ids:
                    rep.err(f"{where}.id: duplicate edge id {edge_id!r}")
                edge_ids.add(edge_id)
                source, target, edge_type = edge.get("source"), edge.get("target"), edge.get("type")
                if all(isinstance(item, str) for item in (source, target, edge_type)):
                    if edge_type == "tension":
                        source, target = sorted((source, target))
                    canonical = f"{source}__{edge_type}__{target}"
                    if edge_id != canonical:
                        rep.err(f"{where}.id: non-canonical edge id; run build_graph.py")
        edge_source = edge.get("source")
        edge_target = edge.get("target")
        _check_string(edge_source, f"{where}.source", rep, nonempty=True)
        _check_string(edge_target, f"{where}.target", rep, nonempty=True)
        if "label" in edge:
            _check_string(edge.get("label"), f"{where}.label", rep)
        edge_type = edge.get("type")
        if not isinstance(edge_type, str) or edge_type not in EDGE_TYPES:
            rep.err(f"{where}.type: {edge_type!r} not in the 8-type vocabulary")
        for end, endpoint in (("source", edge_source), ("target", edge_target)):
            if isinstance(endpoint, str) and endpoint not in node_ids:
                rep.err(f"{where}.{end}: {endpoint!r} does not resolve to a node")
        weight = edge.get("weight")
        if not isinstance(weight, (int, float)) or isinstance(weight, bool) or not 0 <= weight <= 1:
            rep.err(f"{where}.weight: {weight!r} outside [0,1]")
        _check_enum(edge.get("confidence"), CONFIDENCE, f"{where}.confidence", rep)
        if "temporal" in edge and not isinstance(edge.get("temporal"), dict):
            rep.err(f"{where}.temporal: expected object")
        elif isinstance(edge.get("temporal"), dict):
            _warn_unknown(edge["temporal"], {"valid_from", "valid_until"}, f"{where}.temporal", rep)
            for field in ("valid_from", "valid_until"):
                if field in edge["temporal"]:
                    _check_iso_date(edge["temporal"].get(field), f"{where}.temporal.{field}", rep)
        _check_originated(edge, evidence_ids, where, rep)
        _check_spatial_contexts(edge.get("spatial_contexts", []), evidence_ids, f"{where}.spatial_contexts", rep)

    facts = _as_list(doc.get("facts", []), "facts", rep)
    fact_ids: set[str] = set()
    for index, fact in enumerate(facts):
        where = f"facts[{fact.get('id', index) if isinstance(fact, dict) else index}]"
        if not _require(fact, ["id", "statement", "value", "confidence", "source"], where, rep):
            continue
        _warn_unknown(fact, KNOWN_FACT_KEYS, where, rep)
        _check_unique_id(fact, fact_ids, where, rep)
        _check_string(fact.get("statement"), f"{where}.statement", rep, nonempty=True)
        _check_string(fact.get("value"), f"{where}.value", rep)
        for field in ("context", "concept", "metric", "explanation"):
            if field in fact:
                _check_string(fact.get(field), f"{where}.{field}", rep)
        fact_source = fact.get("source")
        _check_string(fact_source, f"{where}.source", rep, nonempty=True)
        if isinstance(fact_source, str) and fact_source not in source_ids:
            rep.err(f"{where}.source: {fact.get('source')!r} does not resolve to metadata.sources")
        fact_concept = fact.get("concept")
        if isinstance(fact_concept, str) and fact_concept not in node_ids:
            rep.err(f"{where}.concept: {fact.get('concept')!r} does not resolve to a node")
        _check_enum(fact.get("confidence"), CONFIDENCE, f"{where}.confidence", rep)
        if "temporal" in fact and not isinstance(fact.get("temporal"), dict):
            rep.err(f"{where}.temporal: expected object")
        elif isinstance(fact.get("temporal"), dict):
            _warn_unknown(fact["temporal"], KNOWN_TEMPORAL_KEYS, f"{where}.temporal", rep)
            for field in ("source_date", "source_period", "valid_from", "valid_until"):
                if field in fact["temporal"]:
                    _check_iso_date(fact["temporal"].get(field), f"{where}.temporal.{field}", rep)
            if "temporal_confidence" in fact["temporal"]:
                _check_enum(
                    fact["temporal"].get("temporal_confidence"), TEMPORAL_CONFIDENCE,
                    f"{where}.temporal.temporal_confidence", rep,
                )
        _check_originated(fact, evidence_ids, where, rep)
        _check_spatial_contexts(fact.get("spatial_contexts", []), evidence_ids, f"{where}.spatial_contexts", rep)

    chunks = _as_list(doc.get("chunks", []), "chunks", rep)
    chunk_ids: set[str] = set()
    for index, chunk in enumerate(chunks):
        where = f"chunks[{chunk.get('id', index) if isinstance(chunk, dict) else index}]"
        if not _require(chunk, ["id", "text", "concepts"], where, rep):
            continue
        _warn_unknown(chunk, KNOWN_CHUNK_KEYS, where, rep)
        _check_string(chunk.get("id"), f"{where}.id", rep, nonempty=True)
        if isinstance(chunk.get("id"), str):
            if chunk["id"] in chunk_ids:
                rep.err(f"{where}.id: duplicate chunk id {chunk['id']!r}")
            chunk_ids.add(chunk["id"])
        text = chunk.get("text", "")
        if not isinstance(text, str) or not text:
            rep.err(f"{where}.text: expected non-empty string")
            text = ""
        for label, pattern in CHUNK_FORBIDDEN.items():
            if pattern.search(text):
                rep.err(f"{where}: embedding text contains a {label} artifact")
        if EMOJI.search(text):
            rep.err(f"{where}: embedding text contains an emoji")
        for concept in _check_string_list(chunk.get("concepts"), f"{where}.concepts", rep):
            if concept not in node_ids:
                rep.warn(f"{where}.concepts: {concept!r} does not resolve to a node")
        if "temporal_scope" in chunk:
            _check_string(chunk.get("temporal_scope"), f"{where}.temporal_scope", rep, nullable=True)
        if "token_estimate" in chunk:
            _check_integer(chunk.get("token_estimate"), f"{where}.token_estimate", rep)
        if "kind" in chunk:
            _check_enum(chunk.get("kind"), {"source_claims", "inference", "summary"}, f"{where}.kind", rep)
        if "include_in_default_retrieval" in chunk and not isinstance(chunk.get("include_in_default_retrieval"), bool):
            rep.err(f"{where}.include_in_default_retrieval: expected boolean")
        _check_originated(chunk, evidence_ids, where, rep)
        _check_spatial_contexts(chunk.get("spatial_contexts", []), evidence_ids, f"{where}.spatial_contexts", rep)
        if chunk.get("kind") == "inference" and chunk.get("include_in_default_retrieval") is not False:
            rep.err(f"{where}: inference chunks must set include_in_default_retrieval=false")

    _check_string_list(doc.get("open_questions", []), "open_questions", rep)

    assessment_ids: set[str] = set()
    for index, assessment in enumerate(_as_list(doc.get("assessments", []), "assessments", rep)):
        where = f"assessments[{index}]"
        if not _require(assessment, ["id", "dimension", "scope", "value", "assessor", "method", "assessed_at", "evidence"], where, rep):
            continue
        _warn_unknown(assessment, KNOWN_ASSESSMENT_KEYS, where, rep)
        _check_unique_id(assessment, assessment_ids, where, rep)
        for field in ("dimension", "scope", "value", "assessor", "method"):
            _check_string(assessment.get(field), f"{where}.{field}", rep, nonempty=True)
        _check_iso_date(assessment.get("assessed_at"), f"{where}.assessed_at", rep, nullable=False)
        assessment_evidence = _check_evidence_refs(assessment.get("evidence"), evidence_ids, f"{where}.evidence", rep)
        if not assessment_evidence:
            rep.err(f"{where}.evidence: provenance-backed assessments require at least one evidence record")

    conflict_ids: set[str] = set()
    for index, conflict in enumerate(_as_list(doc.get("fact_conflicts", []), "fact_conflicts", rep)):
        where = f"fact_conflicts[{index}]"
        if not _require(conflict, ["id", "facts", "relation", "reason"], where, rep):
            continue
        _warn_unknown(conflict, KNOWN_CONFLICT_KEYS, where, rep)
        _check_unique_id(conflict, conflict_ids, where, rep)
        _check_enum(conflict.get("relation"), {"tension", "supersedes"}, f"{where}.relation", rep)
        _check_string(conflict.get("reason"), f"{where}.reason", rep, nonempty=True)
        refs = _check_string_list(conflict.get("facts"), f"{where}.facts", rep, nonempty_items=True)
        if len(refs) != 2 or refs[0] == refs[1]:
            rep.err(f"{where}.facts: expected two distinct fact ids")
        for ref in refs:
            if ref not in fact_ids:
                rep.err(f"{where}.facts: {ref!r} does not resolve to facts")
        _check_evidence_refs(conflict.get("evidence", []), evidence_ids, f"{where}.evidence", rep)

    _check_count(meta, "concept_count", len(nodes), rep)
    _check_count(meta, "relationship_count", len(edges), rep)
    _check_count(meta, "cluster_count", len(clusters), rep)
    if "fact_count" in meta:
        _check_count(meta, "fact_count", len(facts), rep)

    if prev is not None:
        _check_monotonic(prev, doc, rep)
    if check_stored_scores:
        _check_stored_scores(meta, rep)
    return rep


def _check_count(meta: dict, field: str, actual: int, rep: Report) -> None:
    declared = meta.get(field)
    if declared is not None and declared != actual:
        rep.err(f"metadata.{field}: declared {declared} but graph has {actual}")


def _canon(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _list_preserved(old: object, new: object) -> bool:
    old_items = old if isinstance(old, list) else []
    new_items = new if isinstance(new, list) else []
    new_canon = {_canon(item) for item in new_items}
    return all(_canon(item) in new_canon for item in old_items)


def _node_identity(node: dict) -> tuple[str, str | None]:
    return ("resource", node.get("resource")) if node.get("resource") else ("id", node.get("id"))


def _edge_identity(edge: dict, resources: dict[str, str] | None = None) -> tuple:
    resources = resources or {}
    source_id, target_id, edge_type = edge.get("source"), edge.get("target"), edge.get("type")
    source = ("resource", resources[source_id]) if source_id in resources else ("id", source_id)
    target = ("resource", resources[target_id]) if target_id in resources else ("id", target_id)
    if edge_type == "tension":
        source, target = sorted((source, target))
    return source, target, edge_type


def _preserve_fields(old: dict, new: dict, fields: tuple[str, ...], where: str, rep: Report) -> None:
    for field in fields:
        if field not in old:
            continue
        before, after = old.get(field), new.get(field)
        if isinstance(before, list):
            if not _list_preserved(before, after):
                rep.err(f"merge: {where}.{field} dropped or changed prior item(s)")
        elif isinstance(before, dict):
            for key, value in before.items():
                if key not in (after or {}) or _canon((after or {}).get(key)) != _canon(value):
                    rep.err(f"merge: {where}.{field}.{key} did not preserve the prior value")
        elif before not in (None, "") and before != after:
            rep.err(f"merge: {where}.{field} changed immutable prior value {before!r}")


def _check_monotonic(prev: dict, new: dict, rep: Report) -> None:
    """Identity- and payload-aware monotonicity audit, not a count-only approximation."""
    for collection in ("nodes", "edges", "facts", "evidence", "claims", "assessments", "fact_conflicts"):
        before = len(prev.get(collection, []) or [])
        after = len(new.get(collection, []) or [])
        if after < before:
            rep.err(f"merge: {collection} shrank from {before} to {after} (violates monotonicity contract §6)")

    new_sources = {item.get("id"): item for item in (new.get("metadata", {}).get("sources", []) or [])}
    for old in prev.get("metadata", {}).get("sources", []) or []:
        current = new_sources.get(old.get("id"))
        if current is None:
            rep.err(f"merge: dropped source {old.get('id')!r}")
        else:
            _preserve_fields(old, current, tuple(old.keys()), f"source[{old.get('id')}]", rep)

    new_nodes = {_node_identity(item): item for item in (new.get("nodes", []) or [])}
    for old in prev.get("nodes", []) or []:
        identity = _node_identity(old)
        current = new_nodes.get(identity)
        if current is None:
            rep.err(f"merge: dropped node identity {identity!r} present in the prior graph (§6)")
            continue
        _preserve_fields(old, current, ("statements", "sources", "citations", "evidence", "claim_ids", "spatial_contexts"), f"node[{old.get('id')}]", rep)

    old_resources = {
        item.get("id"): item.get("resource") for item in (prev.get("nodes", []) or []) if item.get("resource")
    }
    new_resources = {
        item.get("id"): item.get("resource") for item in (new.get("nodes", []) or []) if item.get("resource")
    }
    new_edges = {_edge_identity(item, new_resources): item for item in (new.get("edges", []) or [])}
    for old in prev.get("edges", []) or []:
        old_identity = _edge_identity(old, old_resources)
        current = new_edges.get(old_identity)
        if current is None:
            rep.err(f"merge: dropped edge {old_identity!r}")
            continue
        _preserve_fields(old, current, ("evidence", "spatial_contexts", "explanation", "derivation", "temporal"), f"edge[{old.get('id', old_identity)}]", rep)

    for collection, fields in (
        ("facts", ("statement", "value", "source", "context", "metric", "temporal", "evidence", "spatial_contexts", "explanation", "derivation")),
        ("evidence", ("source", "selector", "support", "attribution_basis", "excerpt", "excerpt_sha256")),
        ("claims", ("statement", "evidence", "temporal", "spatial_contexts", "derivation")),
        ("assessments", ("dimension", "scope", "value", "assessor", "method", "assessed_at", "evidence")),
        ("fact_conflicts", ("facts", "relation", "reason", "evidence")),
    ):
        new_by_id = {item.get("id"): item for item in (new.get(collection, []) or [])}
        for old in prev.get(collection, []) or []:
            current = new_by_id.get(old.get("id"))
            if current is None:
                rep.err(f"merge: dropped {collection[:-1]} {old.get('id')!r}")
            else:
                _preserve_fields(old, current, fields, f"{collection}[{old.get('id')}]", rep)
                reference_field = "concept" if collection == "facts" else "node" if collection == "claims" else None
                if reference_field and old.get(reference_field) is not None:
                    old_ref = old.get(reference_field)
                    new_ref = current.get(reference_field)
                    old_identity = ("resource", old_resources[old_ref]) if old_ref in old_resources else ("id", old_ref)
                    new_identity = ("resource", new_resources[new_ref]) if new_ref in new_resources else ("id", new_ref)
                    if old_identity != new_identity:
                        rep.err(
                            f"merge: {collection}[{old.get('id')}].{reference_field} changed node identity "
                            f"from {old_identity!r} to {new_identity!r}"
                        )


def _print_human(path: str, rep: Report, score: int) -> None:
    for warning in rep.warnings:
        print(f"  ⚠ WARNING  {warning}")
    for error in rep.errors:
        print(f"  ✗ ERROR    {error}")
    status = "PASS" if rep.ok else "FAIL"
    print(f"\n{path}: {status} — {len(rep.errors)} error(s), {len(rep.warnings)} warning(s) — conformance_score = {score}/100")
    print("  semantic_accuracy_evaluated = false")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate a Knowledge Distiller .knowledge.json")
    parser.add_argument("file", help="path to the .knowledge.json to validate")
    parser.add_argument("--prev", help="prior graph for the payload-aware monotonicity audit")
    parser.add_argument("--md", help="optional sibling Markdown to check wikilinks")
    parser.add_argument("--json", action="store_true", dest="as_json", help="emit a machine-readable report")
    parser.add_argument("--quiet", action="store_true", help="only print the summary line")
    args = parser.parse_args(argv)

    try:
        doc = strict_json.load_path(args.file)
    except strict_json.StrictJsonError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    previous = None
    if args.prev:
        try:
            previous = strict_json.load_path(args.prev)
        except strict_json.StrictJsonError as exc:
            print(str(exc), file=sys.stderr)
            return 1

    report = Report()
    schema_checked = schema_validate(doc, report)
    core = validate(doc, prev=previous, ran_schema=schema_checked)
    report.errors.extend(core.errors)
    report.warnings.extend(core.warnings)

    if args.md:
        try:
            markdown = Path(args.md).read_text(encoding="utf-8")
            known = {node.get("label") for node in (doc.get("nodes", []) or [])} | {node.get("id") for node in (doc.get("nodes", []) or [])}
            for target in set(WIKILINK.findall(markdown)):
                if target not in known:
                    report.warn(f"{args.md}: wikilink [[{target}]] has no matching node label/id")
        except Exception as exc:
            report.warn(f"{args.md}: could not read for wikilink check ({exc})")

    score = report.conformance_score()
    payload = {
        "file": args.file,
        "ok": report.ok,
        "errors": report.errors,
        "warnings": report.warnings,
        "conformance_score": score,
        "quality_score": score,
        "semantic_accuracy_evaluated": False,
        "schema_checked": schema_checked,
        "core_checks": "complete",
    }
    if args.as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    elif args.quiet:
        status = "PASS" if report.ok else "FAIL"
        print(f"{args.file}: {status} ({len(report.errors)} errors, {len(report.warnings)} warnings, conformance {score}/100; semantic accuracy not evaluated)")
    else:
        _print_human(args.file, report, score)
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
