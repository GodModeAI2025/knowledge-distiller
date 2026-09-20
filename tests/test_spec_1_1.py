"""Regression tests for the additive Spec 1.1 trust and provenance contract."""
from __future__ import annotations

import copy
import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import build_bundle  # noqa: E402
import build_graph  # noqa: E402
import build_md  # noqa: E402
import validate_knowledge as vk  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "contextual.knowledge.json"


def load() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def run(doc: dict, prev: dict | None = None) -> vk.Report:
    report = vk.Report()
    schema_checked = vk.schema_validate(doc, report)
    core = vk.validate(doc, prev=prev, ran_schema=schema_checked)
    report.errors.extend(core.errors)
    report.warnings.extend(core.warnings)
    return report


def error_text(report: vk.Report) -> str:
    return "\n".join(report.errors)


class Spec11FixtureTests(unittest.TestCase):
    def test_contextual_fixture_is_clean(self):
        report = run(load())
        self.assertEqual(report.errors, [], error_text(report))
        self.assertEqual(report.warnings, [])
        self.assertEqual(report.conformance_score(), 100)

    def test_stored_scores_are_equal_and_match_base_diagnostics(self):
        doc = load()
        doc["metadata"]["quality_score"] = 17
        doc["metadata"]["conformance_score"] = 99

        report = vk.validate(doc)
        errors = error_text(report)
        self.assertIn("must be equal compatibility aliases", errors)
        self.assertIn("metadata.quality_score: stored 17 but diagnostics require 100", errors)
        self.assertIn("metadata.conformance_score: stored 99 but diagnostics require 100", errors)

        build_graph.recompute(doc)
        self.assertEqual(doc["metadata"]["quality_score"], 100)
        self.assertEqual(doc["metadata"]["conformance_score"], 100)
        self.assertEqual(vk.validate(doc).errors, [])

    def test_derivation_inputs_are_nonempty_in_core_and_schema_mirror(self):
        doc = load()
        doc["chunks"][1]["derivation"]["inputs"] = []
        core = vk.validate(doc, check_stored_scores=False)
        self.assertIn("require at least one explicit input", error_text(core))

        schema_report = vk.Report()
        if not vk.schema_validate(doc, schema_report):
            self.skipTest("jsonschema is not installed")
        self.assertTrue(
            any("schema[chunks/1/derivation/inputs]" in item for item in schema_report.errors),
            error_text(schema_report),
        )

    def test_dependency_free_core_catches_schema_level_contracts(self):
        doc = load()
        doc["metadata"]["quality_score"] = True
        doc["metadata"]["sources"][0]["agents"][0]["type"] = "robot"
        doc["evidence"][0]["support"] = "endorses"
        doc["evidence"][0]["selector"] = {"type": "PageSelector", "page": 0}
        doc["nodes"][0]["spatial_contexts"][0]["place"]["kind"] = "planet"
        doc["fact_conflicts"][0]["relation"] = "duplicates"

        report = vk.validate(doc)  # deliberately do not call jsonschema
        text = error_text(report)
        for expected in (
            "metadata.quality_score",
            ".agents[0].type",
            ".support",
            ".selector.page",
            ".place.kind",
            ".relation",
        ):
            self.assertIn(expected, text)

    def test_unknown_fields_are_visible_and_private_reasoning_is_rejected(self):
        doc = load()
        doc["evidence"][0]["selector"]["ghost"] = "ignored"
        doc["metadata"]["sources"][0]["authoritative_source"] = 0.9
        doc["edges"][0]["reasoning"] = "hidden chain"
        doc["extension"] = {"chain_of_thought": "private scratchpad"}
        report = run(doc)
        warnings = "\n".join(report.warnings)
        self.assertIn("unknown extra key 'ghost'", warnings)
        self.assertIn("intrinsic authority score is unsupported", warnings)
        errors = error_text(report)
        self.assertIn("edges[0].reasoning: private reasoning/scratchpad fields are forbidden", errors)
        self.assertIn("extension.chain_of_thought: private reasoning/scratchpad fields are forbidden", errors)

    def test_core_checks_context_questions_and_stable_identity(self):
        doc = load()
        doc["@context"] = []
        doc["open_questions"] = [7]
        doc["nodes"][1]["resource"] = doc["nodes"][0]["resource"]
        doc["chunks"][1]["id"] = doc["chunks"][0]["id"]
        report = vk.validate(doc)
        text = error_text(report)
        self.assertIn("@context: expected object", text)
        self.assertIn("open_questions[0]: expected string", text)
        self.assertIn("duplicate resource identity", text)
        self.assertIn("duplicate chunk id", text)


