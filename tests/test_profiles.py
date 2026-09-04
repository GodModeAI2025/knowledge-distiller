"""Focused tests for versioned compiler/extraction profiles."""

from __future__ import annotations

import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
DEFAULT = ROOT / "profiles" / "default.json"
PROFILE_SCHEMA = ROOT / "schema" / "profile.schema.json"
GRAPH_SCHEMA = ROOT / "schema" / "knowledge.schema.json"
sys.path.insert(0, str(SCRIPTS))

import validate_profile as vp  # noqa: E402


def default_profile():
    return vp.load_profile(DEFAULT)


def manual_report(profile):
    return vp.validate(profile, apply_schema=False)


def all_property_names(schema):
    names = set()
    if isinstance(schema, dict):
        properties = schema.get("properties")
        if isinstance(properties, dict):
            names.update(properties)
        for value in schema.values():
            names.update(all_property_names(value))
    elif isinstance(schema, list):
        for value in schema:
            names.update(all_property_names(value))
    return names


class ProfileDefaultsCase(unittest.TestCase):
    def test_default_is_valid_and_safe_by_default(self):
        profile = default_profile()
        report = manual_report(profile)

        self.assertTrue(report.ok, "\n".join(report.errors))
        self.assertEqual(report.warnings, [])
        self.assertEqual(profile["profile_version"], "1.0")
        self.assertEqual(profile["compiler"]["evidence_policy"]["mode"], "evidence_first")
        self.assertFalse(profile["compiler"]["inference_policy"]["enabled"])
        self.assertFalse(profile["compiler"]["spatial_policy"]["inference_enabled"])
        self.assertFalse(profile["retrieval"]["chunk_policy"]["include_inferred_content"])
        self.assertFalse(profile["extraction"]["network_access"])
        self.assertFalse(profile["extraction"]["execute_active_content"])
        self.assertEqual(
            set(profile["compiler"]["graph_policy"]["allowed_edge_types"]),
            vp.EDGE_TYPES,
        )
        self.assertEqual(set(profile["limits"]), set(vp.LIMIT_BOUNDS))

    def test_local_json_schema_mirrors_the_default_and_unsafe_constants(self):
        schema = json.loads(PROFILE_SCHEMA.read_text(encoding="utf-8"))
        self.assertEqual(schema["$schema"], "https://json-schema.org/draft/2020-12/schema")

        clean = vp.Report()
        if not vp.schema_validate(default_profile(), clean):
            self.skipTest("optional jsonschema package is not installed")
        self.assertEqual(clean.errors, [], "\n".join(clean.errors))

        unsafe = default_profile()
        unsafe["extraction"]["network_access"] = True
        unsafe_report = vp.Report()
        vp.schema_validate(unsafe, unsafe_report)
        self.assertTrue(any("network_access" in item for item in unsafe_report.errors))

        inference_without_review = default_profile()
        inference_without_review["compiler"]["inference_policy"]["enabled"] = True
        inference_without_review["compiler"]["inference_policy"][
            "human_review_required"
        ] = False
        conditional_report = vp.Report()
        vp.schema_validate(inference_without_review, conditional_report)
        self.assertTrue(
            any("human_review_required" in item for item in conditional_report.errors)
        )

        extension = default_profile()
        extension["future_extension"] = {"enabled": False}
        extension_report = vp.Report()
        vp.schema_validate(extension, extension_report)
        self.assertEqual(extension_report.errors, [])

    def test_operational_policy_and_sensitive_fields_are_absent_from_graph_schema(self):
        graph = json.loads(GRAPH_SCHEMA.read_text(encoding="utf-8"))
        operational = {
            "profile_version", "profile_id", "compiler", "extraction", "retrieval", "limits"
        }
        self.assertTrue(operational.isdisjoint(graph.get("properties", {})))

        sensitive = []
        for name in all_property_names(graph):
            normalized = vp._normalized_key(name)
            words = set(normalized.split("_"))
            if normalized in vp.SENSITIVE_KEYS or words.intersection(
                {"prompt", "secret", "password", "credential"}
            ):
                sensitive.append(name)
        self.assertEqual(sensitive, [])


