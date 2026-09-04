"""Security regression tests for bundle path planning and output writes."""
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

import build_bundle  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "minimal.knowledge.json"


def load_fixture():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


class TestBundlePathSecurity(unittest.TestCase):
    def test_safe_fixture_ids_keep_existing_paths_and_links(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "bundle"
            build_bundle.build(load_fixture(), out)

            self.assertTrue((out / "concepts" / "c1" / "alpha.md").is_file())
            self.assertTrue((out / "concepts" / "c2" / "gamma.md").is_file())
            self.assertIn("(c1/index.md)", (out / "concepts" / "index.md").read_text())
            self.assertIn("(alpha.md)", (out / "concepts" / "c1" / "index.md").read_text())

    def test_traversal_absolute_and_separator_ids_are_encoded_inside_root(self):
        unsafe_ids = ("../../outside", "/absolute/path", "nested/name", r"nested\\name")
        for unsafe_id in unsafe_ids:
            with self.subTest(identifier=unsafe_id), tempfile.TemporaryDirectory() as tmp:
                doc = load_fixture()
                doc["clusters"][0]["id"] = unsafe_id
                for node in doc["nodes"]:
                    if node["cluster"] == "c1":
                        node["cluster"] = unsafe_id
                out = Path(tmp) / "bundle"

                build_bundle.build(doc, out)

                dirs = sorted(p.name for p in (out / "concepts").iterdir() if p.is_dir())
                encoded = build_bundle._hashed_component(unsafe_id, "c")
                self.assertIn(encoded, dirs)
                self.assertTrue((out / "concepts" / encoded / "alpha.md").is_file())
                self.assertFalse((Path(tmp) / "outside").exists())

    def test_reserved_node_id_cannot_overwrite_cluster_index(self):
        doc = load_fixture()
        doc["nodes"][0]["id"] = "index"
        doc["edges"][0]["source"] = "index"
        doc["chunks"][0]["concepts"] = ["index"]
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "bundle"
            build_bundle.build(doc, out)

            cdir = out / "concepts" / "c1"
            encoded = build_bundle._hashed_component("index", "n") + ".md"
            self.assertTrue((cdir / "index.md").read_text().startswith("# Cluster One"))
            self.assertTrue((cdir / encoded).is_file())
            self.assertIn(f"({encoded})", (cdir / "index.md").read_text())

    def test_traversal_and_absolute_node_ids_are_encoded_inside_cluster(self):
        unsafe_ids = ("../outside", "/absolute/node")
        for unsafe_id in unsafe_ids:
            with self.subTest(identifier=unsafe_id), tempfile.TemporaryDirectory() as tmp:
                doc = load_fixture()
                doc["nodes"][0]["id"] = unsafe_id
                doc["edges"][0]["source"] = unsafe_id
                doc["chunks"][0]["concepts"] = [unsafe_id]
                out = Path(tmp) / "bundle"

                build_bundle.build(doc, out)

                encoded = build_bundle._hashed_component(unsafe_id, "n") + ".md"
                cdir = out / "concepts" / "c1"
                self.assertTrue((cdir / encoded).is_file())
                self.assertIn(f"({encoded})", (cdir / "index.md").read_text())
                self.assertFalse((Path(tmp) / "outside.md").exists())

    def test_case_collisions_get_distinct_deterministic_components(self):
        first = build_bundle._path_components(["Area", "area"], kind="c")
        second = build_bundle._path_components(["area", "Area"], kind="c")

        self.assertEqual(first["Area"], second["Area"])
        self.assertEqual(first["area"], second["area"])
        self.assertNotEqual(first["Area"].casefold(), first["area"].casefold())
        self.assertTrue(first["Area"].startswith("kd-bundle-c-"))

    def test_duplicate_ids_fail_before_writing(self):
        doc = load_fixture()
        doc["clusters"].append(copy.deepcopy(doc["clusters"][0]))
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "bundle"
            with self.assertRaisesRegex(build_bundle.BundleSecurityError, "duplicate cluster ID"):
                build_bundle.build(doc, out)
            self.assertFalse(out.exists())

    @unittest.skipUnless(hasattr(Path, "symlink_to"), "symlinks unavailable")
    def test_symlinked_concepts_directory_is_rejected_before_any_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            out = base / "bundle"
            outside = base / "outside"
            out.mkdir()
            outside.mkdir()
            (out / "concepts").symlink_to(outside, target_is_directory=True)

            with self.assertRaisesRegex(build_bundle.BundleSecurityError, "symlink"):
                build_bundle.build(load_fixture(), out)

            self.assertEqual(list(outside.iterdir()), [])
            self.assertFalse((out / "knowledge.json").exists())
            self.assertFalse((out / "index.md").exists())

    @unittest.skipUnless(hasattr(Path, "symlink_to"), "symlinks unavailable")
    def test_symlinked_output_root_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            outside = base / "outside"
            outside.mkdir()
            out = base / "bundle"
            out.symlink_to(outside, target_is_directory=True)

            with self.assertRaisesRegex(build_bundle.BundleSecurityError, "output root is a symlink"):
                build_bundle.build(load_fixture(), out)
            self.assertEqual(list(outside.iterdir()), [])

    def test_regular_rebuild_is_atomic_and_does_not_delete_unrelated_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "bundle"
            doc = load_fixture()
            build_bundle.build(doc, out)
            unrelated = out / "keep-me.txt"
            unrelated.write_text("user data", encoding="utf-8")

            doc["metadata"]["title"] = "Rebuilt bundle"
            build_bundle.build(doc, out)

            self.assertEqual(unrelated.read_text(encoding="utf-8"), "user data")
            self.assertIn("Rebuilt bundle", (out / "index.md").read_text(encoding="utf-8"))
            self.assertEqual(list(out.rglob(".kd-bundle-*.tmp")), [])

    @unittest.skipUnless(hasattr(Path, "symlink_to"), "symlinks unavailable")
    def test_cli_reports_clear_error_for_unsafe_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            outside = base / "outside"
            outside.mkdir()
            out = base / "bundle"
            out.symlink_to(outside, target_is_directory=True)
            err = io.StringIO()

            with contextlib.redirect_stderr(err):
                code = build_bundle.main([str(FIXTURE), "-o", str(out)])

            self.assertEqual(code, 2)
            self.assertIn("refusing unsafe bundle write", err.getvalue())
            self.assertIn("symlink", err.getvalue())


if __name__ == "__main__":
    unittest.main(verbosity=2)
