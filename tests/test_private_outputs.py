"""Derived confidential artifacts must remain owner-only even with a permissive umask."""
import contextlib
import io
import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import build_bundle
import build_md
import build_viewer
import evaluate_golden
import merge_knowledge


class PrivateOutputTests(unittest.TestCase):
    def test_all_derived_files_are_owner_only(self):
        doc = json.loads((ROOT / "tests/fixtures/minimal.knowledge.json").read_text())
        for mask in (0o022, 0o077):
            with self.subTest(umask=oct(mask)), tempfile.TemporaryDirectory() as temp:
                base = Path(temp)
                previous = os.umask(mask)
                try:
                    graph = base / "input.knowledge.json"
                    graph.write_text(json.dumps(doc))
                    graph.chmod(0o600)
                    with contextlib.redirect_stdout(io.StringIO()):
                        self.assertEqual(build_md.main([str(graph)]), 0)
                        self.assertEqual(build_viewer.main([str(graph)]), 0)
                        build_bundle.build(doc, base / "bundle")
                    merge_knowledge._atomic_write(base / "merged.json", b"{}")
                    merge_knowledge._atomic_create(base / "archive.json", b"{}")
                    evaluate_golden._atomic_report_write(base / "report.json", "{}", [graph])
                    files = [p for p in base.rglob("*") if p.is_file() and p != graph]
                    self.assertGreater(len(files), 6)
                    for path in files:
                        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600, str(path))
                finally:
                    os.umask(previous)
