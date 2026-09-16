"""Tests for resolving graph evidence against normalized local sources."""
from __future__ import annotations

import contextlib
import copy
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import extract_source  # noqa: E402
import verify_evidence  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "contextual.knowledge.json"
SOURCE_TEXT = (
    "The guide states: Alpha enables Beta in the German deployment profile.\n\n"
    "Two deployments were observed.\n"
)


class VerifyEvidenceTests(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.base = Path(self._temp.name)
        (self.base / "guide.txt").write_text(SOURCE_TEXT, encoding="utf-8")
        self.normalized = extract_source.extract_source("guide.txt", input_root=self.base)
        self.graph = json.loads(FIXTURE.read_text(encoding="utf-8"))
        digest = self.normalized["source"]["content_sha256"]
        for source in self.graph["metadata"]["sources"]:
            if source["id"] == self.graph["evidence"][0]["source"]:
                source["content_sha256"] = digest

    def tearDown(self):
        self._temp.cleanup()

    def _run(self, graph, *extra):
        graph_path = self.base / "graph.knowledge.json"
        graph_path.write_text(json.dumps(graph), encoding="utf-8")
        normalized_path = self.base / "guide.source.json"
        normalized_path.write_text(json.dumps(self.normalized), encoding="utf-8")
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = verify_evidence.main([str(graph_path), str(normalized_path), "--json", *extra])
        return code, json.loads(out.getvalue()) if out.getvalue() else None

    def test_quote_present_in_source_is_verified(self):
        code, report = self._run(self.graph)
        self.assertEqual(code, 0)
        self.assertEqual(report["counts"]["verified"], 1)
        self.assertFalse(report["semantic_support"]["evaluated"])

    def test_invented_quote_is_not_found_and_fails(self):
        graph = copy.deepcopy(self.graph)
        graph["evidence"][0]["selector"]["exact"] = "Alpha replaces Beta everywhere."
        del graph["evidence"][0]["excerpt"]
        code, report = self._run(graph)
        self.assertEqual(code, 1)
        self.assertEqual(report["counts"]["not_found"], 1)

    def test_diverging_excerpt_is_not_found(self):
        graph = copy.deepcopy(self.graph)
        graph["evidence"][0]["excerpt"] = "Alpha enables Gamma."
        code, report = self._run(graph)
        self.assertEqual(code, 1)
        self.assertIn("excerpt", report["results"][0]["detail"])

    def test_whitespace_differences_do_not_break_an_anchor(self):
        graph = copy.deepcopy(self.graph)
        graph["evidence"][0]["selector"]["exact"] = "Alpha  enables\nBeta in the German deployment profile."
        del graph["evidence"][0]["excerpt"]
        code, _ = self._run(graph)
        self.assertEqual(code, 0)

    def test_positions_and_copied_selectors_resolve(self):
        segment = self.normalized["segments"][0]
        position = next(s for s in segment["selectors"] if s["type"] == "TextPositionSelector")
        graph = copy.deepcopy(self.graph)
        graph["evidence"][0]["selector"] = {"type": "TextPositionSelector",
                                            "start": position["start"] + 18,
                                            "end": position["start"] + 23}
        graph["evidence"][0]["excerpt"] = "Alpha"
        code, report = self._run(graph)
        self.assertEqual((code, report["counts"]["verified"]), (0, 1))
        graph["evidence"][0]["excerpt"] = "Omega"
        code, report = self._run(graph)
        self.assertEqual((code, report["counts"]["not_found"]), (1, 1))
        graph["evidence"][0]["selector"] = {"type": "TextPositionSelector", "start": 5000, "end": 5010}
        del graph["evidence"][0]["excerpt"]
        code, report = self._run(graph)
        self.assertEqual((code, report["counts"]["not_found"]), (1, 1))

    def test_unicode_normalization_does_not_break_an_anchor(self):
        (self.base / "note.txt").write_text("Die Gr\u00f6\u00dfe bleibt gleich.\n", encoding="utf-8")
        normalized = extract_source.extract_source("note.txt", input_root=self.base)
        graph = {"metadata": {"sources": [{"id": "n", "content_sha256": normalized["source"]["content_sha256"]}]},
                 "evidence": [{"id": "e", "source": "n",
                               "selector": {"type": "TextQuoteSelector", "exact": "Gro\u0308\u00dfe bleibt"}}]}
        self.assertEqual(verify_evidence.verify(graph, [normalized])["counts"]["verified"], 1)

    def test_narrowed_cell_ranges_and_json_containers_resolve(self):
        (self.base / "people.csv").write_text("name,role\nAda,Engineer\n", encoding="utf-8")
        (self.base / "config.json").write_text('{"deploy": {"region": "eu", "zones": 3}}', encoding="utf-8")
        csv_doc = extract_source.extract_source("people.csv", input_root=self.base)
        json_doc = extract_source.extract_source("config.json", input_root=self.base)
        graph = {"metadata": {"sources": [
            {"id": "c", "content_sha256": csv_doc["source"]["content_sha256"]},
            {"id": "j", "content_sha256": json_doc["source"]["content_sha256"]}]},
            "evidence": [
                {"id": "cell", "source": "c", "selector": {"type": "CsvSelector", "sheet": "data", "cell_range": "B2"}},
                {"id": "wide", "source": "c", "selector": {"type": "CsvSelector", "sheet": "data", "cell_range": "A2:C2"}},
                {"id": "node", "source": "j", "selector": {"type": "JsonPointerSelector", "json_pointer": "/deploy"}},
                {"id": "prefix", "source": "j", "selector": {"type": "JsonPointerSelector", "json_pointer": "/dep"}},
            ]}
        results = {r["evidence"]: r["status"] for r in verify_evidence.verify(graph, [csv_doc, json_doc])["results"]}
        self.assertEqual(results, {"cell": "verified", "wide": "not_found", "node": "verified", "prefix": "not_found"})

    def test_unknown_source_is_unverifiable_not_verified(self):
        graph = copy.deepcopy(self.graph)
        for source in graph["metadata"]["sources"]:
            source["content_sha256"] = "0" * 64
        code, report = self._run(graph)
        self.assertEqual((code, report["counts"]["unverifiable"]), (0, 1))
        code, _ = self._run(graph, "--require-all")
        self.assertEqual(code, 1)

    def test_jsonl_adapter_output_is_accepted(self):
        path = self.base / "guide.source.jsonl"
        lines = [{"record_type": "source", "source": self.normalized["source"]}]
        lines += [dict(segment, record_type="segment") for segment in self.normalized["segments"]]
        path.write_text("\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8")
        document = verify_evidence.load_normalized(path)
        report = verify_evidence.verify(self.graph, [document])
        self.assertEqual(report["counts"]["verified"], 1)

    def test_non_adapter_input_is_rejected(self):
        path = self.base / "other.json"
        path.write_text('{"segments": []}', encoding="utf-8")
        with self.assertRaises(verify_evidence.VerificationError):
            verify_evidence.load_normalized(path)


if __name__ == "__main__":
    unittest.main()
