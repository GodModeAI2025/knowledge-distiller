"""Golden tests for the Knowledge Distiller validator and graph builder.

Runs with either ``python3 -m pytest tests/`` or ``python3 -m unittest discover tests``.
Mirrors OKF's approach: assert *specific* validator error strings so the contract is
regression-locked, not vibe-checked.
"""
import contextlib
import copy
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import build_graph as bg  # noqa: E402
import build_md  # noqa: E402
import build_bundle  # noqa: E402
import validate_knowledge as vk  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "minimal.knowledge.json"
EXAMPLE = ROOT / "examples" / "claude-skills-guide.knowledge.json"


def load(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def run(doc, prev=None):
    """Validate a doc dict the way the CLI does (schema if available + structural)."""
    rep = vk.Report()
    ran = vk.schema_validate(doc, rep)
    sub = vk.validate(doc, prev=prev, ran_schema=ran)
    rep.errors.extend(sub.errors)
    rep.warnings.extend(sub.warnings)
    return rep


def err_text(rep):
    return "\n".join(rep.errors)


class TestValidFixture(unittest.TestCase):
    def test_minimal_is_clean(self):
        rep = run(load(FIXTURE))
        self.assertEqual(rep.errors, [], err_text(rep))
        self.assertEqual(rep.warnings, [])
        self.assertEqual(rep.quality_score(), 100)

    def test_shipped_example_is_clean(self):
        # Regression guard: the example we ship must always conform.
        rep = run(load(EXAMPLE))
        self.assertEqual(rep.errors, [], err_text(rep))
        self.assertEqual(rep.quality_score(), 100)


class TestStructuralErrors(unittest.TestCase):
    def setUp(self):
        self.base = load(FIXTURE)

    def test_dangling_edge_target(self):
        d = copy.deepcopy(self.base)
        d["edges"][0]["target"] = "does-not-exist"
        rep = run(d)
        self.assertTrue(any("does not resolve to a node" in e for e in rep.errors), err_text(rep))

    def test_unknown_cluster(self):
        d = copy.deepcopy(self.base)
        d["nodes"][0]["cluster"] = "ghost-cluster"
        rep = run(d)
        self.assertTrue(any("does not resolve to a declared cluster" in e for e in rep.errors), err_text(rep))

    def test_bad_edge_type(self):
        d = copy.deepcopy(self.base)
        d["edges"][0]["type"] = "frobnicates"
        rep = run(d)
        self.assertTrue(any("8-type vocabulary" in e or "enum" in e.lower() for e in rep.errors), err_text(rep))

    def test_unresolved_node_source(self):
        d = copy.deepcopy(self.base)
        d["nodes"][0]["sources"] = ["s999"]
        rep = run(d)
        self.assertTrue(any("does not resolve to metadata.sources" in e for e in rep.errors), err_text(rep))

    def test_unresolved_fact_source(self):
        d = copy.deepcopy(self.base)
        d["facts"][0]["source"] = "s999"
        rep = run(d)
        self.assertTrue(any("does not resolve to metadata.sources" in e for e in rep.errors), err_text(rep))

    def test_citation_marker_out_of_range(self):
        d = copy.deepcopy(self.base)
        d["nodes"][0]["statements"][0] = "Alpha cites a missing source. [7]"
        rep = run(d)
        self.assertTrue(any("citation marker [7] out of range" in e for e in rep.errors), err_text(rep))

    def test_absurdly_long_citation_marker_is_reported_not_raised(self):
        # A digit run this long is not a number that is merely out of range:
        # converting it raises before the validator can judge it.
        d = copy.deepcopy(self.base)
        d["nodes"][0]["statements"][0] = "Alpha cites nothing. [" + "1" * 5000 + "]"
        rep = run(d)
        self.assertTrue(
            any("citation marker has more than 9 digits" in e for e in rep.errors),
            err_text(rep),
        )

    def test_count_mismatch(self):
        d = copy.deepcopy(self.base)
        d["metadata"]["concept_count"] = 99
        rep = run(d)
        self.assertTrue(any("metadata.concept_count: declared 99" in e for e in rep.errors), err_text(rep))

    def test_chunk_with_markdown_is_rejected(self):
        d = copy.deepcopy(self.base)
        d["chunks"][0]["text"] = "This chunk has a [[wikilink]] which is forbidden."
        rep = run(d)
        self.assertTrue(any("artifact" in e for e in rep.errors), err_text(rep))

    def test_chunk_with_arrow_is_rejected(self):
        d = copy.deepcopy(self.base)
        d["chunks"][0]["text"] = "A leads to B via A -> B notation."
        rep = run(d)
        self.assertTrue(any("artifact" in e for e in rep.errors), err_text(rep))


class TestWarnings(unittest.TestCase):
    def setUp(self):
        self.base = load(FIXTURE)

    def test_node_without_citations_warns(self):
        d = copy.deepcopy(self.base)
        d["nodes"][0].pop("citations", None)
        d["nodes"][0]["statements"] = ["A statement with no citation marker."]
        rep = run(d)
        self.assertIn("stored 100 but diagnostics require 98", err_text(rep))

        # Refresh derived scores before publishing the warned-but-otherwise-valid graph.
        bg.recompute(d)
        rep = run(d)
        self.assertEqual(rep.errors, [], err_text(rep))
        self.assertEqual(d["metadata"]["quality_score"], 98)
        self.assertTrue(any("has no citations" in w for w in rep.warnings))

    def test_unknown_spec_version_warns(self):
        d = copy.deepcopy(self.base)
        d["metadata"]["distiller_spec_version"] = "9.9"
        rep = run(d)
        self.assertTrue(any("unknown version" in w for w in rep.warnings))


class TestMonotonicity(unittest.TestCase):
    def setUp(self):
        self.base = load(FIXTURE)

    def test_merge_growth_is_ok(self):
        prev = copy.deepcopy(self.base)
        new = copy.deepcopy(self.base)
        new["nodes"].append({
            "id": "delta", "label": "Delta", "cluster": "c2", "confidence": "high",
            "definition": "A new concept.", "relevance": "Added by a merge.", "resource": None,
            "statements": ["Delta extends Gamma. [1]"],
            "temporal": {"source_date": "2026-02", "source_period": None, "valid_from": "2026-02",
                         "valid_until": None, "temporal_confidence": "explicit"},
            "sources": ["s1"], "citations": [{"n": 1, "source": "s1"}],
        })
        bg.recompute(new)  # fix rollups so counts pass
        rep = run(new, prev=prev)
        self.assertEqual([e for e in rep.errors if "merge" in e], [], err_text(rep))

    def test_merge_shrink_fails(self):
        prev = copy.deepcopy(self.base)
        new = copy.deepcopy(self.base)
        new["nodes"] = new["nodes"][:-1]
        new["edges"] = [e for e in new["edges"]
                        if e["source"] != "gamma" and e["target"] != "gamma"]
        bg.recompute(new)
        rep = run(new, prev=prev)
        self.assertTrue(any("shrank" in e or "dropped node" in e for e in rep.errors), err_text(rep))


class TestBuildGraph(unittest.TestCase):
    def test_recompute_fixes_counts_and_membership(self):
        d = load(FIXTURE)
        d["metadata"]["concept_count"] = 0
        d["clusters"][0]["concepts"] = []
        bg.recompute(d)
        self.assertEqual(d["metadata"]["concept_count"], 3)
        self.assertEqual(d["clusters"][0]["concepts"], ["alpha", "beta"])
        self.assertEqual(d["metadata"]["quality_score"], 100)

    def test_write_rejects_symlinks_and_atomically_breaks_hardlinks(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            document = load(FIXTURE)
            document["metadata"]["concept_count"] = 0
            original = (json.dumps(document, ensure_ascii=False, indent=2) + "\n").encode()
            source = root / "graph.knowledge.json"
            source.write_bytes(original)

            symlink = root / "linked.knowledge.json"
            symlink.symlink_to(source)
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(bg.main([str(symlink), "--write"]), 1)
            self.assertEqual(source.read_bytes(), original)

            hardlink = root / "hardlink.knowledge.json"
            os.link(source, hardlink)
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(bg.main([str(source), "--write"]), 0)
            self.assertNotEqual(source.read_bytes(), original)
            self.assertEqual(hardlink.read_bytes(), original)

    def test_failed_atomic_replace_preserves_canonical_bytes(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "graph.knowledge.json"
            original = FIXTURE.read_bytes()
            source.write_bytes(original)
            with mock.patch.object(bg.os, "replace", side_effect=OSError("simulated")):
                with contextlib.redirect_stderr(io.StringIO()):
                    self.assertEqual(bg.main([str(source), "--write"]), 1)
            self.assertEqual(source.read_bytes(), original)
            self.assertEqual(list(Path(temp).glob(".kd-graph-*.tmp")), [])

    def test_canonical_tension_edge_id_is_sorted(self):
        # tension is symmetric: id must be order-independent
        e1 = bg.canonical_edge_id({"source": "z", "target": "a", "type": "tension"})
        e2 = bg.canonical_edge_id({"source": "a", "target": "z", "type": "tension"})
        self.assertEqual(e1, e2)
        self.assertEqual(e1, "a__tension__z")

    def test_directed_edge_id_keeps_direction(self):
        e = bg.canonical_edge_id({"source": "a", "target": "b", "type": "uses"})
        self.assertEqual(e, "a__uses__b")


class TestRenderersSmoke(unittest.TestCase):
    def test_build_md_produces_sections(self):
        md = build_md.render(load(FIXTURE))
        for section in ("## Concept Map", "## Kernwissen", "## Wissensgraph (Mermaid)",
                        "## Quellen", 'distiller_spec_version: "1.0"'):
            self.assertIn(section, md)

    def test_chunks_in_md_are_clean(self):
        # the rendered chunk section must still pass the chunk cleanliness rule
        md = build_md.render(load(FIXTURE))
        self.assertIn("As of early 2026", md)

    def test_build_bundle_concept_has_citations(self):
        doc = load(FIXTURE)
        bg.recompute(doc)
        nodes_by_id = {n["id"]: n for n in doc["nodes"]}
        src_idx = build_bundle._sources_index(doc["metadata"])
        leaf = build_bundle.concept_md(nodes_by_id["alpha"], doc["edges"], nodes_by_id, src_idx)
        self.assertIn("# Citations", leaf)
        self.assertIn("type: Concept", leaf)


if __name__ == "__main__":
    unittest.main(verbosity=2)
