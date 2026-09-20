"""Focused contract, conflict and file-safety tests for deterministic graph merging."""
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

import build_graph as bg  # noqa: E402
import merge_knowledge as mk  # noqa: E402
import validate_knowledge as vk  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "minimal.knowledge.json"
CONTEXTUAL_FIXTURE = ROOT / "tests" / "fixtures" / "contextual.knowledge.json"


def fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def conform(doc: dict) -> dict:
    bg.recompute(doc)
    report = vk.Report()
    schema = vk.schema_validate(doc, report)
    core = vk.validate(doc, ran_schema=schema)
    report.errors.extend(core.errors)
    if report.errors:
        raise AssertionError("invalid test graph:\n" + "\n".join(report.errors))
    return doc


def incoming_with_delta() -> dict:
    doc = fixture()
    s1 = copy.deepcopy(doc["metadata"]["sources"][0])
    s2 = {"id": "s2", "file": "delta.txt", "type": "txt", "date": "2026-02", "url": None}
    doc["metadata"]["sources"] = [s2, s1]
    for node in doc["nodes"]:
        node["statements"] = [value.replace("[1]", "[2]") for value in node["statements"]]
        for citation in node.get("citations", []):
            citation["n"] = 2
    doc["nodes"].append({
        "id": "delta",
        "label": "Delta",
        "cluster": "c2",
        "confidence": "high",
        "definition": "A newly added concept.",
        "relevance": "It proves additive merging.",
        "resource": "urn:example:delta",
        "statements": ["Delta extends Gamma. [1]"],
        "temporal": {
            "source_date": "2026-02", "source_period": None, "valid_from": "2026-02",
            "valid_until": None, "temporal_confidence": "explicit",
        },
        "sources": ["s2"],
        "citations": [{"n": 1, "source": "s2", "locator": "line 1"}],
    })
    doc["edges"].append({
        "source": "delta", "target": "gamma", "type": "extends", "weight": 0.8,
        "confidence": "high", "label": "Delta extends Gamma",
    })
    doc["chunks"].append({
        "id": "ch-delta", "text": "As of February 2026, Delta extends Gamma.",
        "concepts": ["delta"], "temporal_scope": "2026-02", "token_estimate": 8,
    })
    return conform(doc)


def add_evidence(doc: dict, evidence_id: str, page: int = 1) -> dict:
    doc["metadata"]["distiller_spec_version"] = "1.1"
    doc.setdefault("evidence", []).append({
        "id": evidence_id,
        "source": "s1",
        "selector": {"type": "PageSelector", "page": page},
        "support": "supports",
        "attribution_basis": "source_explicit",
        "review_status": "unreviewed",
    })
    return doc


def empty_tracker() -> dict:
    return {
        "added": {}, "enriched": {}, "node_aliases": {}, "resource_enrichments": {},
        "fact_aliases": {}, "recorded_fact_conflicts": [],
    }


def fact_population(count: int, prefix: str, *, shared_key: bool = True) -> list:
    """``count`` facts; with ``shared_key`` false every fact has its own identity."""
    return [
        {
            "id": f"{prefix}-{index}", "statement": f"Fact {index}. [1]", "value": str(index),
            "confidence": "high", "source": "s1",
            "concept": "alpha" if shared_key else f"concept-{index}", "metric": "count",
        }
        for index in range(count)
    ]


def nested_extension(depth: int, leaf: str = "leaf") -> dict:
    root: dict = {}
    cursor = root
    for _ in range(depth - 1):
        cursor["extension"] = {}
        cursor = cursor["extension"]
    cursor["extension"] = leaf
    return root


