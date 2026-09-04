"""Focused regression tests for deterministic offline export formats."""

from __future__ import annotations

import contextlib
import copy
import io
import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import build_exports as exports  # noqa: E402


FIXTURE = ROOT / "tests" / "fixtures" / "minimal.knowledge.json"


def load_fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def extended_graph() -> dict:
    graph = load_fixture()
    graph["metadata"]["distiller_spec_version"] = "1.1"
    graph["metadata"]["conformance_score"] = 100
    evidence = {
        "id": "ev:alpha:1",
        "source": "s1",
        "selector": {
            "type": "TextQuoteSelector",
            "exact": "Alpha is located in Berlin.",
            "prefix": "Evidence: ",
        },
        "support": "supports",
        "attribution_basis": "source_explicit",
        "excerpt": "Alpha is located in Berlin.",
        "review_status": "reviewed",
    }
    spatial = {
        "role": "event_location",
        "place": {
            "id": "wikidata:Q64",
            "label": "Berlin",
            "kind": "city",
            "identifiers": [{"scheme": "wikidata", "value": "Q64"}],
            "precision": "city",
            "sensitive": False,
            "redacted": False,
        },
        "basis": "source_explicit",
        "confidence": "high",
        "evidence": ["ev:alpha:1"],
    }
    derivation = {
        "kind": "rule_derived",
        "activity": "join-explicit-facts",
        "inputs": ["ev:alpha:1"],
        "rule": "same subject and time window",
        "summary": "Combined two explicit source assertions; no hidden reasoning trace.",
        "review_status": "reviewed",
        "evidence": ["ev:alpha:1"],
    }
    claim = {
        "id": "claim:alpha:berlin",
        "node": "alpha",
        "statement": "Alpha is located in Berlin.",
        "confidence": "high",
        "origin": "source_stated",
        "evidence": ["ev:alpha:1"],
        "spatial_contexts": [spatial],
        "derivation": derivation,
        "review_status": "reviewed",
    }
    graph["evidence"] = [evidence]
    graph["claims"] = [claim]
    graph["assessments"] = [
        {
            "id": "assessment:authority:1",
            "dimension": "source_authority",
            "scope": "claim:alpha:berlin",
            "value": "primary_source",
            "assessor": "human-reviewer",
            "method": "source inspection",
            "assessed_at": "2026-09-04",
            "evidence": ["ev:alpha:1"],
        }
    ]
    graph["nodes"][0]["evidence"] = ["ev:alpha:1"]
    graph["nodes"][0]["claim_ids"] = ["claim:alpha:berlin"]
    graph["nodes"][0]["spatial_contexts"] = [spatial]
    graph["nodes"][0]["statements"].append("Alpha is located in Berlin.")
    graph["edges"][0].update(
        {
            "evidence": ["ev:alpha:1"],
            "origin": "rule_derived",
            "explanation": "An explicit relation supported by the cited span.",
            "derivation": derivation,
            "spatial_contexts": [spatial],
        }
    )
    graph["facts"][0].update(
        {
            "evidence": ["ev:alpha:1"],
            "origin": "source_stated",
            "derivation": derivation,
            "spatial_contexts": [spatial],
        }
    )
    graph["chunks"][0].update(
        {
            "kind": "inference",
            "evidence": ["ev:alpha:1"],
            "origin": "rule_derived",
            "derivation": derivation,
            "include_in_default_retrieval": False,
            "spatial_contexts": [spatial],
        }
    )
    # Forward-compatible unknown data must survive every export.
    graph["future_extension"] = {"nested": [1, {"opaque": "kept exactly"}]}
    return graph


def parse_ctxt_graph(text: str) -> dict:
    line = next(item for item in text.splitlines() if item.startswith("graph-json: "))
    return json.loads(line.removeprefix("graph-json: "))


