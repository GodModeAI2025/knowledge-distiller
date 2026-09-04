"""Security and offline regression tests for the standalone graph viewer."""

import copy
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from html.parser import HTMLParser
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "tests" / "fixtures" / "minimal.knowledge.json"
BUILD_VIEWER = ROOT / "scripts" / "build_viewer.py"
VIEWER_JS = ROOT / "viewer" / "viz.js"
sys.path.insert(0, str(ROOT / "scripts"))

import build_viewer  # noqa: E402


class _AssetCollector(HTMLParser):
    """Collect network-capable static asset references from generated HTML."""

    def __init__(self):
        super().__init__()
        self.references = []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag in {"script", "img", "iframe", "audio", "video", "source"} and values.get("src"):
            self.references.append((tag, "src", values["src"]))
        if tag == "link" and values.get("href"):
            self.references.append((tag, "href", values["href"]))


def _chrome_binary():
    candidates = [
        shutil.which("google-chrome"),
        shutil.which("google-chrome-stable"),
        shutil.which("chromium"),
        shutil.which("chromium-browser"),
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    ]
    return next((candidate for candidate in candidates if candidate and Path(candidate).is_file()), None)


class TestViewerHardening(unittest.TestCase):
    def setUp(self):
        self.base = json.loads(FIXTURE.read_text(encoding="utf-8"))
        self.tempdir = tempfile.TemporaryDirectory(prefix="kd-viewer-test-")
        self.addCleanup(self.tempdir.cleanup)
        self.tmp = Path(self.tempdir.name)

    def build(self, document):
        source = self.tmp / "input.knowledge.json"
        output = self.tmp / "output.knowledge.html"
        source.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
        result = subprocess.run(
            [sys.executable, str(BUILD_VIEWER), str(source), "-o", str(output)],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=20,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(output.is_file())
        return output.read_text(encoding="utf-8"), output

    def malicious_document(self):
        document = copy.deepcopy(self.base)
        document["metadata"]["title"] = "</script><script>window.__viewerXss=1</script>\u2028title"
        document["metadata"]["unknown_future_field"] = {"kept": True}
        node = document["nodes"][0]
        node["definition"] = (
            '<img id="xss" src=x onerror="window.__viewerXss=1"> '
            "**safe** [bad](javascript:window.__viewerXss=1) "
            "[good](https://example.com/docs?q=viewer)"
        )
        node["citations"] = [
            {"n": 1, "source": "s1", "label": "Unsafe", "url": "javascript:window.__viewerXss=1"},
            {"n": 2, "source": "s1", "label": "Safe", "url": "https://example.com/source"},
        ]
        node["unknown_future_field"] = ["consumer", "must", "ignore"]
        return document

    def test_cli_rejects_duplicate_keys_nonfinite_numbers_and_surrogates(self):
        invalid_payloads = (
            ('{"metadata":{},"metadata":{}}', "duplicate object key"),
            ('{"value":NaN}', "non-finite number"),
            ('{"value":"\\ud800"}', "isolated Unicode surrogate"),
        )
        for index, (payload, expected) in enumerate(invalid_payloads):
            with self.subTest(payload=payload):
                source = self.tmp / f"invalid-{index}.knowledge.json"
                output = self.tmp / f"invalid-{index}.knowledge.html"
                source.write_text(payload, encoding="utf-8")
                result = subprocess.run(
                    [sys.executable, str(BUILD_VIEWER), str(source), "-o", str(output)],
                    cwd=ROOT,
                    text=True,
                    capture_output=True,
                    timeout=20,
                    check=False,
                )
                self.assertEqual(result.returncode, 1)
                self.assertIn(expected, result.stderr)
                self.assertFalse(output.exists())

    def test_programmatic_renderer_rejects_nonportable_json_values(self):
        with self.assertRaisesRegex(build_viewer.ViewerInputError, "JSON object"):
            build_viewer.render([])
        with self.assertRaisesRegex(ValueError, "Out of range float"):
            build_viewer.render({"value": float("nan")})
        with self.assertRaises(UnicodeEncodeError):
            build_viewer.render({"value": "\ud800"})

    def test_cli_rejects_non_object_root_without_output(self):
        source = self.tmp / "array.knowledge.json"
        output = self.tmp / "array.knowledge.html"
        source.write_text("[]\n", encoding="utf-8")
        result = subprocess.run(
            [sys.executable, str(BUILD_VIEWER), str(source), "-o", str(output)],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=20,
            check=False,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("graph root must be a JSON object", result.stderr)
        self.assertFalse(output.exists())

    def test_cli_never_overwrites_input_symlink_or_hardlink(self):
        source = self.tmp / "input.knowledge.json"
        original = FIXTURE.read_bytes()
        source.write_bytes(original)

        unsafe_targets = [source]
        symlink = self.tmp / "viewer-symlink.html"
        symlink.symlink_to(source)
        unsafe_targets.append(symlink)
        hardlink = self.tmp / "viewer-hardlink.html"
        os.link(source, hardlink)
        unsafe_targets.append(hardlink)

        for target in unsafe_targets:
            with self.subTest(target=target.name):
                result = subprocess.run(
                    [sys.executable, str(BUILD_VIEWER), str(source), "-o", str(target)],
                    cwd=ROOT,
                    text=True,
                    capture_output=True,
                    timeout=20,
                    check=False,
                )
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn("refusing viewer build", result.stderr)
                self.assertEqual(source.read_bytes(), original)

    def test_default_output_requires_knowledge_json(self):
        source = self.tmp / "input.json"
        original = FIXTURE.read_bytes()
        source.write_bytes(original)
        result = subprocess.run(
            [sys.executable, str(BUILD_VIEWER), str(source)],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=20,
            check=False,
        )
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("must end with .knowledge.json", result.stderr)
        self.assertEqual(source.read_bytes(), original)

    def test_generated_html_is_self_contained_and_bound_explicitly(self):
        html, _ = self.build(self.base)
        collector = _AssetCollector()
        collector.feed(html)

        self.assertEqual(collector.references, [])
        self.assertIn('id="kd-graph-data" type="application/json"', html)
        self.assertIn('JSON.parse(dataElement.textContent || "{}")', html)
        self.assertNotIn("const GRAPH", html)
        self.assertNotIn("window.GRAPH", html)
        self.assertNotIn("window.__KD_GRAPH__", html)
        self.assertNotIn("window.cytoscape", html)
        self.assertNotIn("window.marked", html)
        self.assertNotIn("unpkg.com", html)
        self.assertNotIn("cdn.jsdelivr.net", html)
        self.assertIn("default-src 'none'", html)
        self.assertIn("connect-src 'none'", html)
        self.assertNotRegex(html, r"(?i)@import\s+url|url\(\s*['\"]?https?://")

    def test_graph_data_keeps_json_semantics_for_proto_named_keys(self):
        document = copy.deepcopy(self.base)
        document["__proto__"] = {"metadata": {"title": "must remain inert data"}}
        html, _ = self.build(document)
        match = re.search(
            r'<script id="kd-graph-data" type="application/json">(.*?)</script>',
            html,
            flags=re.DOTALL,
        )
        self.assertIsNotNone(match)
        round_tripped = json.loads(match.group(1))
        self.assertEqual(round_tripped["metadata"]["title"], "Minimal Valid Graph")
        self.assertEqual(round_tripped["__proto__"]["metadata"]["title"], "must remain inert data")

    def test_inline_data_cannot_break_out_of_its_script(self):
        document = self.malicious_document()
        html, _ = self.build(document)

        self.assertNotIn("</script><script>window.__viewerXss=1</script>", html)
        self.assertIn(r"\u003c/script\u003e\u003cscript\u003ewindow.__viewerXss=1", html)
        self.assertNotIn("\u2028", html)
        self.assertIn(r"\u2028title", html)
        # Exactly the inert graph-data and viewer-code script elements remain.
        self.assertEqual(html.count("<script"), 2)
        self.assertEqual(html.count("</script>"), 2)

    def test_renderer_uses_safe_dom_and_an_explicit_url_allowlist(self):
        source = VIEWER_JS.read_text(encoding="utf-8")
        self.assertNotIn("innerHTML", source)
        self.assertNotIn("insertAdjacentHTML", source)
        self.assertNotIn("document.write", source)
        self.assertIn('parsed.protocol === "https:"', source)
        self.assertIn('parsed.protocol === "http:"', source)
        self.assertIn('parsed.protocol === "mailto:"', source)
        self.assertIn("raw\n     HTML is displayed as text", source)
        self.assertIn('rel = "noopener noreferrer"', source)
        self.assertIn('referrerPolicy = "no-referrer"', source)
        self.assertIn("META.conformance_score", source)
        self.assertIn('chip(conformance, "conformance", true)', source)
        self.assertNotIn('chip(META.quality_score, "quality"', source)

    @unittest.skipUnless(shutil.which("node"), "Node.js is needed for the JavaScript syntax check")
    def test_viewer_javascript_parses(self):
        result = subprocess.run(
            [shutil.which("node"), "--check", str(VIEWER_JS)],
            text=True,
            capture_output=True,
            timeout=20,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(shutil.which("node"), "Node.js is needed for the URL policy test")
    def test_url_policy_rejects_active_and_local_schemes(self):
        source = VIEWER_JS.read_text(encoding="utf-8")
        start = source.index("  function safeUrl")
        end = source.index("  function externalLink", start)
        function_source = source[start:end]
        values = [
            "https://example.com/path",
            "http://example.com/path",
            "mailto:reader@example.com",
            "javascript:alert(1)",
            "data:text/html,<script>alert(1)</script>",
            "file:///etc/passwd",
            "vbscript:msgbox(1)",
            "//example.com/protocol-relative",
            "https://example.com/\nheader-injection",
            "https://reader:secret@example.com/private",
            "https://example.com/download?access_token=secret",
        ]
        program = (
            'var window = {location: {href: "file:///tmp/viewer.html"}};\n'
            + function_source
            + "\nprocess.stdout.write(JSON.stringify("
            + json.dumps(values)
            + ".map(safeUrl)));"
        )
        result = subprocess.run(
            [shutil.which("node"), "-e", program],
            text=True,
            capture_output=True,
            timeout=20,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        resolved = json.loads(result.stdout)
        self.assertEqual(resolved[:3], values[:3])
        self.assertEqual(resolved[3:], [None] * 8)

    @unittest.skipUnless(shutil.which("node"), "Node.js is needed for the Markdown DOM test")
    def test_markdown_parser_does_not_treat_identifier_underscores_as_delimiters(self):
        source = VIEWER_JS.read_text(encoding="utf-8")
        start = source.index("  function text")
        end = source.index("  function appendMarkdown", start)
        function_source = source[start:end]
        payload = (
            '<img id="xss" src=x onerror="window.__viewerXss=1"> '
            "**safe** [bad](javascript:window.__viewerXss=1) "
            "[good](https://example.com/docs?q=viewer)"
        )
        program = r"""
function TestNode(tag, value) {
  this.tag = tag;
  this.value = value === undefined ? null : String(value);
  this.children = [];
}
TestNode.prototype.appendChild = function (child) { this.children.push(child); return child; };
Object.defineProperty(TestNode.prototype, "textContent", {
  set: function (value) { this.children = [new TestNode("#text", value)]; }
});
var document = {
  createElement: function (tag) { return new TestNode(tag); },
  createElementNS: function (_namespace, tag) { return new TestNode(tag); },
  createTextNode: function (value) { return new TestNode("#text", value); }
};
var window = {location: {href: "file:///tmp/viewer.html"}};
""" + function_source + "\n" + (
            "var root = new TestNode('root');\n"
            "appendInlineMarkdown(root, " + json.dumps(payload) + ");\n"
            "process.stdout.write(JSON.stringify(root));"
        )
        result = subprocess.run(
            [shutil.which("node"), "-e", program],
            text=True,
            capture_output=True,
            timeout=20,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        tree = json.loads(result.stdout)

        def descendants(node):
            yield node
            for child in node.get("children", []):
                yield from descendants(child)

        nodes = list(descendants(tree))
        strong = [node for node in nodes if node.get("tag") == "strong"]
        self.assertEqual(len(strong), 1)
        self.assertEqual(strong[0]["children"][0]["value"], "safe")
        self.assertFalse(any(node.get("tag") == "img" for node in nodes))
        anchors = [node for node in nodes if node.get("tag") == "a"]
        self.assertEqual(len(anchors), 1)
        self.assertEqual(anchors[0]["href"], "https://example.com/docs?q=viewer")
        plain_text = "".join(
            node.get("value") or "" for node in nodes if node.get("tag") == "#text"
        )
        self.assertIn('<img id="xss"', plain_text)
        self.assertIn("bad", plain_text)

    @unittest.skipUnless(
        _chrome_binary() and os.environ.get("KD_BROWSER_TEST") == "1",
        "set KD_BROWSER_TEST=1 to run the Chrome/Chromium security smoke test",
    )
    def test_browser_renders_markdown_without_activating_untrusted_html_or_urls(self):
        html, output = self.build(self.malicious_document())
        probe = """<script>
window.__kgSelect("alpha");
document.body.dataset.viewerReady = String(document.querySelectorAll(".graph-node").length);
document.body.dataset.viewerXss = String(window.__viewerXss || 0);
</script>"""
        output.write_text(html.replace("</body>", probe + "\n</body>"), encoding="utf-8")

        profile = self.tmp / "chrome-profile"
        command = [
            _chrome_binary(),
            "--headless=new",
            "--disable-gpu",
            "--no-first-run",
            "--no-default-browser-check",
            "--user-data-dir=" + str(profile),
            "--dump-dom",
            output.resolve().as_uri(),
        ]
        try:
            result = subprocess.run(
                command,
                text=True,
                capture_output=True,
                timeout=40,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            # Some macOS Chrome builds emit the complete --dump-dom result and
            # then remain alive in CVDisplayLink teardown.  Accept that host
            # quirk only when a complete HTML document was actually captured;
            # every security assertion below still runs against the real DOM.
            rendered = exc.stdout or ""
            if isinstance(rendered, bytes):
                rendered = rendered.decode("utf-8", errors="replace")
            stderr = exc.stderr or ""
            if isinstance(stderr, bytes):
                stderr = stderr.decode("utf-8", errors="replace")
            self.assertIn("</html>", rendered.lower(), stderr)
        else:
            self.assertEqual(result.returncode, 0, result.stderr)
            rendered = result.stdout
        self.assertIn('data-viewer-ready="3"', rendered)
        self.assertIn('data-viewer-xss="0"', rendered)
        self.assertNotIn('<img id="xss"', rendered)
        self.assertNotIn('href="javascript:', rendered.lower())
        self.assertIn("&lt;img id=\"xss\"", rendered)
        self.assertIn("<strong>safe</strong>", rendered)
        self.assertIn('href="https://example.com/docs?q=viewer"', rendered)
        self.assertIn('href="https://example.com/source"', rendered)


if __name__ == "__main__":
    unittest.main(verbosity=2)
