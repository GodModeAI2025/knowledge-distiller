"""Regression coverage for strict render inputs and faithful derived Markdown."""
from __future__ import annotations

import contextlib
import copy
import io
import json
import sys
import tempfile
import unittest
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import build_bundle  # noqa: E402
import build_md  # noqa: E402
import strict_json  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "contextual.knowledge.json"


def load_fixture() -> dict:
    return strict_json.load_path(FIXTURE)


class _ExcerptParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.inside = False
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "pre" and dict(attrs).get("id") == "contextual-markdown-excerpt":
            self.inside = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "pre" and self.inside:
            self.inside = False

    def handle_data(self, data: str) -> None:
        if self.inside:
            self.parts.append(data)

    @property
    def excerpt(self) -> str:
        return "".join(self.parts)


class StrictJsonTests(unittest.TestCase):
    def test_rejects_ambiguous_and_nonportable_json(self) -> None:
        invalid = {
            "duplicate object key": b'{"x": 1, "x": 2}',
            "non-finite number": b'{"x": NaN}',
            "overflowed finite syntax": b'{"x": 1e999}',
            "isolated surrogate": b'{"x": "\\ud800"}',
        }
        for label, payload in invalid.items():
            with self.subTest(label=label):
                with self.assertRaises(strict_json.StrictJsonError):
                    strict_json.loads(payload, source=label)

    def test_rejects_excessive_nesting_as_strict_error(self) -> None:
        payload = "[" * 5000 + "0" + "]" * 5000
        with self.assertRaisesRegex(strict_json.StrictJsonError, "nesting is too deep"):
            strict_json.loads(payload, source="deep.json")

    def test_nesting_is_bounded_by_a_stated_limit_not_by_the_recursion_limit(self) -> None:
        limit = strict_json.MAX_STRUCTURE_DEPTH
        at_limit = "[" * limit + "0" + "]" * limit
        self.assertIsInstance(strict_json.loads(at_limit, source="deep.json"), list)
        over_limit = "[" * (limit + 1) + "0" + "]" * (limit + 1)
        with self.assertRaisesRegex(
            strict_json.StrictJsonError, rf"nesting is too deep; the safe depth limit is {limit}"
        ):
            strict_json.loads(over_limit, source="deep.json")

    def test_the_depth_message_does_not_carry_a_kilobyte_of_location(self) -> None:
        over_limit = "[" * (strict_json.MAX_STRUCTURE_DEPTH + 1) + "0"
        over_limit += "]" * (strict_json.MAX_STRUCTURE_DEPTH + 1)
        with self.assertRaises(strict_json.StrictJsonError) as caught:
            strict_json.loads(over_limit, source="deep.json")
        self.assertLess(len(str(caught.exception)), 200)

    def test_a_file_is_never_read_without_a_bound(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "big.json"
            path.write_bytes(b'{"value": "' + b"a" * 4096 + b'"}')
            with self.assertRaisesRegex(strict_json.StrictJsonError, "configured limit"):
                strict_json.load_path(path, max_bytes=1024)
            self.assertEqual(
                strict_json.load_path(path)["value"],
                "a" * 4096,
                "the house limit must not reject an ordinary file",
            )

    def test_accepts_valid_surrogate_pair_as_unicode_scalar(self) -> None:
        self.assertEqual(
            strict_json.loads(b'{"value": "\\ud83d\\ude00"}'),
            {"value": "😀"},
        )

    def test_both_render_clis_use_strict_loader(self) -> None:
        invalid_payloads = (
            '{"x": 1, "x": 2}',
            '{"x": Infinity}',
            '{"x": "\\udfff"}',
        )
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for index, payload in enumerate(invalid_payloads):
                source = root / f"bad-{index}.knowledge.json"
                source.write_text(payload, encoding="utf-8")
                with self.subTest(tool="markdown", payload=payload):
                    with contextlib.redirect_stderr(io.StringIO()):
                        self.assertNotEqual(
                            build_md.main([str(source), "--out", str(root / f"bad-{index}.md")]),
                            0,
                        )
                with self.subTest(tool="bundle", payload=payload):
                    with contextlib.redirect_stderr(io.StringIO()):
                        self.assertNotEqual(
                            build_bundle.main([str(source), "--out", str(root / f"bad-{index}.bundle")]),
                            0,
                        )

    def test_both_render_clis_reject_non_object_json_without_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "array.knowledge.json"
            source.write_text("[]\n", encoding="utf-8")
            markdown = root / "array.knowledge.md"
            bundle = root / "array.bundle"

            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(
                    build_md.main([str(source), "--out", str(markdown)]), 2
                )
                self.assertEqual(
                    build_bundle.main([str(source), "--out", str(bundle)]), 2
                )
            self.assertFalse(markdown.exists())
            self.assertFalse(bundle.exists())


class MarkdownCliSafetyTests(unittest.TestCase):
    def test_default_requires_knowledge_json_and_never_overwrites_input(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "graph.json"
            original = FIXTURE.read_bytes()
            source.write_bytes(original)
            with contextlib.redirect_stderr(io.StringIO()):
                result = build_md.main([str(source)])
            self.assertEqual(result, 2)
            self.assertEqual(source.read_bytes(), original)

    def test_explicit_output_must_be_distinct_markdown_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "graph.md"
            original = FIXTURE.read_bytes()
            source.write_bytes(original)
            with contextlib.redirect_stderr(io.StringIO()):
                result = build_md.main([str(source), "--out", str(source)])
            self.assertEqual(result, 2)
            self.assertEqual(source.read_bytes(), original)

            source = Path(temp) / "graph.knowledge.json"
            source.write_bytes(original)
            with contextlib.redirect_stderr(io.StringIO()):
                result = build_md.main([str(source), "--out", str(Path(temp) / "graph.txt")])
            self.assertEqual(result, 2)
            self.assertEqual(source.read_bytes(), original)

    def test_explicit_distinct_output_allows_nonstandard_input_name(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "graph.json"
            source.write_bytes(FIXTURE.read_bytes())
            output = Path(temp) / "rendered.md"
            with contextlib.redirect_stdout(io.StringIO()):
                result = build_md.main([str(source), "--out", str(output)])
            self.assertEqual(result, 0)
            self.assertTrue(output.is_file())
            self.assertEqual(source.read_bytes(), FIXTURE.read_bytes())


class ProvenanceRenderingTests(unittest.TestCase):
    def test_markdown_retains_spec_11_provenance_fields(self) -> None:
        doc = load_fixture()
        doc["evidence"][0]["excerpt_sha256"] = "b" * 64
        markdown = build_md.render(doc)

        expected_fragments = (
            "- Attribution basis: `source_explicit`",
            '"exact": "Alpha enables Beta in the German deployment profile."',
            '"prefix": "The guide states: "',
            '"suffix": " Two deployments were observed."',
            '**Excerpt SHA-256:** `' + "b" * 64 + '`',
            "- Review status: `reviewed`",
            '"source_period": "2026-Q3"',
            '"role": "jurisdiction"',
            '"basis": "source_explicit"',
            "- Kind: `inference`",
            "- Origin: `model_inferred`",
            "- Evidence: `evidence-alpha-beta`",
            "- Include in default retrieval: `false`",
            '"activity": "profile-condition-synthesis-v1"',
            '"review_status": "unreviewed"',
            "## Relationship provenance",
            "### Fact provenance",
            "**Source agents:**",
        )
        for expected in expected_fragments:
            with self.subTest(expected=expected):
                self.assertIn(expected, markdown)

    def test_bundle_markdown_retains_evidence_and_claim_context(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "context.bundle"
            build_bundle.build(load_fixture(), output)
            evidence = (output / "evidence.md").read_text(encoding="utf-8")
            concept = (output / "concepts" / "deployment" / "alpha.md").read_text(
                encoding="utf-8"
            )
            for expected in (
                '"exact": "Alpha enables Beta in the German deployment profile."',
                '"prefix": "The guide states: "',
                '"suffix": " Two deployments were observed."',
                "Attribution basis: `source_explicit`",
                "**Excerpt:**",
            ):
                self.assertIn(expected, evidence)
            for expected in (
                "review `reviewed`",
                "Claim claim-alpha-enables-beta temporal record",
                "Claim claim-alpha-enables-beta spatial context records",
                "Citation [1] selector",
            ):
                self.assertIn(expected, concept)

    def test_landing_excerpt_is_current_renderer_output_and_pages_match(self) -> None:
        root_page = (ROOT / "index.html").read_bytes()
        docs_page = (ROOT / "docs" / "index.html").read_bytes()
        self.assertEqual(root_page, docs_page)

        parser = _ExcerptParser()
        parser.feed(root_page.decode("utf-8"))
        self.assertTrue(parser.excerpt)
        self.assertIn(parser.excerpt, build_md.render(load_fixture()))

    def test_renderers_skip_malformed_optional_items_without_mutating_input(self) -> None:
        document = load_fixture()
        for collection in (
            "facts", "claims", "evidence", "assessments", "fact_conflicts", "chunks"
        ):
            document[collection] = [42]
        original = copy.deepcopy(document)

        markdown = build_md.render(document)
        self.assertIn("## Kernwissen", markdown)
        self.assertEqual(document, original)

        with tempfile.TemporaryDirectory() as temp:
            build_bundle.build(document, Path(temp) / "safe.bundle")
            self.assertTrue((Path(temp) / "safe.bundle" / "knowledge.json").is_file())
        self.assertEqual(document, original)

    def test_renderers_skip_malformed_nested_optional_values(self) -> None:
        document = load_fixture()
        document["nodes"][0]["citations"] = [{"n": 1, "source": {"bad": True}}]
        document["nodes"][0]["sources"] = [{"bad": True}]
        document["nodes"][0]["evidence"] = [{"bad": True}]
        document["nodes"][0]["spatial_contexts"] = [42]
        document["facts"][0]["source"] = {"bad": True}
        document["facts"][0]["evidence"] = [{"bad": True}]
        document["facts"][0]["temporal"] = "invalid"
        document["claims"][0]["node"] = ["invalid"]
        document["claims"][0]["evidence"] = [{"bad": True}]
        document["chunks"][0]["concepts"] = [{"bad": True}]
        document["chunks"][0]["evidence"] = [{"bad": True}]
        document["evidence"][0]["selector"] = "invalid"
        document["assessments"][0]["evidence"] = [{"bad": True}]
        document["fact_conflicts"][0]["facts"] = [{"bad": True}]
        original = copy.deepcopy(document)

        markdown = build_md.render(document)
        self.assertIn("## Kernwissen", markdown)
        self.assertEqual(document, original)

        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "safe.bundle"
            build_bundle.build(document, output)
            self.assertTrue((output / "knowledge.json").is_file())
        self.assertEqual(document, original)

    def test_yaml_frontmatter_escapes_newline_injection(self) -> None:
        document = load_fixture()
        document["metadata"]["title"] = "Safe title\n---\nattacker_key: true"
        document["nodes"][0]["label"] = "Safe label\n---\nattacker_node: true"

        markdown = build_md.render(document)
        self.assertIn('title: "Safe title\\n---\\nattacker_key: true"', markdown)
        self.assertNotIn("\ntitle: Safe title\n---\nattacker_key", markdown)

        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "safe.bundle"
            build_bundle.build(document, output)
            root_index = (output / "index.md").read_text(encoding="utf-8")
            concept = (output / "concepts" / "deployment" / "alpha.md").read_text(
                encoding="utf-8"
            )
            self.assertIn('title: "Safe title\\n---\\nattacker_key: true"', root_index)
            self.assertIn('label: "Safe label\\n---\\nattacker_node: true"', concept)

    def test_yaml_scalars_preserve_string_types_and_escape_unicode_line_breaks(self) -> None:
        document = load_fixture()
        document["metadata"]["title"] = "true"
        document["metadata"]["domain"] = "one,two\u0085three\u2028four\u2029five"

        markdown = build_md.render(document)
        self.assertIn('title: "true"', markdown)
        self.assertIn('domain: "one,two\\u0085three\\u2028four\\u2029five"', markdown)

        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "safe.bundle"
            build_bundle.build(document, output)
            root_index = (output / "index.md").read_text(encoding="utf-8")
            self.assertIn('title: "true"', root_index)
            self.assertIn('domain: "one,two\\u0085three\\u2028four\\u2029five"', root_index)

    def test_renderers_do_not_make_credential_bearing_urls_clickable(self) -> None:
        for unsafe_url in (
            "https://reader:secret@example.com/private",
            "https://example.com/download?access_token=secret",
        ):
            with self.subTest(url=unsafe_url):
                document = load_fixture()
                document["metadata"]["sources"][0]["url"] = unsafe_url
                document["nodes"][0]["citations"][0]["url"] = unsafe_url
                markdown = build_md.render(document)
                self.assertNotIn(f"]({unsafe_url})", markdown)

                with tempfile.TemporaryDirectory() as temp:
                    output = Path(temp) / "safe.bundle"
                    build_bundle.build(document, output)
                    for rendered in output.rglob("*.md"):
                        self.assertNotIn(f"]({unsafe_url})", rendered.read_text(encoding="utf-8"))


class BundleDefaultPathTests(unittest.TestCase):
    def test_default_bundle_path_has_bundle_suffix(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "sample.knowledge.json"
            source.write_bytes(FIXTURE.read_bytes())
            with contextlib.redirect_stdout(io.StringIO()):
                result = build_bundle.main([str(source)])
            self.assertEqual(result, 0)
            self.assertTrue((Path(temp) / "sample.bundle" / "knowledge.json").is_file())
            self.assertFalse((Path(temp) / "sample" / "knowledge.json").exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
