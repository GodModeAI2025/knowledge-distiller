#!/usr/bin/env python3
"""Evaluate a local Knowledge Distiller graph against explicit golden labels.

This is deliberately an offline, closed-world regression evaluator.  It measures only the
fields declared in a golden case and never turns a passing score into a claim that the source,
graph, or model is universally correct.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import build_graph  # noqa: E402
import strict_json  # noqa: E402
import validate_knowledge as validator  # noqa: E402

MANIFEST_VERSION = "1.0"
MAX_FILE_BYTES = 64 * 1024 * 1024
ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")

PROJECTIONS = {
    "nodes": ("id", "resource"),
    "edges": ("source", "type", "target"),
    "claims": ("id", "node", "statement"),
    "facts": ("id", "statement", "value", "source"),
    "evidence": ("id", "source", "selector_type"),
}


class EvaluationError(ValueError):
    pass


def _read_json(path: Path) -> Any:
    if not path.is_file():
        raise EvaluationError(f"not a regular file: {path}")
    if path.stat().st_size > MAX_FILE_BYTES:
        raise EvaluationError(f"file exceeds {MAX_FILE_BYTES} byte limit: {path}")
    try:
        return strict_json.load_path(path)
    except strict_json.StrictJsonError as exc:
        raise EvaluationError(f"invalid strict JSON in {path}: {exc}") from exc


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _project(category: str, item: dict[str, Any]) -> dict[str, Any]:
    if category == "evidence":
        selector = item.get("selector") if isinstance(item.get("selector"), dict) else {}
        augmented = dict(item)
        if selector:
            augmented["selector_type"] = selector.get("type")
        item = augmented
    return {key: item.get(key) for key in PROJECTIONS[category]}


def _metric(category: str, actual: list[dict[str, Any]], expected: list[dict[str, Any]]) -> dict[str, Any]:
    actual_set = {_canonical(_project(category, item)) for item in actual if isinstance(item, dict)}
    expected_set = {_canonical(_project(category, item)) for item in expected if isinstance(item, dict)}
    true_positives = len(actual_set & expected_set)
    false_positives = len(actual_set - expected_set)
    false_negatives = len(expected_set - actual_set)
    precision = true_positives / len(actual_set) if actual_set else (1.0 if not expected_set else 0.0)
    recall = true_positives / len(expected_set) if expected_set else (1.0 if not actual_set else 0.0)
    f1 = (2 * precision * recall / (precision + recall)) if precision + recall else 0.0
    return {
        "true_positives": true_positives,
        "false_positives": false_positives,
        "false_negatives": false_negatives,
        "precision": round(precision, 6),
        "recall": round(recall, 6),
        "f1": round(f1, 6),
        "missing": [json.loads(item) for item in sorted(expected_set - actual_set)],
        "unexpected": [json.loads(item) for item in sorted(actual_set - expected_set)],
    }


def _coverage(doc: dict[str, Any]) -> dict[str, Any]:
    originated: list[dict[str, Any]] = []
    inferred: list[dict[str, Any]] = []
    for collection in ("claims", "edges", "facts", "chunks"):
        for item in doc.get(collection, []) or []:
            if not isinstance(item, dict) or item.get("origin") is None:
                continue
            originated.append(item)
            if item.get("origin") in {"model_inferred", "rule_derived", "synthesized"}:
                inferred.append(item)
    evidence_records = [item for item in (doc.get("evidence", []) or []) if isinstance(item, dict)]
    claims = [item for item in (doc.get("claims", []) or []) if isinstance(item, dict)]
    nodes = {item.get("id"): item for item in (doc.get("nodes", []) or []) if isinstance(item, dict)}

    def ratio(hit: int, total: int) -> float:
        return round(hit / total, 6) if total else 1.0

    return {
        "originated_records_with_evidence": ratio(
            sum(bool(item.get("evidence")) for item in originated), len(originated)
        ),
        "inferred_records_with_derivation": ratio(
            sum(isinstance(item.get("derivation"), dict) for item in inferred), len(inferred)
        ),
        "evidence_with_locator": ratio(
            sum(isinstance(item.get("selector"), dict) and bool(item["selector"].get("type")) for item in evidence_records),
            len(evidence_records),
        ),
        "claims_projected_to_nodes": ratio(
            sum(
                item.get("node") in nodes and item.get("statement") in (nodes[item.get("node")].get("statements") or [])
                for item in claims
            ),
            len(claims),
        ),
    }


def _stable_ids(doc: dict[str, Any]) -> dict[str, Any]:
    checks: list[bool] = []
    details: dict[str, float] = {}
    for collection in ("nodes", "claims", "facts", "evidence"):
        items = [item for item in (doc.get(collection, []) or []) if isinstance(item, dict)]
        results = [bool(ID_PATTERN.fullmatch(str(item.get("id", "")))) for item in items]
        details[collection] = round(sum(results) / len(results), 6) if results else 1.0
        checks.extend(results)
    edges = [item for item in (doc.get("edges", []) or []) if isinstance(item, dict)]
    edge_results = [item.get("id") == build_graph.canonical_edge_id(item) for item in edges]
    details["edges"] = round(sum(edge_results) / len(edge_results), 6) if edge_results else 1.0
    checks.extend(edge_results)
    return {
        "rate": round(sum(checks) / len(checks), 6) if checks else 1.0,
        "by_collection": details,
    }


def evaluate_case(case: dict[str, Any], base_dir: Path) -> dict[str, Any]:
    case_id = case.get("id")
    graph_value = case.get("graph")
    if not isinstance(case_id, str) or not case_id:
        raise EvaluationError("each case requires a non-empty id")
    if not isinstance(graph_value, str) or not graph_value or "://" in graph_value:
        raise EvaluationError(f"case {case_id}: graph must be a local path")
    graph_path = Path(graph_value)
    if not graph_path.is_absolute():
        graph_path = (base_dir / graph_path).resolve()
    doc = _read_json(graph_path)
    if not isinstance(doc, dict):
        raise EvaluationError(f"case {case_id}: graph must contain a JSON object")

    report = validator.Report()
    schema_checked = validator.schema_validate(doc, report)
    core = validator.validate(doc, ran_schema=schema_checked)
    report.errors.extend(core.errors)
    report.warnings.extend(core.warnings)

    expected = case.get("expected")
    if not isinstance(expected, dict):
        raise EvaluationError(f"case {case_id}: expected must be an object")
    metrics: dict[str, Any] = {}
    totals = {"tp": 0, "fp": 0, "fn": 0}
    for category in PROJECTIONS:
        wanted = expected.get(category, [])
        if not isinstance(wanted, list):
            raise EvaluationError(f"case {case_id}: expected.{category} must be an array")
        result = _metric(category, doc.get(category, []) or [], wanted)
        metrics[category] = result
        totals["tp"] += result["true_positives"]
        totals["fp"] += result["false_positives"]
        totals["fn"] += result["false_negatives"]
    precision = totals["tp"] / (totals["tp"] + totals["fp"]) if totals["tp"] + totals["fp"] else 1.0
    recall = totals["tp"] / (totals["tp"] + totals["fn"]) if totals["tp"] + totals["fn"] else 1.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    minimum = case.get("minimum_f1", 1.0)
    if not isinstance(minimum, (int, float)) or isinstance(minimum, bool) or not 0 <= minimum <= 1:
        raise EvaluationError(f"case {case_id}: minimum_f1 must be within 0..1")
    passed = report.ok and f1 >= minimum
    return {
        "id": case_id,
        "graph": graph_value,
        "passed": passed,
        "minimum_f1": minimum,
        "micro": {
            "precision": round(precision, 6),
            "recall": round(recall, 6),
            "f1": round(f1, 6),
        },
        "categories": metrics,
        "coverage": _coverage(doc),
        "stable_ids": _stable_ids(doc),
        "validation": {
            "ok": report.ok,
            "errors": report.errors,
            "warnings": report.warnings,
            "conformance_score": report.conformance_score(),
            "schema_checked": schema_checked,
        },
    }


def evaluate_manifest(path: Path) -> dict[str, Any]:
    manifest = _read_json(path)
    if not isinstance(manifest, dict) or manifest.get("version") != MANIFEST_VERSION:
        raise EvaluationError(f"manifest version must be {MANIFEST_VERSION}")
    cases = manifest.get("cases")
    if not isinstance(cases, list) or not cases:
        raise EvaluationError("manifest requires at least one case")
    results = [evaluate_case(case, path.resolve().parent) for case in cases]
    return {
        "version": MANIFEST_VERSION,
        "passed": all(item["passed"] for item in results),
        "case_count": len(results),
        "cases": results,
        "semantic_accuracy": {
            "evaluated": True,
            "scope": "Exact agreement with the explicit, locally curated golden fields only.",
            "not_evaluated": [
                "truth beyond the cited source material",
                "open-world completeness beyond the golden labels",
                "fitness for an unstated downstream purpose",
            ],
        },
    }


def _protected_input_paths(manifest_path: Path) -> list[Path]:
    """Return the manifest and every local graph it references for output collision checks."""
    manifest = _read_json(manifest_path)
    paths = [manifest_path.resolve()]
    if not isinstance(manifest, dict) or not isinstance(manifest.get("cases"), list):
        return paths
    for case in manifest["cases"]:
        if not isinstance(case, dict):
            continue
        value = case.get("graph")
        if not isinstance(value, str) or not value or "://" in value:
            continue
        graph_path = Path(value)
        if not graph_path.is_absolute():
            graph_path = manifest_path.resolve().parent / graph_path
        paths.append(graph_path.resolve())
    return paths


def _atomic_report_write(output: Path, payload: str, protected: list[Path]) -> None:
    if output.is_symlink():
        raise EvaluationError(f"output is a symlink: {output}")
    if output.exists() and not output.is_file():
        raise EvaluationError(f"output is not a regular file: {output}")
    for source in protected:
        try:
            collision = output.resolve(strict=False) == source.resolve(strict=True)
            if output.exists():
                collision = collision or os.path.samefile(output, source)
        except OSError as exc:
            raise EvaluationError(f"could not validate output target: {exc}") from exc
        if collision:
            raise EvaluationError("output must differ from the golden manifest and graph inputs")
    if not output.parent.exists() or not output.parent.is_dir():
        raise EvaluationError(f"output directory does not exist: {output.parent}")

    temp_name: str | None = None
    try:
        descriptor, temp_name = tempfile.mkstemp(
            prefix=".kd-evaluation-", suffix=".tmp", dir=output.parent
        )
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
            # On the descriptor, not on the name: the mode belongs to
            # the file just written, not to whatever carries that name
            # by the time the call runs.
            os.fchmod(handle.fileno(), 0o644)
        os.replace(temp_name, output)
        temp_name = None
    finally:
        if temp_name is not None:
            try:
                Path(temp_name).unlink()
            except FileNotFoundError:
                pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate local graphs against explicit golden labels")
    parser.add_argument("manifest", help="path to a golden_cases.json manifest")
    parser.add_argument("--output", help="optional JSON report path")
    args = parser.parse_args(argv)
    try:
        result = evaluate_manifest(Path(args.manifest))
        payload = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        if args.output:
            manifest_path = Path(args.manifest)
            _atomic_report_write(
                Path(args.output), payload, _protected_input_paths(manifest_path)
            )
        else:
            print(payload, end="")
        return 0 if result["passed"] else 1
    except (OSError, EvaluationError) as exc:
        print(f"evaluation failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