class MergeContractCase(unittest.TestCase):
    def test_additive_union_preserves_base_order_and_remaps_citations(self) -> None:
        base = fixture()
        incoming = incoming_with_delta()
        base_before = copy.deepcopy(base)
        incoming_before = copy.deepcopy(incoming)

        merged, diff = mk.merge_documents(base, incoming)

        self.assertEqual([source["id"] for source in merged["metadata"]["sources"]], ["s1", "s2"])
        self.assertEqual([node["id"] for node in merged["nodes"]], ["alpha", "beta", "gamma", "delta"])
        delta = next(node for node in merged["nodes"] if node["id"] == "delta")
        self.assertEqual(delta["statements"], ["Delta extends Gamma. [2]"])
        self.assertEqual(delta["citations"][0]["n"], 2)
        self.assertEqual(merged["nodes"][0]["statements"], base["nodes"][0]["statements"])
        self.assertEqual(diff["added"]["nodes"], ["delta"])
        self.assertEqual(diff["counts"]["output"]["nodes"], 4)
        self.assertFalse(diff["network_access"])
        self.assertEqual(base, base_before)
        self.assertEqual(incoming, incoming_before)

    def test_absurdly_long_citation_marker_fails_the_merge_instead_of_raising(self) -> None:
        base = fixture()
        incoming = incoming_with_delta()
        delta = next(node for node in incoming["nodes"] if node["id"] == "delta")
        delta["statements"] = ["Delta extends Gamma. [" + "1" * 5000 + "]"]
        with self.assertRaisesRegex(mk.MergeValidationError, "more than 9 digits"):
            mk.merge_documents(base, incoming)

    def test_resource_identity_unifies_node_and_remaps_all_known_references(self) -> None:
        base = fixture()
        base["nodes"][0]["resource"] = "urn:example:alpha"
        base = conform(base)
        incoming = fixture()
        incoming["metadata"]["distiller_spec_version"] = "1.1"
        alpha = incoming["nodes"][0]
        alpha["id"] = "alpha-copy"
        alpha["resource"] = "urn:example:alpha"
        alpha["claim_ids"] = ["claim-alpha"]
        alpha["evidence"] = ["ev-alpha"]
        for edge in incoming["edges"]:
            if edge["source"] == "alpha":
                edge["source"] = "alpha-copy"
            if edge["target"] == "alpha":
                edge["target"] = "alpha-copy"
        for chunk in incoming["chunks"]:
            chunk["concepts"] = ["alpha-copy" if value == "alpha" else value for value in chunk["concepts"]]
        add_evidence(incoming, "ev-alpha")
        incoming["claims"] = [{
            "id": "claim-alpha", "node": "alpha-copy",
            "statement": alpha["statements"][0], "confidence": "high",
            "origin": "source_stated", "evidence": ["ev-alpha"],
            "review_status": "unreviewed",
        }]
        incoming = conform(incoming)

        merged, diff = mk.merge_documents(base, incoming)

        self.assertEqual([node["id"] for node in merged["nodes"]], ["alpha", "beta", "gamma"])
        self.assertEqual(diff["aliases"]["nodes"], {"alpha-copy": "alpha"})
        self.assertEqual(merged["claims"][0]["node"], "alpha")
        self.assertIn("alpha", merged["chunks"][0]["concepts"])
        self.assertTrue(any(edge["source"] == "alpha" for edge in merged["edges"]))
        self.assertIn("claim-alpha", merged["nodes"][0]["claim_ids"])

    def test_resource_can_enrich_same_id_without_false_monotonic_drop(self) -> None:
        incoming = fixture()
        incoming["nodes"][0]["resource"] = "urn:example:alpha"
        incoming = conform(incoming)

        merged, diff = mk.merge_documents(fixture(), incoming)

        self.assertEqual(merged["nodes"][0]["resource"], "urn:example:alpha")
        self.assertEqual(
            diff["aliases"]["resource_enrichments"], {"alpha": "urn:example:alpha"}
        )
        self.assertTrue(diff["validation"]["merged"]["ok"])

    def test_conflicting_source_payload_fails_without_mutation(self) -> None:
        base = fixture()
        incoming = fixture()
        incoming["metadata"]["sources"][0]["file"] = "different.txt"
        incoming = conform(incoming)
        before = copy.deepcopy(base)

        with self.assertRaisesRegex(mk.PayloadConflictError, r"source\[s1\].file"):
            mk.merge_documents(base, incoming)
        self.assertEqual(base, before)

    def test_conflicting_node_payload_fails_closed(self) -> None:
        incoming = fixture()
        incoming["nodes"][0]["definition"] = "A contradictory replacement definition."
        incoming = conform(incoming)

        with self.assertRaisesRegex(mk.PayloadConflictError, r"node\[alpha\].definition"):
            mk.merge_documents(fixture(), incoming)

    def test_conflicting_edge_payload_fails_closed(self) -> None:
        incoming = fixture()
        incoming["edges"][0]["label"] = "A contradictory relationship label"
        incoming = conform(incoming)

        with self.assertRaisesRegex(mk.PayloadConflictError, r"edge\[alpha\|uses\|beta\].label"):
            mk.merge_documents(fixture(), incoming)

    def test_same_id_fact_conflict_errors_by_default(self) -> None:
        incoming = fixture()
        incoming["facts"][0]["value"] = "4"
        incoming = conform(incoming)

        with self.assertRaisesRegex(mk.PayloadConflictError, r"fact\[f1\].value"):
            mk.merge_documents(fixture(), incoming)

    def test_same_id_fact_conflict_can_be_retained_explicitly(self) -> None:
        incoming = fixture()
        incoming["facts"][0]["value"] = "4"
        incoming = conform(incoming)

        merged, diff = mk.merge_documents(fixture(), incoming, fact_conflicts="record")

        self.assertEqual(len(merged["facts"]), 2)
        self.assertEqual({fact["value"] for fact in merged["facts"]}, {"3", "4"})
        retained = next(fact for fact in merged["facts"] if fact["value"] == "4")
        self.assertTrue(retained["id"].startswith("f1--conflict-"))
        self.assertEqual(len(merged["fact_conflicts"]), 1)
        self.assertEqual(set(merged["fact_conflicts"][0]["facts"]), {"f1", retained["id"]})
        self.assertEqual(diff["aliases"]["facts"], {"f1": retained["id"]})
        self.assertEqual(diff["spec_version"], "1.1")

        repeated, _ = mk.merge_documents(merged, incoming, fact_conflicts="record")
        self.assertEqual(repeated, merged)

    def test_distinct_ids_with_same_metric_and_period_create_explicit_conflict(self) -> None:
        base = fixture()
        base["facts"][0].update({"concept": "alpha", "metric": "count"})
        base = conform(base)
        incoming = copy.deepcopy(base)
        incoming["facts"][0]["id"] = "f2"
        incoming["facts"][0]["value"] = "4"
        incoming = conform(incoming)

        merged, _ = mk.merge_documents(base, incoming)

        self.assertEqual([fact["id"] for fact in merged["facts"]], ["f1", "f2"])
        self.assertEqual(len(merged["fact_conflicts"]), 1)
        self.assertEqual(set(merged["fact_conflicts"][0]["facts"]), {"f1", "f2"})

    def test_evidence_and_assessment_lists_are_unioned_but_scalar_conflicts_fail(self) -> None:
        base = add_evidence(fixture(), "ev1", 1)
        add_evidence(base, "ev2", 2)
        base["assessments"] = [{
            "id": "a1", "dimension": "fitness", "scope": "node:alpha", "value": "fit",
            "assessor": "human:reviewer", "method": "review", "assessed_at": "2026-06",
            "evidence": ["ev1"],
        }]
        base = conform(base)
        incoming = copy.deepcopy(base)
        incoming["assessments"][0]["evidence"] = ["ev2"]
        incoming = conform(incoming)

        merged, _ = mk.merge_documents(base, incoming)
        self.assertEqual(merged["assessments"][0]["evidence"], ["ev1", "ev2"])

        incoming["evidence"][0]["support"] = "contradicts"
        incoming = conform(incoming)
        with self.assertRaisesRegex(mk.PayloadConflictError, r"evidence\[ev1\].support"):
            mk.merge_documents(base, incoming)

    def test_claim_and_explicit_fact_conflict_payloads_are_not_overwritten(self) -> None:
        base = add_evidence(fixture(), "ev1")
        statement = base["nodes"][0]["statements"][0]
        base["nodes"][0]["claim_ids"] = ["claim-1"]
        base["claims"] = [{
            "id": "claim-1", "node": "alpha", "statement": statement,
            "confidence": "high", "origin": "source_stated", "evidence": ["ev1"],
        }]
        second = copy.deepcopy(base["facts"][0])
        second.update({"id": "f2", "value": "4"})
        base["facts"].append(second)
        base["fact_conflicts"] = [{
            "id": "fc1", "facts": ["f1", "f2"], "relation": "tension",
            "reason": "Values differ in the source set.", "evidence": ["ev1"],
        }]
        base = conform(base)

        incoming_claim = copy.deepcopy(base)
        alternative = "Alpha has a different first-class claim. [1]"
        incoming_claim["nodes"][0]["statements"].append(alternative)
        incoming_claim["claims"][0]["statement"] = alternative
        incoming_claim = conform(incoming_claim)
        with self.assertRaisesRegex(mk.PayloadConflictError, r"claims\[claim-1\].statement"):
            mk.merge_documents(base, incoming_claim)

        incoming_conflict = copy.deepcopy(base)
        incoming_conflict["fact_conflicts"][0]["reason"] = "A different asserted reason."
        incoming_conflict = conform(incoming_conflict)
        with self.assertRaisesRegex(mk.PayloadConflictError, r"fact_conflicts\[fc1\].reason"):
            mk.merge_documents(base, incoming_conflict)

    def test_merge_is_deterministic_and_idempotent_for_same_inputs(self) -> None:
        base = fixture()
        incoming = incoming_with_delta()
        first_graph, first_diff = mk.merge_documents(base, incoming)
        second_graph, second_diff = mk.merge_documents(base, incoming)
        self.assertEqual(first_graph, second_graph)
        self.assertEqual(first_diff, second_diff)

        repeated, _ = mk.merge_documents(first_graph, incoming)
        self.assertEqual(first_graph, repeated)

    def test_fact_merge_work_grows_with_the_population_not_with_its_square(self) -> None:
        """The pairwise scan made an N-fact merge cost N**2 logical-key computations."""
        def key_computations(count: int) -> int:
            original = mk._logical_fact_key
            counter = [0]

            def counting(fact: dict):
                counter[0] += 1
                return original(fact)

            mk._logical_fact_key = counting
            try:
                mk._merge_facts(
                    fact_population(count, "base", shared_key=False),
                    fact_population(count, "inc", shared_key=False),
                    empty_tracker(), conflict_mode="error",
                )
            finally:
                mk._logical_fact_key = original
            return counter[0]

        small = key_computations(100)
        large = key_computations(400)
        self.assertGreater(small, 0)
        # Quadratic would be a factor of about sixteen for four times the input.
        self.assertLessEqual(
            large, small * 6,
            f"four times the facts cost {large / small:.1f} times the work",
        )

    def test_indexed_fact_conflicts_keep_their_observable_order(self) -> None:
        base = fixture()
        base["facts"] = [
            dict(fact_population(1, "a")[0], id="f1", value="1"),
            dict(fact_population(1, "a")[0], id="f2", value="2"),
            dict(fact_population(1, "a")[0], id="f3", value="3"),
        ]
        base = conform(base)
        incoming = fixture()
        incoming["facts"] = [
            dict(fact_population(1, "a")[0], id="f4", value="4"),
            dict(fact_population(1, "a")[0], id="f5", value="5"),
        ]
        incoming = conform(incoming)

        merged, _ = mk.merge_documents(base, incoming)

        self.assertEqual(
            [conflict["facts"] for conflict in merged["fact_conflicts"]],
            [["f1", "f4"], ["f2", "f4"], ["f3", "f4"],
             ["f1", "f5"], ["f2", "f5"], ["f3", "f5"], ["f4", "f5"]],
        )

    def test_enrichment_that_changes_a_logical_key_is_seen_by_later_facts(self) -> None:
        """A merged fact is compared under its merged identity, not its prior one."""
        period = {"source_date": "2026-03", "source_period": None, "valid_from": "2026-03",
                  "valid_until": None, "temporal_confidence": "explicit"}
        base = fixture()
        base["facts"] = [dict(fact_population(1, "a")[0], id="f1", value="1")]
        base = conform(base)
        incoming = fixture()
        incoming["facts"] = [
            dict(fact_population(1, "a")[0], id="f1", value="1", temporal=period),
            dict(fact_population(1, "a")[0], id="f2", value="2", temporal=period),
        ]
        incoming = conform(incoming)

        merged, _ = mk.merge_documents(base, incoming)

        self.assertEqual(merged["facts"][0]["temporal"], period)
        self.assertEqual(
            [conflict["facts"] for conflict in merged["fact_conflicts"]], [["f1", "f2"]]
        )

    def test_an_unhashable_fact_identity_is_still_compared_by_equality(self) -> None:
        """The index must not turn a malformed concept/metric into a TypeError."""
        def fact(fid: str, concept, value: str) -> dict:
            return {"id": fid, "statement": "s", "value": value, "confidence": "high",
                    "source": "s1", "concept": concept, "metric": "count"}

        result, aliases, generated = mk._merge_facts(
            [fact("f1", ["a"], "1"), fact("f2", {"k": 1}, "1")],
            [fact("f3", ["a"], "2"), fact("f4", {"k": 1}, "2"), fact("f5", ["b"], "2")],
            empty_tracker(), conflict_mode="error",
        )

        self.assertEqual([item["id"] for item in result], ["f1", "f2", "f3", "f4", "f5"])
        self.assertEqual(
            [conflict["facts"] for conflict in generated], [["f1", "f3"], ["f2", "f4"]]
        )
        self.assertEqual(set(aliases), {"f3", "f4", "f5"})

    def test_numerically_equal_fact_identities_still_match(self) -> None:
        """``1``, ``True`` and ``1.0`` compared equal before and must keep doing so."""
        def fact(fid: str, concept, value: str) -> dict:
            return {"id": fid, "statement": "s", "value": value, "confidence": "high",
                    "source": "s1", "concept": concept, "metric": "count"}

        _, _, generated = mk._merge_facts(
            [fact("f1", 1, "1")],
            [fact("f2", True, "2"), fact("f3", 1.0, "3")],
            empty_tracker(), conflict_mode="error",
        )

        self.assertEqual(
            [conflict["facts"] for conflict in generated],
            [["f1", "f2"], ["f1", "f3"], ["f2", "f3"]],
        )

    def test_graph_nested_past_the_depth_limit_is_refused_with_a_message(self) -> None:
        incoming = fixture()
        incoming["extension"] = nested_extension(mk.MAX_STRUCTURE_DEPTH + 1)
        with self.assertRaisesRegex(mk.MergeValidationError, "nesting exceeds the safe depth"):
            mk.merge_documents(fixture(), incoming)
        with self.assertRaisesRegex(mk.MergeValidationError, "base graph"):
            mk.merge_documents(incoming, fixture())

    def test_a_graph_at_the_depth_limit_is_not_refused(self) -> None:
        """The bound must not be so tight that a document sitting on it is rejected."""
        doc = fixture()
        doc["extension"] = nested_extension(mk.MAX_STRUCTURE_DEPTH - 1)
        self.assertFalse(mk._exceeds_structure_depth(doc))
        self.assertEqual(
            mk._merge_value(doc["extension"], copy.deepcopy(doc["extension"]), "$"),
            doc["extension"],
            "the recursive merge must still complete at the limit",
        )
        doc["extension"] = nested_extension(mk.MAX_STRUCTURE_DEPTH)
        self.assertTrue(mk._exceeds_structure_depth(doc))

    def test_merge_value_refuses_to_recurse_past_the_depth_limit(self) -> None:
        left = nested_extension(mk.MAX_STRUCTURE_DEPTH + 2, "left")
        right = nested_extension(mk.MAX_STRUCTURE_DEPTH + 2, "right")
        with self.assertRaisesRegex(mk.MergeValidationError, "nesting exceeds the safe depth"):
            mk._merge_value(left, right, "$")

    def test_depth_probe_terminates_on_a_self_referential_structure(self) -> None:
        cycle: dict = {}
        cycle["self"] = cycle
        self.assertTrue(mk._exceeds_structure_depth(cycle))
        self.assertFalse(mk._exceeds_structure_depth(fixture()))

    def test_invalid_input_is_rejected_before_merge(self) -> None:
        incoming = fixture()
        incoming["nodes"][0]["sources"] = ["missing"]
        with self.assertRaisesRegex(mk.MergeValidationError, "incoming graph failed validation"):
            mk.merge_documents(fixture(), incoming)


class MergeCliCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.base_path = self.root / "base.knowledge.json"
        self.incoming_path = self.root / "incoming.knowledge.json"
        self.base_raw = (json.dumps(fixture(), ensure_ascii=False, indent=2) + "\n").encode()
        self.base_path.write_bytes(self.base_raw)
        self.incoming_path.write_text(
            json.dumps(incoming_with_delta(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_cli_always_writes_both_deltas_and_exact_version_archive(self) -> None:
        output = self.root / "merged.knowledge.json"
        report = self.root / "merged.knowledge.diff.json"
        markdown_report = self.root / "merged.knowledge.diff.md"
        code = mk.main([str(self.base_path), str(self.incoming_path), "-o", str(output)])

        self.assertEqual(code, 0)
        merged = json.loads(output.read_text(encoding="utf-8"))
        diff = json.loads(report.read_text(encoding="utf-8"))
        self.assertEqual(diff["hashes"]["output_canonical_sha256"], mk._sha_document(merged))
        markdown = markdown_report.read_text(encoding="utf-8")
        self.assertIn(diff["hashes"]["output_canonical_sha256"], markdown)
        self.assertIn("## Canonical machine delta", markdown)
        self.assertEqual(markdown, mk.render_markdown_diff(diff))
        archives = list((self.root / "versions").glob("*.json"))
        self.assertEqual(len(archives), 1)
        self.assertEqual(archives[0].read_bytes(), self.base_raw)
        self.assertEqual(list(self.root.rglob(".merge-*.tmp")), [])
        self.assertEqual(list(self.root.rglob(".archive-*.tmp")), [])

    def test_cli_self_merge_with_explicit_conflict_is_idempotent_and_audited(self) -> None:
        contextual_raw = CONTEXTUAL_FIXTURE.read_bytes()
        contextual = json.loads(contextual_raw)
        self.base_path.write_bytes(contextual_raw)
        output = self.root / "contextual-self.knowledge.json"

        code = mk.main([str(self.base_path), str(self.base_path), "-o", str(output)])

        self.assertEqual(code, 0)
        merged = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(
            merged["fact_conflicts"][0]["facts"],
            contextual["fact_conflicts"][0]["facts"],
        )
        repeated, _ = mk.merge_documents(merged, contextual)
        self.assertEqual(repeated, merged)

        json_delta, markdown_delta = mk._default_audit_paths(output)
        self.assertTrue(json_delta.is_file())
        self.assertTrue(markdown_delta.is_file())
        archives = list((self.root / "versions").glob("*.json"))
        self.assertEqual(len(archives), 1)
        self.assertEqual(archives[0].read_bytes(), contextual_raw)

    def test_cli_refuses_existing_output_without_force(self) -> None:
        output = self.root / "merged.knowledge.json"
        output.write_text("do not overwrite", encoding="utf-8")
        code = mk.main([str(self.base_path), str(self.incoming_path), "-o", str(output)])
        self.assertEqual(code, 1)
        self.assertEqual(output.read_text(encoding="utf-8"), "do not overwrite")

    def test_in_place_requires_force_and_archives_automatically(self) -> None:
        before = self.base_path.read_bytes()
        code = mk.main([
            str(self.base_path), str(self.incoming_path), "-o", str(self.base_path),
        ])
        self.assertEqual(code, 1)
        self.assertEqual(self.base_path.read_bytes(), before)

        code = mk.main([
            str(self.base_path), str(self.incoming_path), "-o", str(self.base_path),
            "--force",
        ])
        self.assertEqual(code, 0)
        self.assertEqual(next((self.root / "versions").glob("*.json")).read_bytes(), before)
        self.assertTrue((self.root / "base.knowledge.diff.json").is_file())
        self.assertTrue((self.root / "base.knowledge.diff.md").is_file())

    def test_custom_delta_paths_are_still_both_required(self) -> None:
        output = self.root / "merged.knowledge.json"
        json_delta = self.root / "audit" / "delta.json"
        markdown_delta = self.root / "audit" / "delta.md"
        code = mk.main([
            str(self.base_path), str(self.incoming_path), "-o", str(output),
            "--diff-report", str(json_delta), "--markdown-diff", str(markdown_delta),
        ])
        self.assertEqual(code, 0)
        self.assertTrue(json_delta.is_file())
        self.assertTrue(markdown_delta.is_file())

    def test_cli_rejects_ambiguous_json_before_creating_audit_artifacts(self) -> None:
        output = self.root / "merged.knowledge.json"
        invalid_payloads = (
            '{"metadata":{},"metadata":{}}',
            '{"value":Infinity}',
            '{"value":"\\ud800"}',
        )
        for payload in invalid_payloads:
            self.incoming_path.write_text(payload, encoding="utf-8")
            code = mk.main([str(self.base_path), str(self.incoming_path), "-o", str(output)])
            self.assertEqual(code, 1)
            self.assertFalse(output.exists())
            self.assertFalse((self.root / "versions").exists())

        self.base_path.write_text('{"metadata":{},"metadata":{}}', encoding="utf-8")
        self.incoming_path.write_text(
            json.dumps(incoming_with_delta(), ensure_ascii=False), encoding="utf-8"
        )
        self.assertEqual(
            mk.main([str(self.base_path), str(self.incoming_path), "-o", str(output)]),
            1,
        )
        self.assertFalse((self.root / "versions").exists())

    def test_cli_reports_deep_nesting_instead_of_a_recursion_traceback(self) -> None:
        output = self.root / "merged.knowledge.json"
        nested = "null"
        for _ in range(mk.MAX_STRUCTURE_DEPTH + 10):
            nested = '{"extension":%s}' % nested
        payload = json.dumps(incoming_with_delta(), ensure_ascii=False)
        self.incoming_path.write_text('{"extension":%s,' % nested + payload[1:], encoding="utf-8")

        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            code = mk.main([str(self.base_path), str(self.incoming_path), "-o", str(output)])

        self.assertEqual(code, 1)
        self.assertIn("nesting is too deep", stderr.getvalue())
        self.assertNotIn("Traceback", stderr.getvalue())
        self.assertFalse(output.exists())
        self.assertFalse((self.root / "versions").exists())

    def test_symlink_output_is_rejected_without_touching_target(self) -> None:
        outside = self.root / "outside.json"
        outside.write_text("outside", encoding="utf-8")
        output = self.root / "merged.knowledge.json"
        try:
            output.symlink_to(outside)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks unavailable")
        code = mk.main([
            str(self.base_path), str(self.incoming_path), "-o", str(output), "--force",
        ])
        self.assertEqual(code, 1)
        self.assertEqual(outside.read_text(encoding="utf-8"), "outside")

    def test_archive_rotation_retires_but_never_deletes_prior_bytes(self) -> None:
        versions = self.root / "versions"
        payloads = [b"version one\n", b"version two\n", b"version three\n"]
        for payload in payloads:
            mk._archive_prior(self.base_path, payload, versions, max_versions=2)

        active = sorted(path.read_bytes() for path in versions.glob("*.json"))
        retired = sorted(path.read_bytes() for path in (versions / "retired").glob("*.json"))
        self.assertEqual(len(active), 2)
        self.assertEqual(len(retired), 1)
        self.assertEqual(sorted(active + retired), sorted(payloads))

        with self.assertRaisesRegex(mk.WriteSafetyError, "between 1 and 5"):
            mk._archive_prior(self.base_path, b"invalid window", versions, max_versions=6)


if __name__ == "__main__":
    unittest.main(verbosity=2)