class CypherExportCase(unittest.TestCase):
    def test_spec_10_script_is_deterministic_and_idempotent(self) -> None:
        graph = load_fixture()
        first = exports.render_cypher(graph)
        second = exports.render_cypher(copy.deepcopy(graph))
        self.assertEqual(first, second)
        self.assertIn("CREATE CONSTRAINT kd_entity_id IF NOT EXISTS", first)
        self.assertIn("MERGE (n:KDEntity", first)
        self.assertIn("MERGE (a)-[r:KD_RELATIONSHIP", first)
        self.assertNotIn("CREATE (", first)
        self.assertTrue(all(line.endswith(";") for line in first.splitlines() if not line.startswith("//")))

    def test_property_encoding_and_relationship_types_are_guarded(self) -> None:
        self.assertEqual(exports.cypher_string("a'b\\c\n"), "'a\\'b\\\\c\\n'")
        graph = load_fixture()
        graph["nodes"][0]["label"] = "x'); MATCH (pwn) DETACH DELETE pwn; //"
        graph["edges"][0]["type"] = "uses`) DELETE pwn; //"
        script = exports.render_cypher(graph)
        self.assertNotIn("label: 'x'); MATCH", script)
        self.assertIn("label: 'x\\'); MATCH", script)
        relation_types = {
            match.group(1)
            for match in re.finditer(r"\[r:([A-Z_]+)", script)
        }
        self.assertTrue(relation_types)
        self.assertLessEqual(relation_types, exports._REL_TYPES)

    def test_stable_concept_id_does_not_depend_on_display_label(self) -> None:
        graph = load_fixture()
        expected = exports.stable_id("concept", "alpha")
        self.assertIn(expected, exports.render_cypher(graph))
        graph["nodes"][0]["label"] = "Renamed Alpha"
        self.assertIn(expected, exports.render_cypher(graph))

    def test_extended_entities_and_full_source_json_are_preserved(self) -> None:
        graph = extended_graph()
        script = exports.render_cypher(graph)
        self.assertIn("SET n:KDClaim", script)
        self.assertIn("SET n:KDEvidence", script)
        self.assertIn("SET n:KDAssessment", script)
        self.assertIn("[r:SUPPORTED_BY", script)
        self.assertIn("spatial_contexts_json", script)
        self.assertIn("derivation_json", script)
        expected_literal = exports.cypher_string(exports.canonical_json(graph))
        self.assertIn(f"source_graph_json: {expected_literal}", script)


class CTXTExportCase(unittest.TestCase):
    def test_ctxt_is_deterministic_and_lossless_at_graph_boundary(self) -> None:
        graph = extended_graph()
        text = exports.render_ctxt(graph)
        self.assertEqual(text, exports.render_ctxt(copy.deepcopy(graph)))
        self.assertTrue(text.startswith("#!KD-CTXT/1\n"))
        self.assertEqual(parse_ctxt_graph(text), graph)
        self.assertIn("spatial-contexts-json: ", text)
        self.assertIn("derivation-json: ", text)
        self.assertIn("record-json: ", text)

    def test_record_json_is_authoritative_when_body_contains_delimiters(self) -> None:
        graph = load_fixture()
        body = "first\n===\nrecord-json: fake\n---\nlast"
        graph["chunks"][0]["text"] = body
        text = exports.render_ctxt(graph)
        record_line = next(item for item in text.splitlines() if item.startswith("record-json: "))
        record = json.loads(record_line.removeprefix("record-json: "))
        self.assertEqual(record["raw"]["text"], body)
        self.assertEqual(parse_ctxt_graph(text), graph)

        encoded = text.encode("utf-8")
        header_end = encoded.index(b"---\n")
        header = encoded[:header_end].decode("utf-8")
        length_line = [line for line in header.splitlines() if line.startswith("body-utf8-bytes: ")][-1]
        body_length = int(length_line.removeprefix("body-utf8-bytes: "))
        body_start = header_end + len(b"---\n")
        self.assertEqual(encoded[body_start : body_start + body_length].decode("utf-8"), body)
        self.assertEqual(encoded[body_start + body_length : body_start + body_length + 1], b"\n")

    def test_json_metadata_stays_on_one_physical_line(self) -> None:
        graph = load_fixture()
        graph["metadata"]["title"] = "before\u0085middle\u2028after\u2029end"
        text = exports.render_ctxt(graph)
        header = text.split("===\n", 1)[0]
        title_lines = [line for line in header.splitlines() if line.startswith("title-json: ")]
        self.assertEqual(len(title_lines), 1)
        self.assertEqual(json.loads(title_lines[0].removeprefix("title-json: ")), graph["metadata"]["title"])

    def test_concepts_become_context_records_when_chunks_are_absent(self) -> None:
        graph = load_fixture()
        graph["chunks"] = []
        text = exports.render_ctxt(graph)
        records = [
            json.loads(line.removeprefix("record-json: "))
            for line in text.splitlines()
            if line.startswith("record-json: ")
        ]
        self.assertEqual([item["id"] for item in records], ["context:alpha", "context:beta", "context:gamma"])
        self.assertTrue(all(item["synthetic"] for item in records))