class SchemaCoreParityTests(unittest.TestCase):
    def schema_report(self, doc: dict) -> vk.Report:
        report = vk.Report()
        if not vk.schema_validate(doc, report):
            self.skipTest("jsonschema is not installed or could not safely validate the document")
        return report

    def test_schema_mirrors_common_private_field_name_variants(self):
        for field in ("COT", "Reasoning-Trace", "internal_scratchpad", "chain.of.thought"):
            with self.subTest(field=field):
                doc = load()
                doc["extension"] = {field: "private"}
                self.assertTrue(self.schema_report(doc).errors)
                self.assertIn("private reasoning/scratchpad fields are forbidden", error_text(vk.validate(doc)))

    def test_arbitrary_intratoken_private_field_separators_are_a_core_only_boundary(self):
        doc = load()
        doc["extension"] = {"c.o.t": "private"}
        self.assertEqual(self.schema_report(doc).errors, [])
        self.assertIn("private reasoning/scratchpad fields are forbidden", error_text(vk.validate(doc)))

    def test_schema_mirrors_url_and_identifier_scheme_guards(self):
        cases = (
            ("source", lambda doc: doc["metadata"]["sources"][0].__setitem__("url", "javascript:alert(1)")),
            ("citation", lambda doc: doc["nodes"][0]["citations"][0].__setitem__("url", "https://u:p@example.invalid/x")),
            ("resource", lambda doc: doc["nodes"][0].__setitem__("resource", "file:///private/data")),
            ("place", lambda doc: doc["nodes"][0]["spatial_contexts"][0]["place"].__setitem__("id", "Germany")),
        )
        for label, mutate in cases:
            with self.subTest(label=label):
                doc = load()
                mutate(doc)
                self.assertTrue(self.schema_report(doc).errors)
                self.assertTrue(vk.validate(doc, check_stored_scores=False).errors)

    def test_schema_requires_selector_specific_fields(self):
        selector_types = (
            "TextQuoteSelector", "TextPositionSelector", "PageSelector", "FragmentSelector",
            "CsvSelector", "SvgSelector", "JsonPointerSelector", "CodeSelector",
        )
        for selector_type in selector_types:
            with self.subTest(selector_type=selector_type):
                doc = load()
                doc["evidence"][0]["selector"] = {"type": selector_type}
                self.assertTrue(self.schema_report(doc).errors)
                self.assertIn("missing required field", error_text(vk.validate(doc, check_stored_scores=False)))

    def test_selector_order_is_an_explicit_core_only_mirror_boundary(self):
        doc = load()
        doc["evidence"][0]["selector"] = {
            "type": "TextPositionSelector", "start": 9, "end": 3,
        }
        self.assertEqual(self.schema_report(doc).errors, [])
        self.assertIn("0 <= start <= end", error_text(vk.validate(doc, check_stored_scores=False)))

    def test_schema_mirrors_derived_evidence_and_spatial_conditions(self):
        cases = (
            lambda doc: doc["evidence"][0].update({"attribution_basis": "parser_derived"}),
            lambda doc: doc["nodes"][0]["spatial_contexts"][0].update({"basis": "model_inferred"}),
        )
        for mutate in cases:
            doc = load()
            mutate(doc)
            self.assertTrue(self.schema_report(doc).errors)
            self.assertIn("requires an explicit derivation", error_text(vk.validate(doc, check_stored_scores=False)))

    def test_schema_mirrors_origin_provenance_conditions(self):
        locations = (("edges", 0), ("facts", 0), ("chunks", 0), ("claims", 0))
        for collection, index in locations:
            with self.subTest(collection=collection, condition="source"):
                doc = load()
                item = doc[collection][index]
                item["origin"] = "source_stated"
                item["evidence"] = []
                self.assertTrue(self.schema_report(doc).errors)
                self.assertIn("requires source evidence", error_text(vk.validate(doc, check_stored_scores=False)))
            with self.subTest(collection=collection, condition="derived"):
                doc = load()
                item = doc[collection][index]
                item["origin"] = "synthesized"
                item.pop("derivation", None)
                self.assertTrue(self.schema_report(doc).errors)
                self.assertIn("requires an explicit derivation", error_text(vk.validate(doc, check_stored_scores=False)))

    def test_schema_mirrors_inference_retrieval_and_unique_conflict_facts(self):
        inference = load()
        inference["chunks"][1]["include_in_default_retrieval"] = True
        self.assertTrue(self.schema_report(inference).errors)
        self.assertIn("include_in_default_retrieval=false", error_text(vk.validate(inference, check_stored_scores=False)))

        conflict = load()
        fact_id = conflict["fact_conflicts"][0]["facts"][0]
        conflict["fact_conflicts"][0]["facts"] = [fact_id, fact_id]
        self.assertTrue(self.schema_report(conflict).errors)
        self.assertIn("two distinct fact ids", error_text(vk.validate(conflict, check_stored_scores=False)))

    def test_parsed_credential_query_is_an_explicit_core_only_mirror_boundary(self):
        doc = load()
        doc["metadata"]["sources"][0]["url"] = "https://example.invalid/doc?api_key=secret"
        self.assertEqual(self.schema_report(doc).errors, [])
        self.assertIn("URL query contains a credential", error_text(vk.validate(doc, check_stored_scores=False)))