class ProfilePolicyCase(unittest.TestCase):
    def assertInvalid(self, profile, fragment):  # noqa: N802 - unittest convention
        report = manual_report(profile)
        self.assertFalse(report.ok)
        self.assertTrue(
            any(fragment in message for message in report.errors),
            f"missing {fragment!r} in:\n" + "\n".join(report.errors),
        )

    def test_unknown_keys_warn_but_are_ignored(self):
        profile = default_profile()
        profile["future_extension"] = {"enabled": False}
        profile["retrieval"]["chunk_policy"]["ranking_hint"] = "stable"
        report = manual_report(profile)

        self.assertTrue(report.ok, "\n".join(report.errors))
        self.assertEqual(len(report.warnings), 2)
        self.assertTrue(any("future_extension" in item for item in report.warnings))
        self.assertTrue(any("ranking_hint" in item for item in report.warnings))

    def test_missing_badly_typed_and_unsupported_identity_fields_fail(self):
        profile = default_profile()
        profile.pop("description")
        self.assertInvalid(profile, "missing required key 'description'")

        profile = default_profile()
        profile["profile_id"] = "Not Safe"
        self.assertInvalid(profile, "kebab-case")

        profile = default_profile()
        profile["profile_version"] = "2.0"
        self.assertInvalid(profile, "unsupported profile version")

        profile = default_profile()
        profile["compiler"] = []
        self.assertInvalid(profile, "$.compiler: expected object")

    def test_evidence_first_contract_is_complete(self):
        profile = default_profile()
        profile["compiler"]["evidence_policy"]["required_for"].pop()
        self.assertInvalid(profile, "must cover")

        profile = default_profile()
        profile["compiler"]["evidence_policy"]["require_selector"] = False
        self.assertInvalid(profile, "require_selector: must be true")

        profile = default_profile()
        profile["compiler"]["evidence_policy"]["minimum_items_per_claim"] = 0
        self.assertInvalid(profile, "must be between 1 and 100")

        profile = default_profile()
        profile["compiler"]["evidence_policy"]["allowed_attribution_basis"] = []
        self.assertInvalid(profile, "must not be empty")

    def test_inference_requires_explicit_reviewed_opt_in(self):
        profile = default_profile()
        profile["compiler"]["inference_policy"]["enabled"] = True
        profile["compiler"]["inference_policy"]["human_review_required"] = False
        self.assertInvalid(profile, "enabled inference requires")

        profile = default_profile()
        profile["compiler"]["evidence_policy"]["allowed_attribution_basis"].append(
            "model_inferred"
        )
        self.assertInvalid(profile, "model_inferred requires inference")

        profile = default_profile()
        profile["retrieval"]["chunk_policy"]["include_inferred_content"] = True
        self.assertInvalid(profile, "requires reviewed inference")

        profile = default_profile()
        profile["compiler"]["inference_policy"]["persist_chain_of_thought"] = True
        self.assertInvalid(profile, "chain-of-thought must not be persisted")

        profile = default_profile()
        profile["compiler"]["inference_policy"]["allow_unattributed_claims"] = True
        self.assertInvalid(profile, "unattributed claims are forbidden")

    def test_spatial_inference_requires_base_inference_evidence_and_review(self):
        profile = default_profile()
        profile["compiler"]["spatial_policy"]["inference_enabled"] = True
        self.assertInvalid(profile, "requires compiler inference")

        profile = default_profile()
        profile["compiler"]["spatial_policy"]["require_evidence"] = False
        self.assertInvalid(profile, "require_evidence: must be true")

        profile = default_profile()
        profile["compiler"]["inference_policy"]["enabled"] = True
        profile["compiler"]["spatial_policy"]["inference_enabled"] = True
        profile["compiler"]["spatial_policy"]["human_review_required"] = False
        self.assertInvalid(profile, "spatial inference requires human_review_required")

    def test_reviewed_inference_and_spatial_opt_in_can_be_valid(self):
        profile = default_profile()
        profile["compiler"]["inference_policy"]["enabled"] = True
        profile["compiler"]["evidence_policy"]["allowed_attribution_basis"].append(
            "model_inferred"
        )
        profile["compiler"]["spatial_policy"]["inference_enabled"] = True
        profile["retrieval"]["chunk_policy"]["include_inferred_content"] = True

        report = manual_report(profile)
        self.assertTrue(report.ok, "\n".join(report.errors))
        mirror = vp.validate(profile, apply_schema=True)
        self.assertTrue(mirror.ok, "\n".join(mirror.errors))

    def test_graph_edge_allowlist_and_monotonicity_are_enforced(self):
        for bad_edges, fragment in (
            ([], "must not be empty"),
            (["uses", "uses"], "duplicate items"),
            (["uses", "invented"], "unsupported item"),
        ):
            with self.subTest(edges=bad_edges):
                profile = default_profile()
                profile["compiler"]["graph_policy"]["allowed_edge_types"] = bad_edges
                self.assertInvalid(profile, fragment)

        profile = default_profile()
        profile["compiler"]["graph_policy"]["preserve_conflicts"] = False
        self.assertInvalid(profile, "preserve_conflicts: must be true")

        profile = default_profile()
        profile["compiler"]["graph_policy"]["monotonic_merge"] = False
        self.assertInvalid(profile, "monotonic_merge: must be true")

    def test_extraction_cannot_enable_network_or_active_content(self):
        profile = default_profile()
        profile["extraction"]["network_access"] = True
        self.assertInvalid(profile, "network access is forbidden")

        profile = default_profile()
        profile["extraction"]["execute_active_content"] = True
        self.assertInvalid(profile, "active content execution is forbidden")

        profile = default_profile()
        profile["extraction"]["allowed_source_types"].append("pdf")
        self.assertInvalid(profile, "unsupported item")

        profile = default_profile()
        profile["extraction"]["encoding_policy"] = "guess"
        self.assertInvalid(profile, "unsupported value")

    def test_chunk_policy_is_evidence_bound_and_deterministic(self):
        profile = default_profile()
        profile["retrieval"]["chunk_policy"]["include_evidence_excerpt"] = False
        self.assertInvalid(profile, "include_evidence_excerpt: must be true")

        profile = default_profile()
        profile["retrieval"]["chunk_policy"]["include_source_metadata"] = False
        self.assertInvalid(profile, "include_source_metadata: must be true")

        profile = default_profile()
        profile["retrieval"]["chunk_policy"]["deduplicate_by"].reverse()
        self.assertInvalid(profile, "expected deterministic order")

        profile = default_profile()
        profile["retrieval"]["chunk_policy"]["max_chunks_per_node"] = 0
        self.assertInvalid(profile, "must be between 1 and 100")

    def test_limits_reject_booleans_zero_and_unbounded_values(self):
        profile = default_profile()
        profile["limits"]["max_segments"] = True
        self.assertInvalid(profile, "max_segments: expected integer")

        profile = default_profile()
        profile["limits"]["max_input_bytes"] = 0
        self.assertInvalid(profile, "must be between 1 and 536870912")

        profile = default_profile()
        profile["limits"]["max_output_bytes"] = 2_147_483_649
        self.assertInvalid(profile, "must be between 1 and 2147483648")

    def test_sensitive_or_prompt_bearing_keys_fail_even_when_unknown(self):
        for key in (
            "prompt_template", "api-key", "clientSecret", "accessToken", "sshPrivateKey",
            "reasoningTrace", "chain-of-thought", "scratchpad",
        ):
            with self.subTest(key=key):
                profile = default_profile()
                profile["compiler"][key] = "must-never-be-stored"
                report = manual_report(profile)
                self.assertFalse(report.ok)
                self.assertTrue(any("forbidden in profiles" in item for item in report.errors))
                self.assertTrue(any("unknown key" in item for item in report.warnings))


class ProfileLoadingAndCliCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="kd-profile-test-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)

    def test_loader_rejects_urls_duplicate_keys_constants_encoding_and_non_objects(self):
        with self.assertRaisesRegex(vp.ProfileLoadError, "URLs and URI schemes"):
            vp.load_profile("https://example.invalid/profile.json")

        cases = {
            "duplicate.json": ('{"profile_id":"a","profile_id":"b"}', "duplicate object key"),
            "nan.json": ('{"value":NaN}', "non-finite number"),
            "overflow.json": ('{"value":1e999}', "non-finite number"),
            "huge-int.json": ('{"value":' + "9" * 4_301 + "}", "safe numeric length"),
            "array.json": ('[]', "root must be a JSON object"),
        }
        for name, (content, fragment) in cases.items():
            with self.subTest(name=name):
                path = self.base / name
                path.write_text(content, encoding="utf-8")
                with self.assertRaisesRegex(vp.ProfileLoadError, fragment):
                    vp.load_profile(path)

        invalid_utf8 = self.base / "invalid.json"
        invalid_utf8.write_bytes(b"{\xff}")
        with self.assertRaisesRegex(vp.ProfileLoadError, "not valid UTF-8"):
            vp.load_profile(invalid_utf8)

        with self.assertRaisesRegex(vp.ProfileLoadError, "limit is 1 bytes"):
            vp.load_profile(DEFAULT, max_bytes=1)

    def test_cli_output_and_exit_codes_are_deterministic(self):
        command = [
            sys.executable,
            "-B",
            str(SCRIPTS / "validate_profile.py"),
            str(DEFAULT),
            "--stdlib-only",
            "--json",
        ]
        first = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=False)
        second = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=False)
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(first.stdout, second.stdout)
        payload = json.loads(first.stdout)
        self.assertTrue(payload["valid"])
        self.assertEqual(payload["schema_validation"], "unavailable")
        self.assertEqual(payload["profile"], "default.json")

        unsafe = default_profile()
        unsafe["extraction"]["network_access"] = True
        unsafe_path = self.base / "unsafe.json"
        unsafe_path.write_text(json.dumps(unsafe), encoding="utf-8")
        invalid = subprocess.run(
            command[:3] + [str(unsafe_path), "--stdlib-only"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(invalid.returncode, 1)
        self.assertIn("INVALID profile", invalid.stdout)

        malformed_path = self.base / "malformed.json"
        malformed_path.write_text("{", encoding="utf-8")
        malformed = subprocess.run(
            command[:3] + [str(malformed_path), "--stdlib-only"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(malformed.returncode, 2)
        self.assertIn("profile_load", malformed.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
