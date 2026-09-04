"""Tests for the local deterministic runner and its guarded API/MCP facade."""

from __future__ import annotations

import copy
import http.client
import json
import re
import shutil
import sys
import tempfile
import threading
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import run_pipeline  # noqa: E402
import serve_api  # noqa: E402
import build_viewer  # noqa: E402


FIXTURE = ROOT / "tests" / "fixtures" / "minimal.knowledge.json"


class RunnerCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.input_root = self.base / "inputs"
        self.output_root = self.base / "outputs"
        self.outside_root = self.base / "outside"
        self.input_root.mkdir()
        self.output_root.mkdir()
        self.outside_root.mkdir()
        self.graph = self.input_root / "graph.knowledge.json"
        shutil.copyfile(FIXTURE, self.graph)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def run_build(self, artifacts=("json", "md"), **kwargs):
        return run_pipeline.execute_pipeline(
            "graph.knowledge.json",
            input_root=self.input_root,
            output_root=self.output_root,
            operation="build",
            artifacts=artifacts,
            **kwargs,
        )

    def test_manifest_hashes_and_idempotent_reuse(self) -> None:
        requested = ("json", "md", "bundle", "html", "cypher", "ctxt", "canvas")
        first = self.run_build(requested)
        self.assertEqual(first["status"], "succeeded")
        self.assertFalse(first["reused"])
        manifest_path = Path(first["manifest_path"])
        before = manifest_path.read_bytes()
        before_mtime = manifest_path.stat().st_mtime_ns

        manifest = first["manifest"]
        self.assertEqual(manifest["manifest_version"], "1.0")
        self.assertFalse(manifest["network_access"])
        self.assertFalse(manifest["telemetry"])
        self.assertEqual(manifest["input"]["sha256"], run_pipeline.sha256_file(self.graph))
        self.assertTrue(manifest["toolchain"]["hashes"]["spec"])
        self.assertTrue(manifest["toolchain"]["hashes"]["schema"])
        for tool in (
            "api_server",
            "build_exports",
            "build_viewer",
            "strict_json",
            "viewer_template",
            "viewer_css",
            "viewer_javascript",
        ):
            self.assertTrue(manifest["toolchain"]["hashes"][tool])
        self.assertTrue(all(stage["status"] == "succeeded" for stage in manifest["stages"]))
        self.assertTrue(run_pipeline._records_valid(Path(first["run_dir"]), manifest["outputs"]))
        kinds = {output["kind"] for output in manifest["outputs"]}
        self.assertTrue(
            {
                "knowledge_json",
                "knowledge_markdown",
                "knowledge_bundle",
                "knowledge_html",
                "knowledge_cypher",
                "knowledge_ctxt",
                "knowledge_canvas",
                "validation_report",
            }
            <= kinds
        )

        run_dir = Path(first["run_dir"])
        derived = json.loads((run_dir / "artifacts/graph.knowledge.json").read_text(encoding="utf-8"))
        self.assertEqual(
            manifest["validation"]["conformance_score"],
            derived["metadata"]["conformance_score"],
        )
        self.assertFalse(manifest["validation"]["semantic_accuracy_evaluated"])
        canvas = json.loads((run_dir / "artifacts/graph.knowledge.canvas").read_text(encoding="utf-8"))
        self.assertEqual(canvas["x-knowledge-distiller"]["source_graph"], derived)
        ctxt = (run_dir / "artifacts/graph.knowledge.ctxt").read_text(encoding="utf-8")
        graph_line = next(line for line in ctxt.splitlines() if line.startswith("graph-json: "))
        self.assertEqual(json.loads(graph_line.removeprefix("graph-json: ")), derived)
        html = (run_dir / "artifacts/graph.knowledge.html").read_text(encoding="utf-8")
        self.assertNotIn(build_viewer.TOKEN_DATA, html)
        embedded = re.search(
            r'<script id="kd-graph-data" type="application/json">(.*?)</script>',
            html,
            flags=re.DOTALL,
        )
        self.assertIsNotNone(embedded)
        self.assertEqual(json.loads(embedded.group(1)), derived)

        second = self.run_build(tuple(reversed(requested)))
        self.assertTrue(second["reused"])
        self.assertEqual(second["run_id"], first["run_id"])
        self.assertEqual(manifest_path.read_bytes(), before)
        self.assertEqual(manifest_path.stat().st_mtime_ns, before_mtime)

    def test_viewer_render_is_pure_deterministic_and_escaped(self) -> None:
        graph = json.loads(self.graph.read_text(encoding="utf-8"))
        graph["metadata"]["title"] = "</script><script>alert(1)</script>"
        before = copy.deepcopy(graph)
        first = build_viewer.render(graph)
        second = build_viewer.render(copy.deepcopy(graph))
        self.assertEqual(first, second)
        self.assertEqual(graph, before)
        self.assertNotIn(build_viewer.TOKEN_CSS, first)
        self.assertNotIn(build_viewer.TOKEN_JS, first)
        self.assertNotIn(build_viewer.TOKEN_DATA, first)
        self.assertNotIn("</script><script>alert(1)</script>", first)
        self.assertIn("\\u003c/script\\u003e", first)

    def test_unknown_artifact_is_rejected_before_a_run_is_created(self) -> None:
        with self.assertRaisesRegex(run_pipeline.InvalidRequestError, "unsupported artifact"):
            self.run_build(("json", "pdf"))
        self.assertFalse((self.output_root / "runs").exists())

    def test_tampered_output_never_reuses_or_overwrites_old_attempt(self) -> None:
        first = self.run_build(("canvas",))
        first_manifest = Path(first["manifest_path"])
        manifest_bytes = first_manifest.read_bytes()
        canvas_path = Path(first["run_dir"]) / "artifacts" / "graph.knowledge.canvas"
        canvas_path.write_text(
            canvas_path.read_text(encoding="utf-8") + "tampered\n",
            encoding="utf-8",
        )

        second = self.run_build(("canvas",))
        self.assertFalse(second["reused"])
        self.assertNotEqual(second["run_id"], first["run_id"])
        self.assertTrue(second["run_id"].endswith("a0002"))
        self.assertEqual(first_manifest.read_bytes(), manifest_bytes)

    def test_verified_receipts_resume_an_interrupted_attempt(self) -> None:
        first = self.run_build()
        manifest_path = Path(first["manifest_path"])
        manifest_path.unlink()  # simulate a crash after the last receipt, before finalization

        resumed = self.run_build()
        self.assertTrue(resumed["resumed"])
        self.assertFalse(resumed["reused"])
        self.assertEqual(resumed["run_id"], first["run_id"])
        self.assertTrue(Path(resumed["manifest_path"]).is_file())

    def test_failed_validation_has_an_immutable_failure_manifest(self) -> None:
        graph = json.loads(self.graph.read_text(encoding="utf-8"))
        del graph["metadata"]["title"]
        self.graph.write_text(json.dumps(graph), encoding="utf-8")

        first = run_pipeline.execute_pipeline(
            "graph.knowledge.json",
            input_root=self.input_root,
            output_root=self.output_root,
            operation="validate",
        )
        self.assertEqual(first["status"], "failed")
        self.assertEqual(first["manifest"]["failure"]["code"], "validation_failed")
        self.assertFalse(first["manifest"]["validation"]["ok"])
        first_path = Path(first["manifest_path"])
        first_bytes = first_path.read_bytes()

        retry = run_pipeline.execute_pipeline(
            "graph.knowledge.json",
            input_root=self.input_root,
            output_root=self.output_root,
            operation="validate",
        )
        self.assertNotEqual(retry["run_id"], first["run_id"])
        self.assertTrue(retry["run_id"].endswith("a0002"))
        self.assertEqual(first_path.read_bytes(), first_bytes)

    def test_traversal_and_escaping_symlink_are_rejected(self) -> None:
        outside = self.outside_root / "outside.knowledge.json"
        shutil.copyfile(FIXTURE, outside)
        with self.assertRaises(run_pipeline.PathSandboxError):
            run_pipeline.execute_pipeline(
                "../outside/outside.knowledge.json",
                input_root=self.input_root,
                output_root=self.output_root,
                operation="validate",
            )

        link = self.input_root / "link.knowledge.json"
        try:
            link.symlink_to(outside)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks are unavailable")
        with self.assertRaises(run_pipeline.PathSandboxError):
            run_pipeline.execute_pipeline(
                "link.knowledge.json",
                input_root=self.input_root,
                output_root=self.output_root,
                operation="validate",
            )

    def test_input_limit_is_enforced_before_parsing(self) -> None:
        with self.assertRaises(run_pipeline.InputTooLargeError):
            run_pipeline.execute_pipeline(
                "graph.knowledge.json",
                input_root=self.input_root,
                output_root=self.output_root,
                operation="validate",
                max_input_bytes=10,
            )

    def test_strict_json_rejects_ambiguous_or_nonportable_graph_inputs(self) -> None:
        cases = {
            "duplicate": b'{"metadata":{},"metadata":{}}',
            "nan": b'{"value":NaN}',
            "infinity": b'{"value":Infinity}',
            "surrogate": b'{"value":"\\ud800"}',
            "encoding": b'{"value":"\xff"}',
        }
        for name, raw in cases.items():
            with self.subTest(name=name):
                self.graph.write_bytes(raw)
                result = run_pipeline.execute_pipeline(
                    "graph.knowledge.json",
                    input_root=self.input_root,
                    output_root=self.output_root,
                    operation="validate",
                )
                self.assertEqual(result["status"], "failed")
                self.assertEqual(result["manifest"]["failure"]["code"], "invalid_json")

    def test_path_swap_after_open_cannot_redirect_runner_input(self) -> None:
        original = self.graph.read_bytes()
        held = self.input_root / "opened-original.knowledge.json"
        outside = self.outside_root / "outside.knowledge.json"
        outside.write_text('{"attacker":true}', encoding="utf-8")
        original_reader = run_pipeline._read_fd_bounded

        def swap_then_read(fd, max_bytes):
            self.graph.rename(held)
            try:
                self.graph.symlink_to(outside)
            except (OSError, NotImplementedError):
                self.skipTest("symlinks are unavailable")
            return original_reader(fd, max_bytes)

        with mock.patch.object(
            run_pipeline, "_read_fd_bounded", side_effect=swap_then_read
        ):
            result = run_pipeline.execute_pipeline(
                "graph.knowledge.json",
                input_root=self.input_root,
                output_root=self.output_root,
                operation="validate",
            )
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(
            result["manifest"]["input"]["sha256"],
            run_pipeline.sha256_bytes(original),
        )

    def test_input_root_swap_before_anchor_open_is_rejected(self) -> None:
        moved_root = self.base / "inputs-before-swap"
        outside_graph = self.outside_root / "graph.knowledge.json"
        shutil.copyfile(FIXTURE, outside_graph)
        original_open = run_pipeline._open_component
        swapped = False

        def swap_root_component(name, *, directory_fd, directory):
            nonlocal swapped
            if name == self.input_root.name and directory and not swapped:
                swapped = True
                self.input_root.rename(moved_root)
                try:
                    self.input_root.symlink_to(
                        self.outside_root, target_is_directory=True
                    )
                except (OSError, NotImplementedError):
                    self.skipTest("symlinks are unavailable")
            return original_open(
                name, directory_fd=directory_fd, directory=directory
            )

        with mock.patch.object(
            run_pipeline, "_open_component", side_effect=swap_root_component
        ):
            with self.assertRaises(run_pipeline.PathSandboxError):
                run_pipeline.execute_pipeline(
                    "graph.knowledge.json",
                    input_root=self.input_root,
                    output_root=self.output_root,
                    operation="validate",
                )

    def test_output_root_children_and_attempt_tree_reject_symlink_escapes(self) -> None:
        root_escape = self.base / "output-root-escape"
        root_escape.mkdir()
        root_link = self.base / "output-root-link"
        try:
            root_link.symlink_to(root_escape, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks are unavailable")
        with self.assertRaises(run_pipeline.PathSandboxError):
            run_pipeline.execute_pipeline(
                "graph.knowledge.json",
                input_root=self.input_root,
                output_root=root_link,
                operation="validate",
            )
        self.assertEqual(list(root_escape.iterdir()), [])

        for child in ("runs", ".locks"):
            with self.subTest(child=child):
                output = self.base / f"output-{child.removeprefix('.')}"
                escape = self.base / f"escape-{child.removeprefix('.')}"
                output.mkdir()
                escape.mkdir()
                try:
                    (output / child).symlink_to(escape, target_is_directory=True)
                except (OSError, NotImplementedError):
                    self.skipTest("symlinks are unavailable")
                with self.assertRaises(run_pipeline.PathSandboxError):
                    run_pipeline.execute_pipeline(
                        "graph.knowledge.json",
                        input_root=self.input_root,
                        output_root=output,
                        operation="validate",
                    )
                self.assertEqual(list(escape.iterdir()), [])

        first = self.run_build(("json", "md"))
        run_dir = Path(first["run_dir"])
        (run_dir / "manifest.json").unlink()
        artifacts = run_dir / "artifacts"
        preserved = run_dir / "artifacts-preserved"
        artifacts.rename(preserved)
        escape = self.base / "attempt-escape"
        escape.mkdir()
        try:
            artifacts.symlink_to(escape, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks are unavailable")
        retry = self.run_build(("json", "md"))
        self.assertFalse(retry["resumed"])
        self.assertTrue(retry["run_id"].endswith("a0002"))
        self.assertEqual(list(escape.iterdir()), [])

    def test_incomplete_manifest_and_receipt_records_are_never_trusted(self) -> None:
        first = self.run_build(("json", "md"))
        manifest_path = Path(first["manifest_path"])
        forged = json.loads(manifest_path.read_text(encoding="utf-8"))
        forged["outputs"] = []
        manifest_path.write_text(json.dumps(forged), encoding="utf-8")

        retry = self.run_build(("json", "md"))
        self.assertFalse(retry["reused"])
        self.assertTrue(retry["run_id"].endswith("a0002"))

        second_manifest = Path(retry["manifest_path"])
        second_manifest.unlink()
        load_receipt = Path(retry["run_dir"]) / "stages" / "01-load.json"
        receipt = json.loads(load_receipt.read_text(encoding="utf-8"))
        receipt["outputs"] = []
        load_receipt.write_text(json.dumps(receipt), encoding="utf-8")

        third = self.run_build(("json", "md"))
        self.assertFalse(third["resumed"])
        self.assertTrue(third["run_id"].endswith("a0003"))

    def test_output_records_require_complete_exact_shape(self) -> None:
        first = self.run_build(("json",))
        run_dir = Path(first["run_dir"])
        records = copy.deepcopy(first["manifest"]["outputs"])
        records[0].pop("sha256")
        self.assertFalse(run_pipeline._records_valid(run_dir, records))
        records = copy.deepcopy(first["manifest"]["outputs"])
        records[0]["unexpected"] = True
        self.assertFalse(run_pipeline._records_valid(run_dir, records))

    def test_output_quotas_reject_before_attempt_and_bound_partial_runs(self) -> None:
        (self.output_root / "existing.bin").write_bytes(b"x" * 2048)
        with self.assertRaises(run_pipeline.OutputQuotaError):
            self.run_build(
                ("json",),
                max_output_root_bytes=1024,
            )
        anchored = run_pipeline._prepare_output_root(self.output_root)
        self.assertEqual(list((anchored / "runs").iterdir()), [])

        (self.output_root / "existing.bin").unlink()
        with self.assertRaises(run_pipeline.OutputQuotaError):
            self.run_build(("json",), max_run_bytes=1)
        self.assertEqual(list((anchored / "runs").iterdir()), [])

        max_run_bytes = 20_000
        with self.assertRaises(run_pipeline.OutputQuotaError):
            self.run_build(
                ("json", "md", "bundle", "html", "cypher", "ctxt", "canvas"),
                max_run_bytes=max_run_bytes,
            )
        attempts = [path for path in (anchored / "runs").iterdir() if path.is_dir()]
        self.assertEqual(len(attempts), 1)
        self.assertLessEqual(
            run_pipeline._directory_usage_bytes(attempts[0]), max_run_bytes
        )

    def test_api_quota_error_is_bounded_and_creates_no_attempt(self) -> None:
        config = serve_api.ServerConfig(
            input_root=self.input_root,
            output_root=run_pipeline._prepare_output_root(self.output_root),
            toolchain_fingerprint=run_pipeline.process_toolchain_fingerprint(),
            max_run_bytes=1,
        )
        with self.assertRaises(serve_api.RequestProblem) as raised:
            serve_api.dispatch_operation(
                config, "validate", {"input": "graph.knowledge.json"}
            )
        self.assertEqual(raised.exception.status, 507)
        self.assertEqual(raised.exception.code, "output_quota")
        self.assertEqual(list((config.output_root / "runs").iterdir()), [])

    def test_api_toolchain_change_latches_until_process_restart(self) -> None:
        config = serve_api.ServerConfig(
            input_root=self.input_root,
            output_root=run_pipeline._prepare_output_root(self.output_root),
            toolchain_fingerprint=run_pipeline.process_toolchain_fingerprint(),
        )
        changed = dict(run_pipeline._PROCESS_TOOL_HASHES)
        changed["runner"] = "0" * 64
        original_latch = run_pipeline._TOOLCHAIN_CHANGED
        try:
            with mock.patch.object(
                run_pipeline, "_current_tool_hashes", return_value=changed
            ):
                with self.assertRaises(serve_api.RequestProblem) as raised:
                    serve_api.dispatch_operation(
                        config, "validate", {"input": "graph.knowledge.json"}
                    )
            self.assertEqual(raised.exception.status, 503)
            self.assertEqual(raised.exception.code, "toolchain_changed")
            self.assertFalse((config.output_root / "runs").exists())
            with self.assertRaises(run_pipeline.ToolchainChangedError):
                run_pipeline.assert_toolchain_unchanged(
                    config.toolchain_fingerprint
                )
        finally:
            with run_pipeline._TOOLCHAIN_STATE_LOCK:
                run_pipeline._TOOLCHAIN_CHANGED = original_latch

    def test_toolchain_change_during_run_records_failure_and_latches(self) -> None:
        baseline = dict(run_pipeline._PROCESS_TOOL_HASHES)
        changed = dict(baseline)
        changed["schema"] = "f" * 64
        original_latch = run_pipeline._TOOLCHAIN_CHANGED
        try:
            with mock.patch.object(
                run_pipeline,
                "_current_tool_hashes",
                side_effect=[baseline, changed],
            ):
                result = run_pipeline.execute_pipeline(
                    "graph.knowledge.json",
                    input_root=self.input_root,
                    output_root=self.output_root,
                    operation="validate",
                )
            self.assertEqual(result["status"], "failed")
            self.assertEqual(
                result["manifest"]["failure"]["type"],
                "ToolchainChangedError",
            )
            with self.assertRaises(run_pipeline.ToolchainChangedError):
                run_pipeline.execute_pipeline(
                    "graph.knowledge.json",
                    input_root=self.input_root,
                    output_root=self.output_root,
                    operation="validate",
                )
            attempts = list(
                (run_pipeline._prepare_output_root(self.output_root) / "runs").iterdir()
            )
            self.assertEqual(len(attempts), 1)
        finally:
            with run_pipeline._TOOLCHAIN_STATE_LOCK:
                run_pipeline._TOOLCHAIN_CHANGED = original_latch

    def test_token_file_requires_owner_only_regular_bounded_file(self) -> None:
        token = "file-backed-token-" + "z" * 32
        token_file = self.base / "token.txt"
        token_file.write_text(token + "\n", encoding="utf-8")
        token_file.chmod(0o600)
        self.assertEqual(serve_api._read_token_file(token_file), token)

        token_file.chmod(0o644)
        with self.assertRaisesRegex(ValueError, "permissions"):
            serve_api._read_token_file(token_file)
        token_file.chmod(0o600)

        token_link = self.base / "token-link.txt"
        try:
            token_link.symlink_to(token_file)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks are unavailable")
        with self.assertRaisesRegex(ValueError, "opened safely"):
            serve_api._read_token_file(token_link)

        oversized = self.base / "oversized-token.txt"
        oversized.write_bytes(b"x" * (serve_api.MAX_TOKEN_FILE_BYTES + 1))
        oversized.chmod(0o600)
        with self.assertRaisesRegex(ValueError, "1-1024 bytes"):
            serve_api._read_token_file(oversized)

    def test_invalid_output_quota_configuration_is_rejected_before_bind(self) -> None:
        with self.assertRaisesRegex(ValueError, "size limits"):
            serve_api.create_server(
                input_root=self.input_root,
                output_root=self.output_root,
                port=0,
                max_run_bytes=0,
            )


class APICase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.input_root = self.base / "inputs"
        self.output_root = self.base / "outputs"
        self.outside_root = self.base / "outside"
        self.input_root.mkdir()
        self.output_root.mkdir()
        self.outside_root.mkdir()
        shutil.copyfile(FIXTURE, self.input_root / "graph.knowledge.json")
        shutil.copyfile(FIXTURE, self.outside_root / "outside.knowledge.json")
        self.token = "test-bearer-token-" + "x" * 32
        self.server = serve_api.create_server(
            input_root=self.input_root,
            output_root=self.output_root,
            port=0,
            max_body_bytes=512,
            bearer_token=self.token,
            max_concurrent_requests=8,
            max_concurrent_runs=1,
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.temp.cleanup()

    def request(
        self,
        method: str,
        path: str,
        value=None,
        *,
        raw: bytes | None = None,
        content_type="application/json",
        authorization: str | None = None,
        include_authorization: bool = True,
        extra_headers: dict[str, str] | None = None,
    ):
        body = raw if raw is not None else (json.dumps(value).encode("utf-8") if value is not None else None)
        headers = {}
        if include_authorization:
            headers["Authorization"] = authorization or f"Bearer {self.token}"
        if body is not None:
            headers.update({"Content-Type": content_type, "Content-Length": str(len(body))})
        headers.update(extra_headers or {})
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=10)
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        payload = response.read()
        connection.close()
        decoded = json.loads(payload) if payload else None
        return response.status, decoded, response.getheaders()

    def test_health_and_validate_return_bounded_relative_results(self) -> None:
        status, health, _ = self.request("GET", "/health")
        self.assertEqual(status, 200)
        self.assertEqual(health["status"], "ok")
        self.assertFalse(health["network_access"])

        status, result, headers = self.request("POST", "/v1/validate", {"input": "graph.knowledge.json"})
        self.assertEqual(status, 200)
        self.assertEqual(result["status"], "succeeded")
        self.assertTrue(result["manifest"].startswith("runs/"))
        self.assertNotIn(str(self.base), json.dumps(result))
        self.assertEqual(dict(headers)["Cache-Control"], "no-store")
        self.assertNotIn("Access-Control-Allow-Origin", dict(headers))
        self.assertNotIn(self.token, json.dumps(health) + json.dumps(result))

    def test_authentication_host_origin_and_cors_guards(self) -> None:
        status, result, _ = self.request(
            "GET", "/health", include_authorization=False
        )
        self.assertEqual(status, 401)
        self.assertEqual(result["error"], "authentication_required")

        status, result, headers = self.request(
            "GET", "/health", authorization="Bearer definitely-wrong-token-value"
        )
        self.assertEqual(status, 401)
        self.assertEqual(result["error"], "authentication_required")
        self.assertIn("WWW-Authenticate", dict(headers))
        self.assertNotIn(self.token, json.dumps(result))

        status, result, _ = self.request(
            "GET", "/health", extra_headers={"Host": "127.0.0.1:1"}
        )
        self.assertEqual(status, 400)
        self.assertEqual(result["error"], "host_rejected")

        status, result, headers = self.request(
            "POST",
            "/v1/validate",
            {"input": "graph.knowledge.json"},
            extra_headers={"Origin": "https://example.invalid"},
        )
        self.assertEqual(status, 403)
        self.assertEqual(result["error"], "origin_rejected")
        self.assertNotIn("Access-Control-Allow-Origin", dict(headers))

        status, result, _ = self.request("GET", "/health?details=1")
        self.assertEqual(status, 400)
        self.assertEqual(result["error"], "query_rejected")

        status, result, _ = self.request(
            "PUT", "/v1/validate", authorization="Bearer wrong-token-value-with-length-12345"
        )
        self.assertEqual(status, 401)
        status, result, _ = self.request("PUT", "/v1/validate")
        self.assertEqual(status, 405)
        self.assertEqual(result["error"], "method_not_allowed")

        status, result, _ = self.request(
            "GET", "/health", authorization="Bearer " + "é" * 32
        )
        self.assertEqual(status, 401)

        same_origin = f"http://127.0.0.1:{self.server.server_port}"
        status, result, headers = self.request(
            "POST",
            "/v1/validate",
            {"input": "graph.knowledge.json"},
            extra_headers={"Origin": same_origin},
        )
        self.assertEqual(status, 200)
        self.assertEqual(result["status"], "succeeded")
        self.assertNotIn("Access-Control-Allow-Origin", dict(headers))

    def test_request_json_is_strict(self) -> None:
        invalid_bodies = (
            b'{"input":"graph.knowledge.json","input":"graph.knowledge.json"}',
            b'{"input":NaN}',
            b'{"input":Infinity}',
            b'{"input":"\\ud800"}',
        )
        for raw in invalid_bodies:
            with self.subTest(raw=raw):
                status, result, _ = self.request("POST", "/v1/validate", raw=raw)
                self.assertEqual(status, 400)
                self.assertEqual(result["error"], "invalid_json")

    def test_pipeline_failures_do_not_leak_tokens_or_absolute_paths(self) -> None:
        secret_error = run_pipeline.PipelineError(
            self.token + " " + str(self.input_root) + " " + str(self.output_root)
        )
        with mock.patch.object(
            run_pipeline, "execute_pipeline", side_effect=secret_error
        ):
            status, result, _ = self.request(
                "POST", "/v1/validate", {"input": "graph.knowledge.json"}
            )
            self.assertEqual(status, 500)
            encoded = json.dumps(result)
            self.assertNotIn(self.token, encoded)
            self.assertNotIn(str(self.input_root), encoded)
            self.assertNotIn(str(self.output_root), encoded)

            mcp = {
                "jsonrpc": "2.0",
                "id": 11,
                "method": "tools/call",
                "params": {
                    "name": "knowledge_validate",
                    "arguments": {"input": "graph.knowledge.json"},
                },
            }
            status, result, _ = self.request("POST", "/mcp", mcp)
            self.assertEqual(status, 200)
            encoded = json.dumps(result)
            self.assertNotIn(self.token, encoded)
            self.assertNotIn(str(self.input_root), encoded)
            self.assertNotIn(str(self.output_root), encoded)

    def test_request_and_run_concurrency_are_bounded(self) -> None:
        entered = threading.Event()
        release = threading.Event()
        original_execute = run_pipeline.execute_pipeline

        def blocked_execute(*args, **kwargs):
            entered.set()
            if not release.wait(timeout=5):
                raise AssertionError("test did not release blocked run")
            return original_execute(*args, **kwargs)

        first_result = []
        with mock.patch.object(
            run_pipeline, "execute_pipeline", side_effect=blocked_execute
        ):
            worker = threading.Thread(
                target=lambda: first_result.append(
                    self.request(
                        "POST", "/v1/validate", {"input": "graph.knowledge.json"}
                    )
                ),
                daemon=True,
            )
            worker.start()
            self.assertTrue(entered.wait(timeout=3))
            status, result, _ = self.request(
                "POST", "/v1/validate", {"input": "graph.knowledge.json"}
            )
            self.assertEqual(status, 503)
            self.assertEqual(result["error"], "run_capacity")
            release.set()
            worker.join(timeout=5)
        self.assertEqual(first_result[0][0], 200)

        held = [
            self.server.request_slots.acquire(blocking=False)
            for _ in range(self.server.max_concurrent_requests)
        ]
        self.assertTrue(all(held))
        try:
            status, result, _ = self.request("GET", "/health")
            self.assertEqual(status, 503)
            self.assertEqual(result["error"], "request_capacity")
        finally:
            for _ in held:
                self.server.request_slots.release()

    def test_build_rejects_command_like_and_output_root_fields(self) -> None:
        status, result, _ = self.request(
            "POST",
            "/v1/build",
            {"input": "graph.knowledge.json", "command": "id", "output_root": "/tmp/escape"},
        )
        self.assertEqual(status, 400)
        self.assertEqual(result["error"], "unknown_field")
        self.assertFalse((self.output_root / "escape").exists())

        status, result, _ = self.request(
            "POST",
            "/v1/build",
            {"input": "graph.knowledge.json", "artifacts": ["json", "pdf"]},
        )
        self.assertEqual(status, 400)
        self.assertEqual(result["error"], "invalid_artifacts")

    def test_build_accepts_all_extended_artifacts(self) -> None:
        requested = ["html", "cypher", "ctxt", "canvas"]
        status, result, _ = self.request(
            "POST",
            "/v1/build",
            {"input": "graph.knowledge.json", "artifacts": requested},
        )
        self.assertEqual(status, 200)
        self.assertEqual(result["status"], "succeeded")
        kinds = {item["kind"] for item in result["outputs"]}
        self.assertTrue(
            {
                "knowledge_json",
                "knowledge_html",
                "knowledge_cypher",
                "knowledge_ctxt",
                "knowledge_canvas",
            }
            <= kinds
        )

    def test_traversal_absolute_paths_and_large_bodies_are_rejected(self) -> None:
        status, result, _ = self.request(
            "POST", "/v1/validate", {"input": "../outside/outside.knowledge.json"}
        )
        self.assertEqual(status, 403)
        self.assertEqual(result["error"], "path_rejected")

        status, result, _ = self.request(
            "POST", "/v1/validate", {"input": str(self.input_root / "graph.knowledge.json")}
        )
        self.assertEqual(status, 403)
        self.assertEqual(result["error"], "path_rejected")

        status, result, _ = self.request(
            "POST", "/v1/validate", raw=b"{" + b" " * 600 + b"}"
        )
        self.assertEqual(status, 413)
        self.assertEqual(result["error"], "body_too_large")

    def test_mcp_lists_and_calls_only_safe_tools(self) -> None:
        status, listed, _ = self.request(
            "POST", "/mcp", {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
        )
        self.assertEqual(status, 200)
        names = {tool["name"] for tool in listed["result"]["tools"]}
        self.assertEqual(names, {"knowledge_validate", "knowledge_build"})
        build_tool = next(
            tool for tool in listed["result"]["tools"] if tool["name"] == "knowledge_build"
        )
        self.assertEqual(
            set(build_tool["inputSchema"]["properties"]["artifacts"]["items"]["enum"]),
            run_pipeline.ALLOWED_ARTIFACTS,
        )

        call = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "knowledge_build",
                "arguments": {
                    "input": "graph.knowledge.json",
                    "artifacts": ["html", "cypher", "ctxt", "canvas"],
                },
            },
        }
        status, called, _ = self.request("POST", "/mcp", call)
        self.assertEqual(status, 200)
        self.assertFalse(called["result"]["isError"])
        self.assertEqual(called["result"]["structuredContent"]["status"], "succeeded")

        call["id"] = 3
        call["params"]["arguments"]["artifacts"] = ["pdf"]
        status, rejected, _ = self.request("POST", "/mcp", call)
        self.assertEqual(status, 200)
        self.assertTrue(rejected["result"]["isError"])
        self.assertEqual(rejected["result"]["structuredContent"]["error"], "invalid_artifacts")

        call["id"] = 4
        call["params"]["arguments"]["artifacts"] = ["json"]
        call["params"]["arguments"]["output_root"] = "/tmp/escape"
        status, rejected, _ = self.request("POST", "/mcp", call)
        self.assertEqual(status, 200)
        self.assertTrue(rejected["result"]["isError"])
        self.assertEqual(rejected["result"]["structuredContent"]["error"], "unknown_field")

    def test_unknown_mcp_method_and_non_loopback_bind_are_rejected(self) -> None:
        status, response, _ = self.request(
            "POST", "/mcp", {"jsonrpc": "2.0", "id": 9, "method": "shell/exec", "params": {}}
        )
        self.assertEqual(status, 200)
        self.assertEqual(response["error"]["code"], -32601)
        with self.assertRaises(ValueError):
            serve_api.create_server(
                input_root=self.input_root,
                output_root=self.output_root,
                host="0.0.0.0",
                port=0,
            )

    def test_random_startup_tokens_are_unique_and_response_sanitizer_redacts(self) -> None:
        first = serve_api.create_server(
            input_root=self.input_root,
            output_root=self.output_root,
            port=0,
        )
        second = serve_api.create_server(
            input_root=self.input_root,
            output_root=self.output_root,
            port=0,
        )
        try:
            self.assertNotEqual(first.bearer_token, second.bearer_token)
            self.assertGreaterEqual(
                len(first.bearer_token), serve_api.MIN_BEARER_TOKEN_CHARS
            )
            payload = self.server.sanitize_payload(
                {
                    "secret": self.token,
                    "input": str(self.input_root / "graph.knowledge.json"),
                    "output": str(self.output_root / "runs"),
                }
            )
            encoded = json.dumps(payload)
            self.assertNotIn(self.token, encoded)
            self.assertNotIn(str(self.input_root), encoded)
            self.assertNotIn(str(self.output_root), encoded)
        finally:
            first.server_close()
            second.server_close()

        with self.assertRaises(ValueError):
            serve_api.create_server(
                input_root=self.input_root,
                output_root=self.output_root,
                port=0,
                bearer_token="too-short",
            )
        with self.assertRaises(ValueError):
            serve_api.create_server(
                input_root=self.input_root,
                output_root=self.output_root,
                port=0,
                max_concurrent_requests=1,
                max_concurrent_runs=2,
            )

    def test_http_response_and_api_input_limits_are_hard_bounds(self) -> None:
        token = "bounded-test-token-" + "y" * 32
        response_limited = serve_api.create_server(
            input_root=self.input_root,
            output_root=self.output_root,
            port=0,
            bearer_token=token,
            max_response_bytes=128,
        )
        response_thread = threading.Thread(
            target=response_limited.serve_forever, daemon=True
        )
        response_thread.start()
        try:
            connection = http.client.HTTPConnection(
                "127.0.0.1", response_limited.server_port, timeout=10
            )
            connection.request(
                "GET", "/health", headers={"Authorization": f"Bearer {token}"}
            )
            response = connection.getresponse()
            body = response.read()
            connection.close()
            self.assertEqual(response.status, 500)
            self.assertLessEqual(len(body), 128)
            self.assertLessEqual(int(dict(response.getheaders())["Content-Length"]), 128)
        finally:
            response_limited.shutdown()
            response_limited.server_close()
            response_thread.join(timeout=5)

        input_limited = serve_api.create_server(
            input_root=self.input_root,
            output_root=self.output_root,
            port=0,
            bearer_token=token,
            max_input_bytes=10,
        )
        input_thread = threading.Thread(target=input_limited.serve_forever, daemon=True)
        input_thread.start()
        try:
            body = json.dumps({"input": "graph.knowledge.json"}).encode("utf-8")
            connection = http.client.HTTPConnection(
                "127.0.0.1", input_limited.server_port, timeout=10
            )
            connection.request(
                "POST",
                "/v1/validate",
                body=body,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                    "Content-Length": str(len(body)),
                },
            )
            response = connection.getresponse()
            payload = json.loads(response.read())
            connection.close()
            self.assertEqual(response.status, 413)
            self.assertEqual(payload["error"], "input_too_large")
        finally:
            input_limited.shutdown()
            input_limited.server_close()
            input_thread.join(timeout=5)

    def test_output_quota_and_toolchain_change_use_safe_http_errors(self) -> None:
        original_config = self.server.config
        self.server.config = replace(original_config, max_run_bytes=1)
        try:
            status, result, _ = self.request(
                "POST", "/v1/validate", {"input": "graph.knowledge.json"}
            )
        finally:
            self.server.config = original_config
        self.assertEqual(status, 507)
        self.assertEqual(result["error"], "output_quota")
        self.assertEqual(list((original_config.output_root / "runs").iterdir()), [])

        changed = dict(run_pipeline._PROCESS_TOOL_HASHES)
        changed["api_server"] = "1" * 64
        original_latch = run_pipeline._TOOLCHAIN_CHANGED
        try:
            with mock.patch.object(
                run_pipeline, "_current_tool_hashes", return_value=changed
            ):
                status, result, _ = self.request(
                    "POST", "/v1/validate", {"input": "graph.knowledge.json"}
                )
            self.assertEqual(status, 503)
            self.assertEqual(result["error"], "toolchain_changed")

            status, result, _ = self.request(
                "POST", "/v1/validate", {"input": "graph.knowledge.json"}
            )
            self.assertEqual(status, 503)
            self.assertEqual(result["error"], "toolchain_changed")
        finally:
            with run_pipeline._TOOLCHAIN_STATE_LOCK:
                run_pipeline._TOOLCHAIN_CHANGED = original_latch


if __name__ == "__main__":
    unittest.main()