class CoreRobustnessTests(unittest.TestCase):
    def test_unhashable_reference_values_are_diagnostics_not_crashes(self):
        cases = (
            ("node id", lambda doc: doc["nodes"][0].__setitem__("id", {}), "nodes[0].id"),
            ("node evidence", lambda doc: doc["nodes"][0].__setitem__("evidence", [{}]), "nodes[alpha].evidence[0]"),
            ("node sources", lambda doc: doc["nodes"][0].__setitem__("sources", [{}]), "nodes[alpha].sources[0]"),
            ("claim ids", lambda doc: doc["nodes"][0].__setitem__("claim_ids", [{}]), "nodes[alpha].claim_ids[0]"),
            ("chunk concepts", lambda doc: doc["chunks"][0].__setitem__("concepts", [{}]), "chunks[chunk-alpha-source].concepts[0]"),
            ("cluster concepts", lambda doc: doc["clusters"][0].__setitem__("concepts", [{}]), "clusters[0].concepts[0]"),
            ("evidence source", lambda doc: doc["evidence"][0].__setitem__("source", {}), "evidence[0].source"),
            ("citation source", lambda doc: doc["nodes"][0]["citations"][0].__setitem__("source", {}), "citations[0].source"),
            ("claim node", lambda doc: doc["claims"][0].__setitem__("node", []), "claims[claim-alpha-enables-beta].node"),
            ("edge source", lambda doc: doc["edges"][0].__setitem__("source", {}), "edges[0].source"),
            ("fact source", lambda doc: doc["facts"][0].__setitem__("source", {}), "facts[fact-deployments-source].source"),
            ("fact concept", lambda doc: doc["facts"][0].__setitem__("concept", {}), "facts[fact-deployments-source].concept"),
            ("conflict facts", lambda doc: doc["fact_conflicts"][0].__setitem__("facts", [{}, "fact-deployments-review"]), "fact_conflicts[0].facts[0]"),
            ("spatial evidence", lambda doc: doc["nodes"][0]["spatial_contexts"][0].__setitem__("evidence", [{}]), "spatial_contexts[0].evidence[0]"),
            ("assessment evidence", lambda doc: doc["assessments"][0].__setitem__("evidence", [{}]), "assessments[0].evidence[0]"),
        )
        for label, mutate, expected in cases:
            with self.subTest(label=label):
                doc = load()
                mutate(doc)
                report = vk.validate(doc, check_stored_scores=False)
                self.assertIn(expected, error_text(report))

    def test_malformed_and_authorityless_http_urls_are_diagnostics_not_crashes(self):
        for value in ("https://[", "http:", "https:///path", "https://?x=1"):
            with self.subTest(value=value):
                doc = load()
                doc["metadata"]["sources"][0]["url"] = value
                report = vk.validate(doc, check_stored_scores=False)
                self.assertTrue(
                    any("malformed URL/URI" in item or "requires a host" in item for item in report.errors),
                    error_text(report),
                )

    def test_required_place_identifier_cannot_be_empty_or_null(self):
        for value in ("", None):
            with self.subTest(value=value):
                doc = load()
                doc["nodes"][0]["spatial_contexts"][0]["place"]["id"] = value
                report = vk.validate(doc, check_stored_scores=False)
                self.assertIn("place.id: expected non-empty string", error_text(report))

    def test_spec_version_requires_major_minor_without_jsonschema(self):
        for value in ("future", "1", "1.x"):
            with self.subTest(value=value):
                doc = load()
                doc["metadata"]["distiller_spec_version"] = value
                self.assertIn("expected '<major>.<minor>'", error_text(vk.validate(doc, check_stored_scores=False)))

        future = load()
        future["metadata"]["distiller_spec_version"] = "9.9"
        report = vk.validate(future, check_stored_scores=False)
        self.assertFalse(any("expected '<major>.<minor>'" in item for item in report.errors))
        self.assertIn("unknown version '9.9'", "\n".join(report.warnings))

    def test_explicit_null_does_not_masquerade_as_an_empty_collection_or_enum(self):
        cases = (
            ("clusters", lambda doc: doc.__setitem__("clusters", None), "clusters: expected array"),
            ("nodes", lambda doc: doc.__setitem__("nodes", None), "nodes: expected array"),
            ("edges", lambda doc: doc.__setitem__("edges", None), "edges: expected array"),
            ("authors", lambda doc: doc["metadata"]["sources"][0].__setitem__("authors", None), "authors: expected array"),
            ("place identifiers", lambda doc: doc["nodes"][0]["spatial_contexts"][0]["place"].__setitem__("identifiers", None), "place.identifiers: expected array"),
            ("claim review", lambda doc: doc["claims"][0].__setitem__("review_status", None), "review_status: invalid value None"),
        )
        for label, mutate, expected in cases:
            with self.subTest(label=label):
                doc = load()
                mutate(doc)
                self.assertIn(expected, error_text(vk.validate(doc, check_stored_scores=False)))

    def test_schema_recursion_limit_never_aborts_core_validation(self):
        doc = load()
        nested: dict = {}
        cursor = nested
        for _ in range(350):
            cursor["extension"] = {}
            cursor = cursor["extension"]
        doc["extension"] = nested
        schema_report = vk.Report()
        checked = vk.schema_validate(doc, schema_report)
        if not checked:
            self.assertIn("recursion limit exceeded", "\n".join(schema_report.warnings))
        self.assertIsInstance(vk.validate(doc, check_stored_scores=False), vk.Report)

    def test_nesting_past_the_depth_limit_is_an_error_not_a_traceback(self):
        doc = load()
        nested: dict = {}
        cursor = nested
        for _ in range(vk.MAX_STRUCTURE_DEPTH + 10):
            cursor["extension"] = {}
            cursor = cursor["extension"]
        doc["extension"] = nested
        doc["nodes"][0]["reasoning"] = "private"

        report = vk.validate(doc, check_stored_scores=False)

        self.assertTrue(
            any("nesting exceeds the safe depth limit" in item for item in report.errors),
            "the bound itself must be reported",
        )
        self.assertTrue(
            any("private reasoning/scratchpad fields are forbidden" in item for item in report.errors),
            "stopping the descent must not stop the audit of everything beside it",
        )
        self.assertEqual(
            len([item for item in report.errors if "nesting exceeds" in item]), 1,
            "the bound is reported once, not once per branch",
        )

    def test_nesting_at_the_depth_limit_is_still_audited(self):
        doc = load()
        nested: dict = {}
        cursor = nested
        # The document root and its ``extension`` value already account for two
        # levels, so this is the deepest private field the audit can still reach.
        for _ in range(vk.MAX_STRUCTURE_DEPTH - 2):
            cursor["extension"] = {}
            cursor = cursor["extension"]
        cursor["chain_of_thought"] = "private"
        doc["extension"] = nested

        report = vk.validate(doc, check_stored_scores=False)

        self.assertFalse([item for item in report.errors if "nesting exceeds" in item])
        self.assertTrue(
            any("private reasoning/scratchpad fields are forbidden" in item for item in report.errors)
        )

    def test_producer_must_violations_are_errors_not_warnings(self):
        cluster_doc = load()
        cluster_doc["clusters"][0]["concepts"].remove("alpha")
        cluster_report = vk.validate(cluster_doc, check_stored_scores=False)
        self.assertIn("not listed in cluster", error_text(cluster_report))
        self.assertFalse(any("not listed in cluster" in item for item in cluster_report.warnings))

        edge_doc = load()
        edge_doc["edges"][0]["id"] = "legacy-edge-id"
        edge_report = vk.validate(edge_doc, check_stored_scores=False)
        self.assertIn("non-canonical edge id", error_text(edge_report))
        self.assertFalse(any("non-canonical edge id" in item for item in edge_report.warnings))


