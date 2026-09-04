"""Tests for the closed-world golden evaluator."""
from __future__ import annotations

import contextlib
import copy
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import evaluate_golden  # noqa: E402

MANIFEST = ROOT / "eval" / "golden_cases.json"
FIXTURE = ROOT / "tests" / "fixtures" / "contextual.knowledge.json"


class GoldenEvaluationTests(unittest.TestCase):
    def test_manifest_loader_rejects_duplicate_keys_nonfinite_numbers_and_surrogates(self):
        invalid_payloads = (
            ('{"version":"1.0","version":"1.0","cases":[]}', "duplicate object key"),
            ('{"version":"1.0","cases":[],"value":NaN}', "non-finite number"),
            ('{"version":"1.0","cases":[],"value":"\\ud800"}', "isolated Unicode surrogate"),
        )
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "golden.json"
            for payload, expected in invalid_payloads:
                with self.subTest(payload=payload):
                    path.write_text(payload, encoding="utf-8")
                    with self.assertRaisesRegex(evaluate_golden.EvaluationError, expected):
                        evaluate_golden.evaluate_manifest(path)

    def test_shipped_golden_case_passes_exactly(self):
        first = evaluate_golden.evaluate_manifest(MANIFEST)
        second = evaluate_golden.evaluate_manifest(MANIFEST)
        self.assertEqual(first, second)
        self.assertTrue(first["passed"])
        case = first["cases"][0]
        self.assertEqual(case["micro"]["f1"], 1.0)
        self.assertEqual(case["coverage"]["evidence_with_locator"], 1.0)
        self.assertEqual(case["coverage"]["inferred_records_with_derivation"], 1.0)
        self.assertEqual(case["stable_ids"]["rate"], 1.0)
        self.assertTrue(first["semantic_accuracy"]["evaluated"])
        self.assertIn("golden fields only", first["semantic_accuracy"]["scope"])

    def test_missing_labeled_claim_fails_threshold(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            graph = json.loads(FIXTURE.read_text(encoding="utf-8"))
            graph["claims"] = []
            graph["nodes"][0]["claim_ids"] = []
            graph_path = base / "graph.knowledge.json"
            graph_path.write_text(json.dumps(graph), encoding="utf-8")
            manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
            manifest["cases"][0]["graph"] = graph_path.name
            manifest_path = base / "golden.json"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            result = evaluate_golden.evaluate_manifest(manifest_path)
            self.assertFalse(result["passed"])
            self.assertEqual(result["cases"][0]["categories"]["claims"]["false_negatives"], 1)

    def test_remote_graph_reference_is_rejected(self):
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        manifest["cases"][0]["graph"] = "https://example.invalid/graph.json"
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "golden.json"
            path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(evaluate_golden.EvaluationError, "local path"):
                evaluate_golden.evaluate_manifest(path)

    def test_report_output_cannot_overwrite_manifest_or_graph(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            graph_path = base / "graph.knowledge.json"
            shutil.copyfile(FIXTURE, graph_path)
            manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
            manifest["cases"][0]["graph"] = graph_path.name
            manifest_path = base / "golden.json"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            original_manifest = manifest_path.read_bytes()
            original_graph = graph_path.read_bytes()

            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(
                    evaluate_golden.main(
                        [str(manifest_path), "--output", str(manifest_path)]
                    ),
                    2,
                )
            self.assertEqual(manifest_path.read_bytes(), original_manifest)

            hardlink = base / "report.json"
            os.link(graph_path, hardlink)
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(
                    evaluate_golden.main(
                        [str(manifest_path), "--output", str(hardlink)]
                    ),
                    2,
                )
            self.assertEqual(graph_path.read_bytes(), original_graph)


if __name__ == "__main__":
    unittest.main(verbosity=2)
