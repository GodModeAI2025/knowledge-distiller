"""Focused tests for the deterministic, local-only source adapter."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import extract_source as es  # noqa: E402


CONTENT_TYPES = b"""<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml"
    ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>"""


def word_document(paragraphs):
    body = []
    for paragraph in paragraphs:
        if isinstance(paragraph, tuple) and paragraph[0] == "instruction":
            body.append(
                "<w:p><w:r><w:instrText>" + paragraph[1]
                + "</w:instrText><w:t>" + paragraph[2] + "</w:t></w:r></w:p>"
            )
        else:
            body.append("<w:p><w:r><w:t>" + paragraph + "</w:t></w:r></w:p>")
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:body>" + "".join(body) + "</w:body></w:document>"
    ).encode("utf-8")


def word_header(value):
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<w:hdr xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:p><w:r><w:t>" + value + "</w:t></w:r></w:p></w:hdr>"
    ).encode("utf-8")


class SourceAdapterCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="kd-extract-test-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.input_root = self.base / "inputs"
        self.outside = self.base / "outside"
        self.input_root.mkdir()
        self.outside.mkdir()

    def write(self, relative, data):
        path = self.input_root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(data, str):
            path.write_text(data, encoding="utf-8")
        else:
            path.write_bytes(data)
        return path

    def make_docx(self, relative="sample.docx", *, document=None, extra=None):
        path = self.input_root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("[Content_Types].xml", CONTENT_TYPES)
            archive.writestr(
                "word/document.xml",
                document or word_document(["Hello DOCX", "Second paragraph"]),
            )
            for name, value in (extra or {}).items():
                archive.writestr(name, value)
        return path

    def test_text_is_normalized_and_segment_ids_are_content_stable(self):
        raw = b"\xef\xbb\xbfFirst\r\nline\r\n\r\nSecond paragraph\r\n"
        self.write("folder/notes.txt", raw)
        first = es.extract_source(
            "folder/notes.txt", input_root=self.input_root, max_bytes=1024
        )
        second = es.extract_source(
            self.input_root / "folder/notes.txt", input_root=self.input_root, max_bytes=1024
        )

        self.assertEqual(first, second)
        self.assertEqual(first["source"]["file"], "folder/notes.txt")
        self.assertEqual(first["source"]["logical_filename"], "folder/notes.txt")
        self.assertEqual(first["source"]["mime_type"], "text/plain")
        self.assertEqual(first["source"]["content_sha256"], hashlib.sha256(raw).hexdigest())
        self.assertEqual([item["text"] for item in first["segments"]], ["First\nline", "Second paragraph"])
        self.assertEqual(first["segments"][0]["selector"]["type"], "TextPositionSelector")
        self.assertEqual(first["segments"][0]["selectors"][1]["exact"], "First\nline")
        self.assertEqual(first["segments"][0]["locator"], "chars=0-10")

        self.write("renamed.txt", raw)
        renamed = es.extract_source("renamed.txt", input_root=self.input_root)
        self.assertEqual(
            [item["id"] for item in first["segments"]],
            [item["id"] for item in renamed["segments"]],
        )

    def test_markdown_uses_exact_canonical_text_positions(self):
        self.write("guide.md", "# Heading\n\nA **Markdown** paragraph.")
        result = es.extract_source("guide.md", input_root=self.input_root)
        self.assertEqual(result["source"]["type"], "md")
        self.assertEqual([segment["text"] for segment in result["segments"]], [
            "# Heading", "A **Markdown** paragraph."
        ])
        for segment in result["segments"]:
            selector = segment["selector"]
            self.assertEqual(selector["type"], "TextPositionSelector")
            start, end = selector["start"], selector["end"]
            canonical = "# Heading\n\nA **Markdown** paragraph."
            self.assertEqual(canonical[start:end], segment["text"])

    def test_html_extracts_visible_blocks_but_never_script_style_or_attributes(self):
        self.write(
            "page.html",
            """<!doctype html><html><head><title>Example</title>