class ProvenanceInvariantTests(unittest.TestCase):
    def test_evidence_references_must_resolve(self):
        doc = load()
        doc["claims"][0]["evidence"] = ["missing-evidence"]
        report = run(doc)
        self.assertIn("does not resolve to top-level evidence", error_text(report))

    def test_claim_must_remain_projected_into_node_statements(self):
        doc = load()
        doc["claims"][0]["statement"] = "A detached claim. [1]"
        report = run(doc)
        self.assertIn("compatibility projection", error_text(report))

    def test_source_claim_needs_evidence_and_inference_needs_derivation(self):
        doc = load()
        doc["claims"][0]["evidence"] = []
        doc["chunks"][1].pop("derivation")
        report = run(doc)
        text = error_text(report)
        self.assertIn("source_stated content requires source evidence", text)
        self.assertIn("model_inferred content requires an explicit derivation", text)

    def test_inference_chunks_are_excluded_from_default_retrieval(self):
        doc = load()
        doc["chunks"][1]["include_in_default_retrieval"] = True
        report = run(doc)
        self.assertIn("inference chunks must set include_in_default_retrieval=false", error_text(report))

    def test_spatial_context_requires_evidence_and_sensitive_places_are_redacted(self):
        doc = load()
        context = doc["nodes"][0]["spatial_contexts"][0]
        context["evidence"] = []
        context["place"].update({"kind": "address", "precision": "exact", "sensitive": True, "redacted": False})
        report = run(doc)
        text = error_text(report)
        self.assertIn("spatial context requires explicit provenance", text)
        self.assertIn("sensitive precise location must be redacted", text)

    def test_redacted_places_are_structurally_coarse(self):
        doc = load()
        place = doc["nodes"][0]["spatial_contexts"][0]["place"]
        place.update({
            "kind": "address", "precision": "exact", "sensitive": True,
            "redacted": True, "geometry": {"type": "Point", "coordinates": [1, 2]},
        })
        report = run(doc)
        text = error_text(report)
        for expected in (
            "redacted place must use a coarser kind",
            "redacted place cannot retain exact/address/site precision",
            "redacted place must omit geometry",
            "redacted place must omit external identifiers",
        ):
            self.assertIn(expected, text)

        place.update({"kind": "city", "precision": "city"})
        place.pop("geometry")
        place.pop("identifiers")
        report = run(doc)
        self.assertFalse(
            any("redacted place" in item for item in report.errors),
            error_text(report),
        )

    def test_unsafe_links_and_credential_bearing_urls_are_errors(self):
        doc = load()
        doc["metadata"]["sources"][0]["url"] = "javascript:alert(1)"
        doc["nodes"][0]["citations"][0]["url"] = "https://example.invalid/doc?token=secret"
        doc["nodes"][1]["citations"][0]["url"] = (
            "https://user:pass@example.invalid/doc?X-Amz-Signature=secret"
        )
        report = run(doc)
        text = error_text(report)
        self.assertIn("unsafe or unsupported URI scheme", text)
        self.assertGreaterEqual(text.count("URL query contains a credential"), 2)
        self.assertIn("URL authority contains credentials", text)

        safe = load()
        safe["metadata"]["sources"][0]["url"] = (
            "https://example.invalid/doc?design=compact&signal=green&tokenization=word"
        )
        safe_report = run(safe)
        self.assertFalse(
            any("credential" in item for item in safe_report.errors),
            error_text(safe_report),
        )

    def test_derived_attribution_and_spatial_inference_need_derivations(self):
        doc = load()
        doc["evidence"][0]["attribution_basis"] = "parser_derived"
        doc["nodes"][0]["spatial_contexts"][0]["basis"] = "model_inferred"
        report = run(doc)
        text = error_text(report)
        self.assertIn("parser_derived attribution requires an explicit derivation", text)
        self.assertIn("model_inferred spatial context requires an explicit derivation", text)