class CanvasExportCase(unittest.TestCase):
    def test_canvas_has_resolvable_stable_ids_and_positions(self) -> None:
        graph = load_fixture()
        document = json.loads(exports.render_canvas(graph))
        ids = [item["id"] for item in document["nodes"]]
        self.assertEqual(len(ids), len(set(ids)))
        node_ids = set(ids)
        self.assertTrue(all(edge["fromNode"] in node_ids for edge in document["edges"]))
        self.assertTrue(all(edge["toNode"] in node_ids for edge in document["edges"]))
        self.assertTrue(all(isinstance(item["x"], int) and isinstance(item["y"], int) for item in document["nodes"]))
        text_nodes = [item for item in document["nodes"] if item["type"] == "text"]
        by_column: dict[int, list[dict]] = {}
        for item in text_nodes:
            by_column.setdefault(item["x"], []).append(item)
        for column in by_column.values():
            ordered = sorted(column, key=lambda item: item["y"])
            for previous, current in zip(ordered, ordered[1:]):
                self.assertGreaterEqual(current["y"], previous["y"] + previous["height"] + 40)

        reordered = copy.deepcopy(graph)
        reordered["nodes"].reverse()
        reordered["clusters"].reverse()
        reordered["edges"].reverse()
        reordered_document = json.loads(exports.render_canvas(reordered))
        # The visible Canvas projection is order-independent. The source_graph
        # extension intentionally retains the source document's original order.
        self.assertEqual(document["nodes"], reordered_document["nodes"])
        self.assertEqual(document["edges"], reordered_document["edges"])

    def test_extended_fields_are_readable_and_lossless(self) -> None:
        graph = extended_graph()
        rendered = exports.render_canvas(graph)
        document = json.loads(rendered)
        self.assertEqual(document[exports.CANVAS_EXTENSION_KEY]["source_graph"], graph)
        alpha_id = exports.stable_id("canvas-concept", "alpha")
        alpha = next(item for item in document["nodes"] if item["id"] == alpha_id)
        self.assertIn("## Spatial context", alpha["text"])
        self.assertIn("## Claims", alpha["text"])
        self.assertIn("## Evidence", alpha["text"])
        self.assertIn("Berlin", alpha["text"])
        self.assertEqual(alpha[exports.CANVAS_EXTENSION_KEY]["raw"], graph["nodes"][0])

    def test_markdown_payload_is_escaped_in_visible_node(self) -> None:
        graph = load_fixture()
        graph["nodes"][0]["definition"] = "<script>x</script> ![[embed]] **bold**"
        document = json.loads(exports.render_canvas(graph))
        alpha_id = exports.stable_id("canvas-concept", "alpha")
        text = next(item["text"] for item in document["nodes"] if item["id"] == alpha_id)
        self.assertNotIn("<script>", text)
        self.assertNotIn("![[embed]]", text)
        self.assertIn("&lt;script&gt;", text)


class FileInterfaceCase(unittest.TestCase):
    def test_loader_rejects_duplicate_keys_nonfinite_numbers_and_surrogates(self) -> None:
        invalid_payloads = (
            ('{"metadata":{},"metadata":{}}', "duplicate object key"),
            ('{"value":NaN}', "non-finite number"),
            ('{"value":"\\ud800"}', "isolated Unicode surrogate"),
        )
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "invalid.knowledge.json"
            for payload, expected in invalid_payloads:
                with self.subTest(payload=payload):
                    source.write_text(payload, encoding="utf-8")
                    with self.assertRaisesRegex(exports.ExportError, expected):
                        exports.load_graph(source)

    def test_files_are_repeatable_and_selectable(self) -> None:
        graph = extended_graph()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = exports.export_files(graph, root, "demo")
            before = {kind: path.read_bytes() for kind, path in first.items()}
            second = exports.export_files(graph, root, "demo")
            self.assertEqual(before, {kind: path.read_bytes() for kind, path in second.items()})
            self.assertEqual(set(first), {"cypher", "ctxt", "canvas"})

            only = exports.export_files(graph, root, "single", ("canvas",))
            self.assertEqual(set(only), {"canvas"})
            self.assertFalse((root / "single.cypher").exists())

    def test_ambiguous_duplicates_and_unsafe_basename_are_rejected(self) -> None:
        graph = load_fixture()
        graph["nodes"].append(copy.deepcopy(graph["nodes"][0]))
        with self.assertRaisesRegex(exports.ExportError, "duplicate concept id"):
            exports.render_canvas(graph)
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(exports.ExportError, "basename"):
                exports.export_files(load_fixture(), temporary, "../escape")

    def test_cli_and_preflight_never_overwrite_the_canonical_input(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            victim = root / "victim.cypher"
            original = FIXTURE.read_bytes()
            victim.write_bytes(original)
            with contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as raised:
                    exports.main(
                        [str(victim), "--format", "cypher", "--basename", "victim"]
                    )
            self.assertEqual(raised.exception.code, 2)
            self.assertEqual(victim.read_bytes(), original)

            canonical = root / "graph.knowledge.json"
            canonical.write_bytes(original)
            hardlink = root / "protected.cypher"
            os.link(canonical, hardlink)
            with self.assertRaisesRegex(exports.ExportError, "different from the canonical input"):
                exports.export_files(
                    load_fixture(),
                    root,
                    "protected",
                    ("cypher",),
                    protected_paths=(canonical,),
                )
            self.assertEqual(canonical.read_bytes(), original)


if __name__ == "__main__":
    unittest.main(verbosity=2)
