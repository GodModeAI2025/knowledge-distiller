#!/usr/bin/env python3
"""Validate versioned Knowledge Distiller compiler/extraction profiles.

The manual validator is the authoritative, complete implementation and uses only
the Python standard library. When ``jsonschema`` is installed, the local profile
schema is applied as an additional mirror check; it is never fetched remotely.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

import strict_json


SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
SCHEMA_PATH = REPO_ROOT / "schema" / "profile.schema.json"
DEFAULT_PROFILE_PATH = REPO_ROOT / "profiles" / "default.json"
MAX_PROFILE_BYTES = 1024 * 1024
SUPPORTED_PROFILE_VERSIONS = {"1.0"}

PROFILE_ID = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
PROFILE_VERSION = re.compile(r"^[0-9]+\.[0-9]+$")
URI_PREFIX = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
SENSITIVE_KEYS = {
    "prompt",
    "prompts",
    "system_prompt",
    "prompt_template",
    "secret",
    "secrets",
    "password",
    "passwd",
    "token",
    "tokens",
    "api_key",
    "apikey",
    "credential",
    "credentials",
    "authorization",
    "private_key",
    "reasoning_trace",
    "chain_of_thought",
    "chainofthought",
    "cot",
    "scratchpad",
    "hidden_reasoning",
    "private_reasoning",
}

EDGE_TYPES = {
    "uses", "enables", "based-on", "part-of", "tension", "replaces",
    "extends", "example-of",
}
SOURCE_TYPES = {"txt", "md", "html", "csv", "tsv", "json", "docx"}
EVIDENCE_TARGETS = {
    "node_statements", "facts", "edges", "spatial_contexts", "derivations",
}
ATTRIBUTION_BASES = {
    "source_explicit", "parser_derived", "model_inferred", "human_added",
}

ROOT_KEYS = {
    "$schema", "profile_version", "profile_id", "description", "compiler",
    "extraction", "retrieval", "limits",
}
COMPILER_KEYS = {"evidence_policy", "inference_policy", "spatial_policy", "graph_policy"}
EVIDENCE_KEYS = {
    "mode", "required_for", "minimum_items_per_claim", "require_selector",
    "allowed_attribution_basis", "unresolved_claim_policy", "conflict_policy",
}
INFERENCE_KEYS = {
    "enabled", "human_review_required", "persist_chain_of_thought",
    "allow_unattributed_claims",
}
SPATIAL_KEYS = {
    "inference_enabled", "require_evidence", "human_review_required",
    "unknown_scope_policy",
}
GRAPH_KEYS = {
    "allowed_edge_types", "unknown_edge_policy", "preserve_conflicts", "monotonic_merge",
}
EXTRACTION_KEYS = {
    "allowed_source_types", "network_access", "execute_active_content",
    "encoding_policy", "unsupported_input_policy",
}
RETRIEVAL_KEYS = {"chunk_policy"}
CHUNK_KEYS = {
    "mode", "include_evidence_excerpt", "include_source_metadata",
    "include_inferred_content", "empty_evidence_policy", "deduplicate_by",
    "max_chunks_per_node",
}
LIMIT_BOUNDS = {
    "max_input_bytes": (1, 536_870_912),
    "max_expanded_bytes": (1, 1_073_741_824),
    "max_segments": (1, 1_000_000),
    "max_segment_chars": (1, 1_000_000),
    "max_nodes": (1, 1_000_000),
    "max_edges": (1, 10_000_000),
    "max_evidence_items": (1, 10_000_000),
    "max_chunk_chars": (1, 1_000_000),
    "max_output_bytes": (1, 2_147_483_648),
}


class ProfileLoadError(Exception):
    """The profile file could not be safely loaded as strict local JSON."""


class Report:
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.schema_validation = "unavailable"

    @property
    def ok(self) -> bool:
        return not self.errors

    def error(self, message: str) -> None:
        if message not in self.errors:
            self.errors.append(message)

    def warn(self, message: str) -> None:
        if message not in self.warnings:
            self.warnings.append(message)

    def as_dict(self, profile_path: str | None = None) -> dict[str, Any]:
        result = {
            "valid": self.ok,
            "schema_validation": self.schema_validation,
            "errors": sorted(self.errors),
            "warnings": sorted(self.warnings),
        }
        if profile_path is not None:
            result["profile"] = profile_path
        return result


def load_profile(path: str | Path, max_bytes: int = MAX_PROFILE_BYTES) -> dict[str, Any]:
    value = str(path)
    if not value or "\x00" in value:
        raise ProfileLoadError("profile path must be a non-empty local path")
    if URI_PREFIX.match(value) and not re.match(r"^[A-Za-z]:[\\/]", value):
        raise ProfileLoadError("profile URLs and URI schemes are not supported")
    candidate = Path(path).expanduser()
    try:
        resolved = candidate.resolve(strict=True)
        size = resolved.stat().st_size
    except (FileNotFoundError, OSError) as exc:
        raise ProfileLoadError("profile file does not exist or cannot be read") from exc
    if not resolved.is_file():
        raise ProfileLoadError("profile path must identify a regular file")
    if size > max_bytes:
        raise ProfileLoadError(f"profile is {size} bytes; limit is {max_bytes} bytes")
    try:
        raw = resolved.read_bytes()
    except OSError as exc:
        raise ProfileLoadError("profile file cannot be read") from exc
    if len(raw) > max_bytes:
        raise ProfileLoadError(f"profile grew beyond the {max_bytes}-byte limit")
    try:
        source = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ProfileLoadError(f"profile is not valid UTF-8 at byte {exc.start}") from exc
    try:
        profile = strict_json.loads(source, source="profile")
    except strict_json.StrictJsonError as exc:
        raise ProfileLoadError(str(exc)) from exc
    if not isinstance(profile, dict):
        raise ProfileLoadError("profile root must be a JSON object")
    try:
        json.dumps(profile, ensure_ascii=False).encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ProfileLoadError("profile contains an invalid Unicode surrogate") from exc
    return profile


def schema_validate(profile: dict[str, Any], report: Report) -> bool:
    """Apply the bundled schema when jsonschema is installed; never resolve remote refs."""
    try:
        import jsonschema
    except ImportError:
        report.schema_validation = "unavailable"
        return False
    try:
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        validator = jsonschema.Draft202012Validator(schema)
    except (OSError, json.JSONDecodeError, jsonschema.SchemaError) as exc:
        report.error(f"schema: local profile schema is unusable: {exc}")
        report.schema_validation = "failed"
        return True
    report.schema_validation = "ran"
    for error in sorted(
        validator.iter_errors(profile),
        key=lambda item: tuple(str(part) for part in item.absolute_path),
    ):
        location = ".".join(str(part) for part in error.absolute_path) or "$"
        report.error(f"schema {location}: {error.message}")
    return True


def _require_object(
    value: Any,
    path: str,
    required: set[str],
    known: set[str],
    report: Report,
) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        report.error(f"{path}: expected object")
        return None
    for key in sorted(required - set(value)):
        report.error(f"{path}: missing required key {key!r}")
    for key in sorted(set(value) - known):
        report.warn(f"{path}: unknown key {key!r} is ignored")
    return value


def _string(
    obj: dict[str, Any],
    key: str,
    path: str,
    report: Report,
    *,
    allowed: set[str] | None = None,
    maximum: int | None = None,
) -> str | None:
    value = obj.get(key)
    where = f"{path}.{key}"
    if not isinstance(value, str) or not value:
        report.error(f"{where}: expected non-empty string")
        return None
    if maximum is not None and len(value) > maximum:
        report.error(f"{where}: exceeds maximum length {maximum}")
    if allowed is not None and value not in allowed:
        report.error(f"{where}: unsupported value {value!r}; allowed: {sorted(allowed)}")
    return value


def _boolean(obj: dict[str, Any], key: str, path: str, report: Report) -> bool | None:
    value = obj.get(key)
    if not isinstance(value, bool):
        report.error(f"{path}.{key}: expected boolean")
        return None
    return value


def _integer(
    obj: dict[str, Any],
    key: str,
    path: str,
    report: Report,
    minimum: int,
    maximum: int,
) -> int | None:
    value = obj.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        report.error(f"{path}.{key}: expected integer")
        return None
    if value < minimum or value > maximum:
        report.error(f"{path}.{key}: must be between {minimum} and {maximum}")
    return value


def _enum_array(
    obj: dict[str, Any],
    key: str,
    path: str,
    report: Report,
    allowed: set[str],
    *,
    nonempty: bool = True,
) -> list[str] | None:
    value = obj.get(key)
    where = f"{path}.{key}"
    if not isinstance(value, list):
        report.error(f"{where}: expected array")
        return None
    if nonempty and not value:
        report.error(f"{where}: must not be empty")
    if any(not isinstance(item, str) for item in value):
        report.error(f"{where}: every item must be a string")
        return None
    if len(value) != len(set(value)):
        report.error(f"{where}: duplicate items are not allowed")
    unknown = sorted(set(value) - allowed)
    if unknown:
        report.error(f"{where}: unsupported item(s): {unknown}")
    return value


def _normalized_key(value: str) -> str:
    snake_case = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", value)
    return re.sub(r"[^a-z0-9]+", "_", snake_case.lower()).strip("_")


def _scan_sensitive_keys(value: Any, path: str, report: Report) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            key_text = str(key)
            if re.fullmatch(r"[A-Za-z0-9_$-]+", key_text):
                child_path = f"{path}.{key_text}" if path else key_text
            else:
                child_path = f"{path}[{key_text!r}]" if path else repr(key_text)
            normalized = _normalized_key(str(key))
            words = set(normalized.split("_"))
            token_qualifiers = {
                "access", "api", "auth", "bearer", "client", "oauth", "refresh", "session",
            }
            sensitive_words = {"prompt", "secret", "password", "passwd", "credential"}
            credential_shaped = (
                bool(words.intersection(sensitive_words))
                or ("token" in words and bool(words.intersection(token_qualifiers)))
                or ({"api", "key"} <= words)
                or ({"private", "key"} <= words)
            )
            if normalized in SENSITIVE_KEYS or credential_shaped:
                report.error(
                    f"{child_path}: prompts, private reasoning, secrets, credentials, and access tokens are forbidden in profiles"
                )
            _scan_sensitive_keys(child, child_path, report)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _scan_sensitive_keys(child, f"{path}[{index}]", report)


def validate(profile: Any, *, apply_schema: bool = True) -> Report:
    report = Report()
    if apply_schema and isinstance(profile, dict):
        schema_validate(profile, report)
    if not isinstance(profile, dict):
        report.error("$: expected object")
        return report

    _scan_sensitive_keys(profile, "", report)
    root = _require_object(profile, "$", ROOT_KEYS, ROOT_KEYS, report)
    if root is None:
        return report

    schema_ref = _string(root, "$schema", "$", report)
    if schema_ref is not None and schema_ref != "../schema/profile.schema.json":
        report.error("$.$schema: must reference the bundled ../schema/profile.schema.json")
    version = _string(root, "profile_version", "$", report)
    if version is not None:
        if not PROFILE_VERSION.fullmatch(version):
            report.error("$.profile_version: expected <major>.<minor>")
        elif version not in SUPPORTED_PROFILE_VERSIONS:
            report.error(f"$.profile_version: unsupported profile version {version!r}")
    profile_id = _string(root, "profile_id", "$", report, maximum=80)
    if profile_id is not None and not PROFILE_ID.fullmatch(profile_id):
        report.error("$.profile_id: expected kebab-case identifier")
    _string(root, "description", "$", report, maximum=500)

    compiler = _require_object(root.get("compiler"), "$.compiler", COMPILER_KEYS, COMPILER_KEYS, report)
    extraction = _require_object(
        root.get("extraction"), "$.extraction", EXTRACTION_KEYS, EXTRACTION_KEYS, report
    )
    retrieval = _require_object(
        root.get("retrieval"), "$.retrieval", RETRIEVAL_KEYS, RETRIEVAL_KEYS, report
    )
    limits = _require_object(
        root.get("limits"), "$.limits", set(LIMIT_BOUNDS), set(LIMIT_BOUNDS), report
    )

    inference_enabled = None
    inference_review = None
    if compiler is not None:
        evidence = _require_object(
            compiler.get("evidence_policy"),
            "$.compiler.evidence_policy",
            EVIDENCE_KEYS,
            EVIDENCE_KEYS,
            report,
        )
        inference = _require_object(
            compiler.get("inference_policy"),
            "$.compiler.inference_policy",
            INFERENCE_KEYS,
            INFERENCE_KEYS,
            report,
        )
        spatial = _require_object(
            compiler.get("spatial_policy"),
            "$.compiler.spatial_policy",
            SPATIAL_KEYS,
            SPATIAL_KEYS,
            report,
        )
        graph = _require_object(
            compiler.get("graph_policy"),
            "$.compiler.graph_policy",
            GRAPH_KEYS,
            GRAPH_KEYS,
            report,
        )

        if evidence is not None:
            _string(evidence, "mode", "$.compiler.evidence_policy", report, allowed={"evidence_first"})
            targets = _enum_array(
                evidence,
                "required_for",
                "$.compiler.evidence_policy",
                report,
                EVIDENCE_TARGETS,
            )
            if targets is not None and set(targets) != EVIDENCE_TARGETS:
                missing = sorted(EVIDENCE_TARGETS - set(targets))
                report.error(
                    "$.compiler.evidence_policy.required_for: evidence-first profiles must cover "
                    + ", ".join(missing)
                )
            _integer(
                evidence,
                "minimum_items_per_claim",
                "$.compiler.evidence_policy",
                report,
                1,
                100,
            )
            require_selector = _boolean(
                evidence, "require_selector", "$.compiler.evidence_policy", report
            )
            if require_selector is False:
                report.error("$.compiler.evidence_policy.require_selector: must be true")
            _enum_array(
                evidence,
                "allowed_attribution_basis",
                "$.compiler.evidence_policy",
                report,
                ATTRIBUTION_BASES,
            )
            _string(
                evidence,
                "unresolved_claim_policy",
                "$.compiler.evidence_policy",
                report,
                allowed={"reject", "flag"},
            )
            _string(
                evidence,
                "conflict_policy",
                "$.compiler.evidence_policy",
                report,
                allowed={"preserve", "reject"},
            )

        if inference is not None:
            inference_enabled = _boolean(
                inference, "enabled", "$.compiler.inference_policy", report
            )
            inference_review = _boolean(
                inference, "human_review_required", "$.compiler.inference_policy", report
            )
            persist_cot = _boolean(
                inference, "persist_chain_of_thought", "$.compiler.inference_policy", report
            )
            unattributed = _boolean(
                inference, "allow_unattributed_claims", "$.compiler.inference_policy", report
            )
            if inference_enabled is True and inference_review is not True:
                report.error(
                    "$.compiler.inference_policy: enabled inference requires human_review_required=true"
                )
            if persist_cot is True:
                report.error(
                    "$.compiler.inference_policy.persist_chain_of_thought: hidden chain-of-thought must not be persisted"
                )
            if unattributed is True:
                report.error(
                    "$.compiler.inference_policy.allow_unattributed_claims: unattributed claims are forbidden"
                )

        if spatial is not None:
            spatial_enabled = _boolean(
                spatial, "inference_enabled", "$.compiler.spatial_policy", report
            )
            spatial_evidence = _boolean(
                spatial, "require_evidence", "$.compiler.spatial_policy", report
            )
            spatial_review = _boolean(
                spatial, "human_review_required", "$.compiler.spatial_policy", report
            )
            _string(
                spatial,
                "unknown_scope_policy",
                "$.compiler.spatial_policy",
                report,
                allowed={"omit", "flag"},
            )
            if spatial_evidence is False:
                report.error("$.compiler.spatial_policy.require_evidence: must be true")
            if spatial_enabled is True and inference_enabled is not True:
                report.error(
                    "$.compiler.spatial_policy: spatial inference requires compiler inference to be enabled"
                )
            if spatial_enabled is True and spatial_review is not True:
                report.error(
                    "$.compiler.spatial_policy: spatial inference requires human_review_required=true"
                )

        if graph is not None:
            _enum_array(
                graph,
                "allowed_edge_types",
                "$.compiler.graph_policy",
                report,
                EDGE_TYPES,
            )
            _string(
                graph,
                "unknown_edge_policy",
                "$.compiler.graph_policy",
                report,
                allowed={"reject"},
            )
            preserve = _boolean(graph, "preserve_conflicts", "$.compiler.graph_policy", report)
            monotonic = _boolean(graph, "monotonic_merge", "$.compiler.graph_policy", report)
            if preserve is False:
                report.error("$.compiler.graph_policy.preserve_conflicts: must be true")
            if monotonic is False:
                report.error("$.compiler.graph_policy.monotonic_merge: must be true")

        if evidence is not None:
            bases = evidence.get("allowed_attribution_basis")
            if isinstance(bases, list) and "model_inferred" in bases and inference_enabled is not True:
                report.error(
                    "$.compiler.evidence_policy.allowed_attribution_basis: model_inferred requires inference to be enabled"
                )

    if extraction is not None:
        _enum_array(
            extraction,
            "allowed_source_types",
            "$.extraction",
            report,
            SOURCE_TYPES,
        )
        network = _boolean(extraction, "network_access", "$.extraction", report)
        active = _boolean(extraction, "execute_active_content", "$.extraction", report)
        if network is True:
            report.error("$.extraction.network_access: network access is forbidden in local profiles")
        if active is True:
            report.error("$.extraction.execute_active_content: active content execution is forbidden")
        _string(
            extraction,
            "encoding_policy",
            "$.extraction",
            report,
            allowed={"utf-8-strict"},
        )
        _string(
            extraction,
            "unsupported_input_policy",
            "$.extraction",
            report,
            allowed={"reject"},
        )

    if retrieval is not None:
        chunk = _require_object(
            retrieval.get("chunk_policy"),
            "$.retrieval.chunk_policy",
            CHUNK_KEYS,
            CHUNK_KEYS,
            report,
        )
        if chunk is not None:
            _string(chunk, "mode", "$.retrieval.chunk_policy", report, allowed={"evidence_first"})
            include_excerpt = _boolean(
                chunk, "include_evidence_excerpt", "$.retrieval.chunk_policy", report
            )
            include_source = _boolean(
                chunk, "include_source_metadata", "$.retrieval.chunk_policy", report
            )
            include_inferred = _boolean(
                chunk, "include_inferred_content", "$.retrieval.chunk_policy", report
            )
            if include_excerpt is False:
                report.error("$.retrieval.chunk_policy.include_evidence_excerpt: must be true")
            if include_source is False:
                report.error("$.retrieval.chunk_policy.include_source_metadata: must be true")
            if include_inferred is True and not (inference_enabled is True and inference_review is True):
                report.error(
                    "$.retrieval.chunk_policy.include_inferred_content: requires reviewed inference to be enabled"
                )
            _string(
                chunk,
                "empty_evidence_policy",
                "$.retrieval.chunk_policy",
                report,
                allowed={"exclude", "flag"},
            )
            dedup = _enum_array(
                chunk,
                "deduplicate_by",
                "$.retrieval.chunk_policy",
                report,
                {"source", "selector", "text_sha256"},
            )
            if dedup is not None and dedup != ["source", "selector", "text_sha256"]:
                report.error(
                    "$.retrieval.chunk_policy.deduplicate_by: expected deterministic order "
                    "['source', 'selector', 'text_sha256']"
                )
            _integer(
                chunk,
                "max_chunks_per_node",
                "$.retrieval.chunk_policy",
                report,
                1,
                100,
            )

    if limits is not None:
        for key, (minimum, maximum) in LIMIT_BOUNDS.items():
            _integer(limits, key, "$.limits", report, minimum, maximum)

    return report


def _text_report(report: Report, profile_path: str) -> str:
    lines = [
        ("VALID" if report.ok else "INVALID") + f" profile: {profile_path}",
        f"schema validation: {report.schema_validation}",
    ]
    if report.errors:
        lines.append("errors:")
        lines.extend(f"  - {message}" for message in sorted(report.errors))
    if report.warnings:
        lines.append("warnings:")
        lines.extend(f"  - {message}" for message in sorted(report.warnings))
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate a local compiler/extraction profile without network access."
    )
    parser.add_argument("profile", nargs="?", default=str(DEFAULT_PROFILE_PATH))
    parser.add_argument("--json", action="store_true", help="Emit a deterministic JSON report")
    parser.add_argument(
        "--stdlib-only",
        action="store_true",
        help="Skip the optional jsonschema mirror check",
    )
    args = parser.parse_args(argv)

    try:
        profile = load_profile(args.profile)
    except ProfileLoadError as exc:
        print(f"error [profile_load]: {exc}", file=sys.stderr)
        return 2

    report = validate(profile, apply_schema=not args.stdlib_only)
    display_path = Path(args.profile).name
    if args.json:
        sys.stdout.buffer.write(
            (
                json.dumps(
                    report.as_dict(display_path),
                    ensure_ascii=False,
                    sort_keys=True,
                    indent=2,
                )
                + "\n"
            ).encode("utf-8")
        )
    else:
        sys.stdout.write(_text_report(report, display_path))
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