class MergeAuditTests(unittest.TestCase):
    def test_same_count_statement_rewrite_is_not_monotonic(self):
        previous = load()
        current = copy.deepcopy(previous)
        current["nodes"][0]["statements"][0] = "Alpha silently changed its meaning. [1]"
        current["claims"][0]["statement"] = current["nodes"][0]["statements"][0]
        report = run(current, prev=previous)
        self.assertIn("merge: node[alpha].statements dropped or changed", error_text(report))

    def test_same_count_fact_rewrite_is_not_monotonic(self):
        previous = load()
        current = copy.deepcopy(previous)
        current["facts"][0]["value"] = "999"
        report = run(current, prev=previous)
        self.assertIn("facts[fact-deployments-source].value changed", error_text(report))

    def test_resource_identity_allows_coordinated_node_id_rename(self):
        previous = load()
        current = copy.deepcopy(previous)
        current["nodes"][0]["id"] = "alpha-renamed"
        current["claims"][0]["node"] = "alpha-renamed"
        current["edges"][0]["source"] = "alpha-renamed"
        for fact in current["facts"]:
            if fact.get("concept") == "alpha":
                fact["concept"] = "alpha-renamed"
        for chunk in current["chunks"]:
            chunk["concepts"] = ["alpha-renamed" if item == "alpha" else item for item in chunk["concepts"]]
        build_graph.recompute(current)

        report = run(current, prev=previous)
        merge_errors = [item for item in report.errors if item.startswith("merge:")]
        self.assertEqual(merge_errors, [], "\n".join(merge_errors))