<style>body{display:none}</style><script>window.exfiltrate()</script></head>
<body><h1 id="intro" onclick="run()">Hello &amp; <em>world</em></h1>
<p>Safe <strong>paragraph</strong>.</p><img src=x onerror="run()"></body></html>""",
        )
        result = es.extract_source("page.html", input_root=self.input_root)
        texts = [segment["text"] for segment in result["segments"]]
        self.assertEqual(texts, ["Example", "Hello & world", "Safe paragraph."])
        self.assertNotIn("exfiltrate", json.dumps(result))
        self.assertNotIn("onclick", json.dumps(result))
        intro = result["segments"][1]
        self.assertEqual(intro["selector"], {"type": "FragmentSelector", "fragment": "#intro"})
        self.assertEqual(intro["selectors"][-1], {
            "type": "TextQuoteSelector", "exact": "Hello & world"
        })

        self.write("deep.html", "<div>" * 513 + "text" + "</div>" * 513)
        with self.assertRaisesRegex(es.MalformedSourceError, "nesting exceeds"):
            es.extract_source("deep.html", input_root=self.input_root)

    def test_csv_and_tsv_use_row_order_and_csv_selectors_without_formula_execution(self):
        self.write("table.csv", 'name,value,formula\nalpha,"1,200",=1+1\n\n')
        csv_result = es.extract_source("table.csv", input_root=self.input_root)
        self.assertEqual([segment["locator"] for segment in csv_result["segments"]], [
            "data!A1:C1", "data!A2:C2"
        ])
        self.assertEqual(csv_result["segments"][1]["text"], "alpha\t1,200\t=1+1")
        self.assertEqual(csv_result["segments"][1]["selector"], {
            "type": "CsvSelector", "sheet": "data", "cell_range": "A2:C2"
        })

        self.write("table.tsv", "a\tb\n1\t2\n")
        tsv_result = es.extract_source("table.tsv", input_root=self.input_root)
        self.assertEqual(tsv_result["source"]["type"], "tsv")
        self.assertEqual(tsv_result["segments"][1]["text"], "1\t2")

    def test_json_walk_is_pointer_sorted_and_escapes_pointer_tokens(self):
        self.write(
            "data.json",
            '{"z":1,"a/b":{"~key":"value"},"arr":[true,null],"empty":{}}',
        )
        result = es.extract_source("data.json", input_root=self.input_root)
        pointers = [segment["selector"]["json_pointer"] for segment in result["segments"]]
        self.assertEqual(pointers, ["/a~1b/~0key", "/arr/0", "/arr/1", "/empty", "/z"])
        self.assertEqual([segment["text"] for segment in result["segments"]], [
            "value", "true", "null", "{}", "1"
        ])

    def test_json_duplicate_keys_and_nonstandard_constants_are_rejected(self):
        self.write("duplicate.json", '{"a":1,"a":2}')
        with self.assertRaisesRegex(es.MalformedSourceError, "duplicate object key"):
            es.extract_source("duplicate.json", input_root=self.input_root)

        self.write("nan.json", '{"value":NaN}')
        with self.assertRaisesRegex(es.MalformedSourceError, "non-finite number"):
            es.extract_source("nan.json", input_root=self.input_root)

        self.write("overflow.json", '{"value":1e999}')
        with self.assertRaisesRegex(es.MalformedSourceError, "non-finite number"):
            es.extract_source("overflow.json", input_root=self.input_root)

        self.write("huge-int.json", '{"value":' + "9" * 4_301 + "}")
        with self.assertRaisesRegex(es.MalformedSourceError, "safe numeric length"):
            es.extract_source("huge-int.json", input_root=self.input_root)

        self.write("surrogate.json", '{"value":"\\ud800"}')
        with self.assertRaisesRegex(es.MalformedSourceError, "Unicode surrogate"):
            es.extract_source("surrogate.json", input_root=self.input_root)

    def test_segment_and_serialized_output_limits_are_hard_for_every_format(self):
        self.write("wide.csv", "value\n" + "x" * 11 + "\n")
        with self.assertRaisesRegex(es.InputTooLargeError, "configured limit is 10"):
            es.extract_source(
                "wide.csv", input_root=self.input_root, max_segment_chars=10
            )

        self.write("many.csv", "a\n1\n2\n")
        with self.assertRaisesRegex(es.InputTooLargeError, "2-segment limit"):
            es.extract_source("many.csv", input_root=self.input_root, max_segments=2)

        self.write("split.txt", "one two three four")
        split = es.extract_source(
            "split.txt", input_root=self.input_root, max_segment_chars=8
        )
        self.assertTrue(split["segments"])
        self.assertTrue(all(len(item["text"]) <= 8 for item in split["segments"]))

        self.write("output.txt", "small source")
        with self.assertRaisesRegex(es.OutputTooLargeError, "serialized JSON exceeds"):
            es.extract_source(
                "output.txt", input_root=self.input_root, max_output_bytes=64
            )

        for name, value, maximum in (
            ("max_bytes", es.DEFAULT_MAX_INPUT_BYTES + 1, es.DEFAULT_MAX_INPUT_BYTES),
            ("max_segments", es.DEFAULT_MAX_SEGMENTS + 1, es.DEFAULT_MAX_SEGMENTS),
            (
                "max_segment_chars",
                es.DEFAULT_MAX_SEGMENT_CHARS + 1,
                es.DEFAULT_MAX_SEGMENT_CHARS,
            ),
            (
                "max_output_bytes",
                es.DEFAULT_MAX_OUTPUT_BYTES + 1,
                es.DEFAULT_MAX_OUTPUT_BYTES,
            ),
        ):
            with self.subTest(limit=name):
                with self.assertRaisesRegex(ValueError, f"between 1 and {maximum}"):
                    es.extract_source(
                        "output.txt", input_root=self.input_root, **{name: value}
                    )

    def test_output_limit_applies_to_the_selected_serialization(self):
        payload = {f"key-{index}": "x" * 120 for index in range(80)}
        self.write("shape.json", json.dumps(payload))
        document = es.extract_source("shape.json", input_root=self.input_root)
        sizes = {
            "json": len(es.render_json(document)),
            "jsonl": len(es.render_jsonl(document)),
        }
        self.assertNotEqual(sizes["json"], sizes["jsonl"])
        smaller = min(sizes, key=sizes.get)
        larger = max(sizes, key=sizes.get)
        limit = (sizes[smaller] + sizes[larger]) // 2

        selected = es.extract_source(
            "shape.json",
            input_root=self.input_root,
            output_format=smaller,
            max_output_bytes=limit,
        )
        renderer = es.render_jsonl if smaller == "jsonl" else es.render_json
        self.assertLessEqual(len(renderer(selected, limit)), limit)
        with self.assertRaises(es.OutputTooLargeError):
            es.extract_source(
                "shape.json",
                input_root=self.input_root,
                output_format=larger,
                max_output_bytes=limit,
            )

    def test_docx_reads_only_declared_wordprocessingml_text_parts(self):
        self.make_docx(
            document=word_document([
                "Hello DOCX",
                ("instruction", "DDEAUTO malicious command", "Visible result"),
            ]),
            extra={"word/header1.xml": word_header("Header text")},
        )
        result = es.extract_source("sample.docx", input_root=self.input_root)
        self.assertEqual(result["source"]["type"], "docx")
        self.assertEqual([segment["text"] for segment in result["segments"]], [
            "Hello DOCX", "Visible result", "Header text"
        ])
        self.assertNotIn("DDEAUTO", json.dumps(result))
        self.assertEqual(result["segments"][0]["selector"], {
            "type": "FragmentSelector", "fragment": "word/document.xml#paragraph=1"
        })
        self.assertEqual(result["segments"][0]["selectors"][1], {
            "type": "TextQuoteSelector", "exact": "Hello DOCX"
        })

    def test_docx_rejects_macros_unsafe_members_entities_and_zip_bombs(self):
        self.make_docx("macro.docx", extra={"word/vbaProject.bin": b"not executed"})
        with self.assertRaisesRegex(es.UnsupportedSourceError, "macro"):
            es.extract_source("macro.docx", input_root=self.input_root)

        self.make_docx("traversal.docx", extra={"../escape": b"never extracted"})
        with self.assertRaisesRegex(es.MalformedSourceError, "unsafe archive member"):
            es.extract_source("traversal.docx", input_root=self.input_root)

        entity_xml = b'<!DOCTYPE x [<!ENTITY e "boom">]>' + word_document(["&e;"])
        self.make_docx("entity.docx", document=entity_xml)
        with self.assertRaisesRegex(es.MalformedSourceError, "DTD/entity"):
            es.extract_source("entity.docx", input_root=self.input_root)

        self.make_docx("bomb.docx", document=word_document(["A" * 20_000]))
        bomb_size = (self.input_root / "bomb.docx").stat().st_size
        self.assertLess(bomb_size, 4_000)
        with self.assertRaisesRegex(es.InputTooLargeError, "expands beyond"):
            es.extract_source("bomb.docx", input_root=self.input_root, max_bytes=4_000)

    def test_paths_urls_limits_binary_pdf_and_encoding_are_rejected(self):
        outside_file = self.outside / "outside.txt"
        outside_file.write_text("outside", encoding="utf-8")
        with self.assertRaises(es.PathSafetyError):
            es.extract_source("../outside/outside.txt", input_root=self.input_root)
        with self.assertRaisesRegex(es.UnsupportedSourceError, "URLs"):
            es.extract_source("https://example.com/file.txt", input_root=self.input_root)

        link = self.input_root / "escape.txt"
        try:
            link.symlink_to(outside_file)
        except (OSError, NotImplementedError):
            pass
        else:
            with self.assertRaises(es.PathSafetyError):
                es.extract_source("escape.txt", input_root=self.input_root)

        self.write("large.txt", b"12345")
        with self.assertRaises(es.InputTooLargeError):
            es.extract_source("large.txt", input_root=self.input_root, max_bytes=4)
        self.write("binary.txt", b"abc\x00def")
        with self.assertRaisesRegex(es.UnsupportedSourceError, "binary"):
            es.extract_source("binary.txt", input_root=self.input_root)
        self.write("invalid.txt", b"\xff\xfe")
        with self.assertRaises(es.SourceEncodingError):
            es.extract_source("invalid.txt", input_root=self.input_root)
        self.write("paper.pdf", b"%PDF-1.7\n")
        with self.assertRaisesRegex(es.UnsupportedSourceError, "PDF"):
            es.extract_source("paper.pdf", input_root=self.input_root)
        self.write("image.png", b"\x89PNG")
        with self.assertRaisesRegex(es.UnsupportedSourceError, "unsupported file type"):
            es.extract_source("image.png", input_root=self.input_root)

    def test_path_swap_after_open_reads_only_the_already_open_descriptor(self):
        inside = self.write("swap.txt", "trusted inside")
        outside = self.outside / "outside.txt"
        outside.write_text("untrusted outside", encoding="utf-8")
        held = self.input_root / "opened-original.txt"
        original_reader = es._read_fd_bounded

        def swap_then_read(fd, max_bytes):
            inside.rename(held)
            try:
                inside.symlink_to(outside)
            except (OSError, NotImplementedError):
                self.skipTest("symlinks are unavailable")
            return original_reader(fd, max_bytes)

        with mock.patch.object(es, "_read_fd_bounded", side_effect=swap_then_read):
            result = es.extract_source("swap.txt", input_root=self.input_root)
        self.assertEqual([segment["text"] for segment in result["segments"]], ["trusted inside"])
        self.assertNotIn("untrusted outside", json.dumps(result))

    def test_input_root_swap_before_anchor_open_is_rejected(self):
        self.write("root-swap.txt", "trusted inside")
        (self.outside / "root-swap.txt").write_text(
            "untrusted outside", encoding="utf-8"
        )
        moved_root = self.base / "inputs-before-swap"
        original_open = es._open_component
        swapped = False

        def swap_root_component(name, *, directory_fd, directory):
            nonlocal swapped
            if name == self.input_root.name and directory and not swapped:
                swapped = True
                self.input_root.rename(moved_root)
                try:
                    self.input_root.symlink_to(
                        self.outside, target_is_directory=True
                    )
                except (OSError, NotImplementedError):
                    self.skipTest("symlinks are unavailable")
            return original_open(
                name, directory_fd=directory_fd, directory=directory
            )

        with mock.patch.object(
            es, "_open_component", side_effect=swap_root_component
        ):
            with self.assertRaises(es.PathSafetyError):
                es.extract_source("root-swap.txt", input_root=self.input_root)

    def test_json_jsonl_and_cli_outputs_are_deterministic(self):
        self.write("input.txt", "One.\n\nTwo.")
        document = es.extract_source("input.txt", input_root=self.input_root)
        self.assertEqual(es.render_json(document), es.render_json(document))
        json.loads(es.render_json(document))

        lines = es.render_jsonl(document).decode("utf-8").splitlines()
        records = [json.loads(line) for line in lines]
        self.assertEqual(records[0]["record_type"], "source")
        self.assertEqual([record["record_type"] for record in records[1:]], ["segment", "segment"])
        self.assertEqual(records[1]["source_id"], document["source"]["id"])

        command = [
            sys.executable,
            str(SCRIPTS / "extract_source.py"),
            "input.txt",
            "--input-root",
            str(self.input_root),
            "--format",
            "jsonl",
        ]
        first = subprocess.run(command, capture_output=True, check=False, timeout=20)
        second = subprocess.run(command, capture_output=True, check=False, timeout=20)
        self.assertEqual(first.returncode, 0, first.stderr.decode("utf-8"))
        self.assertEqual(first.stdout, second.stdout)
        self.assertNotIn(str(self.input_root).encode("utf-8"), first.stdout)

    def test_cli_rejection_is_bounded_and_does_not_emit_partial_json(self):
        self.write("bad.pdf", b"%PDF-1.4")
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPTS / "extract_source.py"),
                "bad.pdf",
                "--input-root",
                str(self.input_root),
            ],
            capture_output=True,
            check=False,
            timeout=20,
        )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, b"")
        self.assertIn(b"error [unsupported_source]", result.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
