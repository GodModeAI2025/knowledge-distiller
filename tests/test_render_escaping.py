"""Untrusted document text must not gain structure or script context in derived output.

Every string in a ``.knowledge.json`` comes from a document the distiller was pointed
at, so each of these renders a graph carrying a payload and asserts that the payload
came out as text rather than as Markdown, HTML, a wikilink, a diagram or JavaScript.
"""

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import build_graph  # noqa: E402
import build_md  # noqa: E402
import build_viewer  # noqa: E402


def graph(**overrides):
    doc = {
        "metadata": {
            "title": "t",
            "distiller_version": "4.0",
            "distillation_date": "2026-01-01",
            "sources": [{"id": "s1", "file": "doc.txt", "type": "txt"}],
        },
        "clusters": [{"id": "c1", "label": "C", "concepts": ["n1", "n2"]}],
        "nodes": [
            {"id": "n1", "label": "A", "cluster": "c1", "confidence": "high",
             "definition": "d", "relevance": "r", "statements": []},
            {"id": "n2", "label": "B", "cluster": "c1", "confidence": "high",
             "definition": "d", "relevance": "r", "statements": []},
        ],
        "edges": [{"source": "n1", "target": "n2", "type": "supports"}],
    }
    doc.update(overrides)
    return doc


def render(doc):
    return build_md.render(build_md.consumer_safe_copy(doc))


def _data_payload(html):
    """The inlined graph JSON, without the surrounding script tag."""
    after_tag = html.split('id="kd-graph-data"', 1)[1].split(">", 1)[1]
    return after_tag.split("</script>", 1)[0]


class MarkdownEscaping(unittest.TestCase):
    def test_source_label_cannot_close_the_link_text(self):
        doc = graph()
        doc["metadata"]["sources"] = [{
            "id": "s1", "type": "txt", "url": "https://ok.example/p",
            "file": "x](javascript:alert(1))[y",
        }]
        # The frontmatter carries the same string as an inert YAML scalar, so the
        # assertion has to be about the Quellen heading, which is the Markdown context.
        heading = next(l for l in render(doc).splitlines() if l.startswith("### [1]"))
        self.assertEqual(
            heading, "### [1] [x\\](javascript:alert(1))\\[y](https://ok.example/p)")

    def test_safe_link_target_cannot_close_the_parentheses(self):
        url = "https://a.example/x)[click](javascript:alert(1))"
        self.assertEqual(build_md.safe_link(url), url)  # unchanged: still an https URL
        rendered = build_md.md_link("label", build_md.safe_link(url))
        self.assertNotIn(")[click](javascript:", rendered)
        self.assertIn("%29", rendered)

    def test_frontmatter_url_goes_through_the_scheme_allowlist(self):
        doc = graph()
        doc["metadata"]["sources"] = [
            {"id": "s1", "type": "txt", "file": "f", "url": "javascript:alert(1)"},
        ]
        out = render(doc)
        self.assertNotIn("\n    url: \"javascript:", out)
        # The value is kept -- it is evidence about the source -- just not as a link.
        self.assertIn("url_unsafe:", out)

    def test_frontmatter_keeps_an_allowed_url_under_the_normal_key(self):
        doc = graph()
        doc["metadata"]["sources"] = [
            {"id": "s1", "type": "txt", "file": "f", "url": "https://ok.example/p"},
        ]
        out = render(doc)
        self.assertIn('    url: "https://ok.example/p"', out)
        self.assertNotIn("url_unsafe", out)

    def test_prose_cannot_inject_inline_html(self):
        doc = graph()
        doc["nodes"][0]["definition"] = "<img src=x onerror=alert(1)>"
        out = render(doc)
        self.assertNotIn("<img", out)
        self.assertIn("&lt;img", out)

    def test_prose_cannot_forge_a_wikilink(self):
        doc = graph()
        doc["nodes"][0]["relevance"] = "see [[Someone Else's Private Page]]"
        out = render(doc)
        self.assertNotIn("[[Someone Else's Private Page]]", out)

    def test_a_note_cannot_leave_its_blockquote(self):
        doc = graph()
        doc["nodes"][0]["note"] = "quiet\n\n# FORGED HEADING\n"
        out = render(doc)
        self.assertNotIn("\n# FORGED HEADING", out)

    def test_a_label_cannot_open_a_heading_of_its_own(self):
        doc = graph()
        doc["nodes"][0]["label"] = "A\n\n## FORGED SECTION"
        out = render(doc)
        self.assertNotIn("\n## FORGED SECTION", out)

    def test_a_statement_cannot_break_out_of_its_list_item(self):
        doc = graph()
        doc["nodes"][0]["statements"] = ["ok\n\n### FORGED", "plain"]
        out = render(doc)
        self.assertNotIn("\n### FORGED", out)

    def test_a_table_cell_cannot_inject_html_or_extra_columns(self):
        doc = graph(facts=[{
            "id": "f1", "source": "s1", "concept": "n1",
            "statement": "<b>x</b> | extra | columns", "value": "1",
            "confidence": "high", "origin": "source",
        }])
        out = render(doc)
        self.assertNotIn("<b>x</b>", out)
        self.assertIn("\\|", out)

    def test_an_identifier_cannot_close_its_code_span(self):
        # A code span renders its contents literally, so keeping the span closed is the
        # whole defense: the HTML below stays inert text as long as the backticks that
        # would end it early are gone.
        doc = graph()
        doc["nodes"][0]["sources"] = ["s1` <img src=x onerror=alert(1)> `"]
        line = next(l for l in render(doc).splitlines() if l.startswith("📚 Sources:"))
        self.assertEqual(line.count("`"), 2)


class MermaidEscaping(unittest.TestCase):
    def test_a_label_cannot_close_the_mermaid_fence(self):
        doc = graph()
        doc["nodes"][0]["label"] = "A\n```\n\n# FORGED FROM MERMAID\n"
        out = build_graph.render_mermaid(doc)
        self.assertEqual(out.count("```"), 2)  # the fence, opened and closed, and nothing else
        self.assertNotIn("\n# FORGED FROM MERMAID", out)

    def test_a_label_cannot_break_the_node_shape(self):
        self.assertNotIn("[", build_graph.mermaid_label('a["b"] --> c'))
        self.assertNotIn('"', build_graph.mermaid_label('a["b"] --> c'))


class ViewerTokenSubstitution(unittest.TestCase):
    """A graph value that happens to spell a template token must stay a value."""

    def test_a_graph_value_cannot_pull_in_the_viewer_javascript(self):
        doc = graph()
        doc["nodes"][0]["definition"] = build_viewer.TOKEN_JS
        html = build_viewer.render(doc)
        js = (ROOT / "viewer" / "viz.js").read_text(encoding="utf-8")
        # viz.js is inlined exactly once, in its own script block -- not a second time
        # inside the data payload.
        self.assertEqual(html.count(js[:200]), 1)
        payload = _data_payload(html)
        self.assertIn(build_viewer.TOKEN_JS, payload)
        self.assertNotIn(js[:200], payload)

    def test_every_token_survives_as_data(self):
        doc = graph()
        doc["nodes"][0]["definition"] = (
            build_viewer.TOKEN_JS + build_viewer.TOKEN_CSS + build_viewer.TOKEN_DATA
        )
        html = build_viewer.render(doc)
        decoded = json.loads(_data_payload(html))
        self.assertEqual(
            decoded["nodes"][0]["definition"],
            build_viewer.TOKEN_JS + build_viewer.TOKEN_CSS + build_viewer.TOKEN_DATA,
        )


if __name__ == "__main__":
    unittest.main()