class RenderingAndBuildTests(unittest.TestCase):
    def test_build_graph_is_idempotent_and_scores_only_conformance(self):
        doc = load()
        doc["metadata"]["concept_count"] = 99
        build_graph.recompute(doc)
        first = json.dumps(doc, ensure_ascii=False, sort_keys=True)
        build_graph.recompute(doc)
        second = json.dumps(doc, ensure_ascii=False, sort_keys=True)
        self.assertEqual(first, second)
        self.assertEqual(doc["metadata"]["conformance_score"], 100)
        self.assertEqual(doc["metadata"]["quality_score"], 100)

    def test_markdown_renders_provenance_without_claiming_semantic_accuracy(self):
        markdown = build_md.render(load())
        for expected in (
            "## Evidence",
            "## Assessments",
            "## Fact Conflicts",
            "Claim `claim-alpha-enables-beta`",
            "Semantische Richtigkeit: nicht durch den Validator bewertet",
            "Germany",
        ):
            self.assertIn(expected, markdown)

    def test_bundle_contains_provenance_views(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "context.bundle"
            build_bundle.build(load(), output)
            for filename in ("evidence.md", "assessments.md", "fact-conflicts.md"):
                self.assertTrue((output / filename).is_file())
            concept = (output / "concepts" / "deployment" / "alpha.md").read_text(encoding="utf-8")
            self.assertIn("claim-alpha-enables-beta", concept)
            self.assertIn("Germany", concept)


class StrictJsonCliTests(unittest.TestCase):
    def test_build_graph_rejects_non_object_root_without_rewrite(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "invalid.knowledge.json"
            original = b"[]\n"
            path.write_bytes(original)
            for arguments in ([str(path)], [str(path), "--write"]):
                with self.subTest(arguments=arguments):
                    stderr = io.StringIO()
                    with contextlib.redirect_stderr(stderr):
                        self.assertEqual(build_graph.main(arguments), 1)
                    self.assertIn("graph root must be a JSON object", stderr.getvalue())
                    self.assertEqual(path.read_bytes(), original)

    def test_build_graph_rejects_duplicate_keys_nonfinite_numbers_and_surrogates(self):
        invalid_payloads = (
            ('{"metadata":{},"metadata":{}}', "duplicate object key"),
            ('{"value":NaN}', "non-finite number"),
            ('{"value":"\\ud800"}', "isolated Unicode surrogate"),
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "invalid.knowledge.json"
            for payload, expected in invalid_payloads:
                with self.subTest(payload=payload):
                    path.write_text(payload, encoding="utf-8")
                    stderr = io.StringIO()
                    with contextlib.redirect_stderr(stderr):
                        self.assertEqual(build_graph.main([str(path)]), 1)
                    self.assertIn(expected, stderr.getvalue())

    def test_validator_rejects_duplicate_keys_nonfinite_numbers_and_surrogates(self):
        invalid_payloads = (
            '{"metadata":{},"metadata":{}}',
            '{"value":NaN}',
            '{"value":"\\ud800"}',
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "invalid.json"
            for payload in invalid_payloads:
                path.write_text(payload, encoding="utf-8")
                self.assertEqual(vk.main([str(path), "--quiet"]), 1)

    def test_validator_strictly_loads_previous_graph_too(self):
        with tempfile.TemporaryDirectory() as directory:
            current = Path(directory) / "current.json"
            previous = Path(directory) / "previous.json"
            current.write_text(json.dumps(load(), ensure_ascii=False), encoding="utf-8")
            previous.write_text('{"metadata":{},"metadata":{}}', encoding="utf-8")
            self.assertEqual(
                vk.main([str(current), "--prev", str(previous), "--quiet"]),
                1,
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
