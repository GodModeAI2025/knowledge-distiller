#!/usr/bin/env python3
"""Guarded localhost HTTP and minimal MCP facade for ``run_pipeline``.

The server is intentionally small and local:

* it binds to ``127.0.0.1`` only and verifies peer, Host, and Origin;
* every endpoint requires one per-process bearer token;
* input and output roots are fixed at process start;
* bodies, responses, concurrent requests/runs, paths, keys, and artifact names are bounded;
* it invokes Python functions only (never a shell or arbitrary command); and
* it performs no telemetry or outbound network access.

HTTP endpoints:

``GET /health``
``POST /v1/validate``
``POST /v1/build`` (``/v1/run`` is an alias)
``POST /mcp`` for a compact JSON-RPC/MCP tools subset
"""

from __future__ import annotations

import argparse
import contextlib
import hmac
import ipaddress
import json
import os
import secrets
import stat
import sys
import threading
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import run_pipeline  # noqa: E402


API_VERSION = "0.2.0"
MCP_PROTOCOL_VERSION = "2025-06-18"
DEFAULT_MAX_BODY_BYTES = 64 * 1024
DEFAULT_MAX_RESPONSE_BYTES = 1024 * 1024
MAX_INPUT_ARGUMENT_CHARS = 4096
MAX_DIAGNOSTICS = 100
MAX_DIAGNOSTIC_CHARS = 2000
DEFAULT_MAX_CONCURRENT_REQUESTS = 16
DEFAULT_MAX_CONCURRENT_RUNS = 2
MIN_BEARER_TOKEN_CHARS = 32
MAX_BEARER_TOKEN_CHARS = 512
MAX_TOKEN_FILE_BYTES = 1024


class RequestProblem(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


@dataclass(frozen=True)
class ServerConfig:
    input_root: Path
    output_root: Path
    toolchain_fingerprint: str
    redaction_roots: tuple[str, ...] = ()
    max_body_bytes: int = DEFAULT_MAX_BODY_BYTES
    max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES
    max_input_bytes: int = run_pipeline.DEFAULT_MAX_INPUT_BYTES
    max_run_bytes: int = run_pipeline.DEFAULT_MAX_RUN_BYTES
    max_output_root_bytes: int = run_pipeline.DEFAULT_MAX_OUTPUT_ROOT_BYTES


def _read_token_file(value: str | os.PathLike[str]) -> str:
    """Read one owner-only bearer token without following the final path."""
    raw_path = os.fspath(value)
    if not isinstance(raw_path, str) or not raw_path or "\x00" in raw_path:
        raise ValueError("token file path must be a non-empty local path")
    token_path = Path(raw_path).expanduser().absolute()
    try:
        fd = run_pipeline._open_regular_path(token_path)
    except (OSError, run_pipeline.PipelineError, ValueError) as exc:
        raise ValueError("token file cannot be opened safely") from exc
    try:
        opened = os.fstat(fd)
        mode = stat.S_IMODE(opened.st_mode)
        if not stat.S_ISREG(opened.st_mode):
            raise ValueError("token file must be a regular file")
        if not hasattr(os, "geteuid") or opened.st_uid != os.geteuid():
            raise ValueError("token file must be owned by the current user")
        if mode not in {0o400, 0o600}:
            raise ValueError("token file permissions must be exactly 0400 or 0600")
        if opened.st_nlink != 1:
            raise ValueError("token file must have exactly one hard link")
        if opened.st_size < 1 or opened.st_size > MAX_TOKEN_FILE_BYTES:
            raise ValueError(
                f"token file must contain 1-{MAX_TOKEN_FILE_BYTES} bytes"
            )
        chunks: list[bytes] = []
        remaining = MAX_TOKEN_FILE_BYTES + 1
        while remaining:
            chunk = os.read(fd, min(remaining, 4096))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
    except OSError as exc:
        raise ValueError("token file cannot be read safely") from exc
    finally:
        os.close(fd)
    if not raw or len(raw) > MAX_TOKEN_FILE_BYTES:
        raise ValueError(f"token file must contain 1-{MAX_TOKEN_FILE_BYTES} bytes")
    try:
        token = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("token file must contain UTF-8 text") from exc
    if token.endswith("\n"):
        token = token[:-1]
    return token


def _json_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _require_object(value: Any, name: str = "request") -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RequestProblem(HTTPStatus.BAD_REQUEST, "invalid_request", f"{name} must be a JSON object")
    return value


def _only_keys(value: dict[str, Any], allowed: set[str], name: str = "request") -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise RequestProblem(
            HTTPStatus.BAD_REQUEST,
            "unknown_field",
            f"{name} contains unsupported field(s): {', '.join(unknown)}",
        )


def _input_argument(value: Any) -> str:
    if not isinstance(value, str) or not value or len(value) > MAX_INPUT_ARGUMENT_CHARS or "\x00" in value:
        raise RequestProblem(HTTPStatus.BAD_REQUEST, "invalid_input", "input must be a non-empty bounded string")
    if Path(value).is_absolute():
        raise RequestProblem(HTTPStatus.FORBIDDEN, "path_rejected", "API input paths must be relative to the configured input root")
    return value


def _artifact_argument(value: Any) -> list[str]:
    if value is None:
        return ["json", "md"]
    if not isinstance(value, list) or not value or len(value) > len(run_pipeline.ALLOWED_ARTIFACTS):
        raise RequestProblem(HTTPStatus.BAD_REQUEST, "invalid_artifacts", "artifacts must be a short non-empty array")
    if any(not isinstance(item, str) for item in value):
        raise RequestProblem(HTTPStatus.BAD_REQUEST, "invalid_artifacts", "artifact names must be strings")
    unknown = sorted(set(value) - run_pipeline.ALLOWED_ARTIFACTS)
    if unknown:
        raise RequestProblem(HTTPStatus.BAD_REQUEST, "invalid_artifacts", f"unsupported artifact(s): {', '.join(unknown)}")
    return value


def _trim_diagnostics(items: Any) -> list[str]:
    if not isinstance(items, list):
        return []
    return [str(item)[:MAX_DIAGNOSTIC_CHARS] for item in items[:MAX_DIAGNOSTICS]]


def api_summary(result: dict[str, Any]) -> dict[str, Any]:
    """Return a bounded result with paths relative to the configured output root."""
    manifest = result["manifest"]
    validation = manifest.get("validation") or {}
    failure = manifest.get("failure")
    public_failure = None
    if isinstance(failure, dict):
        public_failure = {
            key: failure[key]
            for key in ("stage", "code", "type")
            if key in failure
        }
    return {
        "run_id": result["run_id"],
        "status": result["status"],
        "reused": result["reused"],
        "resumed": result["resumed"],
        "manifest": f"runs/{result['run_id']}/manifest.json",
        "outputs": [
            {
                key: output[key]
                for key in ("kind", "path", "type", "sha256", "size", "file_count")
                if key in output
            }
            for output in manifest.get("outputs", [])
        ],
        "validation": {
            "ok": validation.get("ok"),
            "conformance_score": validation.get("conformance_score", validation.get("quality_score")),
            "quality_score": validation.get("quality_score"),
            "semantic_accuracy_evaluated": validation.get("semantic_accuracy_evaluated", False),
            "schema_checked": validation.get("schema_checked"),
            "errors": _trim_diagnostics(validation.get("errors")),
            "warnings": _trim_diagnostics(validation.get("warnings")),
        },
        "failure": public_failure,
    }


def dispatch_operation(config: ServerConfig, operation: str, payload: Any) -> tuple[int, dict[str, Any]]:
    """Validate one API request and run the corresponding safe in-process operation."""
    body = _require_object(payload)
    if operation == "validate":
        _only_keys(body, {"input", "resume"})
        artifacts = None
    elif operation in {"build", "run"}:
        _only_keys(body, {"input", "artifacts", "resume"})
        artifacts = _artifact_argument(body.get("artifacts"))
    else:
        raise RequestProblem(HTTPStatus.NOT_FOUND, "not_found", "unknown operation")
    input_value = _input_argument(body.get("input"))
    resume = body.get("resume", True)
    if not isinstance(resume, bool):
        raise RequestProblem(HTTPStatus.BAD_REQUEST, "invalid_resume", "resume must be a boolean")

    try:
        run_pipeline.assert_toolchain_unchanged(config.toolchain_fingerprint)
        result = run_pipeline.execute_pipeline(
            input_value,
            input_root=config.input_root,
            output_root=config.output_root,
            operation=operation,
            artifacts=artifacts,
            max_input_bytes=config.max_input_bytes,
            max_run_bytes=config.max_run_bytes,
            max_output_root_bytes=config.max_output_root_bytes,
            resume=resume,
            expected_toolchain_fingerprint=config.toolchain_fingerprint,
        )
    except run_pipeline.ToolchainChangedError as exc:
        raise RequestProblem(
            HTTPStatus.SERVICE_UNAVAILABLE,
            "toolchain_changed",
            "toolchain changed after server startup; restart required",
        ) from exc
    except run_pipeline.OutputQuotaError as exc:
        raise RequestProblem(
            HTTPStatus.INSUFFICIENT_STORAGE,
            "output_quota",
            "configured output byte limit would be exceeded",
        ) from exc
    except run_pipeline.PathSandboxError as exc:
        raise RequestProblem(
            HTTPStatus.FORBIDDEN, "path_rejected", "input path was rejected"
        ) from exc
    except FileNotFoundError as exc:
        raise RequestProblem(
            HTTPStatus.NOT_FOUND, "input_not_found", "input file was not found"
        ) from exc
    except run_pipeline.InputTooLargeError as exc:
        raise RequestProblem(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "input_too_large", str(exc)) from exc
    except run_pipeline.InvalidRequestError as exc:
        raise RequestProblem(HTTPStatus.BAD_REQUEST, "invalid_request", str(exc)) from exc
    except (OSError, run_pipeline.PipelineError) as exc:
        raise RequestProblem(
            HTTPStatus.INTERNAL_SERVER_ERROR,
            "pipeline_error",
            "pipeline execution failed; inspect the local manifest",
        ) from exc

    failure_type = ((result.get("manifest") or {}).get("failure") or {}).get("type")
    if failure_type == "ToolchainChangedError":
        raise RequestProblem(
            HTTPStatus.SERVICE_UNAVAILABLE,
            "toolchain_changed",
            "toolchain changed after server startup; restart required",
        )
    if failure_type == "OutputQuotaError":
        raise RequestProblem(
            HTTPStatus.INSUFFICIENT_STORAGE,
            "output_quota",
            "configured output byte limit would be exceeded",
        )
    status = HTTPStatus.OK if result["status"] == "succeeded" else HTTPStatus.UNPROCESSABLE_ENTITY
    return status, api_summary(result)


def _tool_definitions() -> list[dict[str, Any]]:
    input_property = {
        "type": "string",
        "description": "Relative *.knowledge.json path below the configured input root.",
        "maxLength": MAX_INPUT_ARGUMENT_CHARS,
    }
    return [
        {
            "name": "knowledge_validate",
            "description": "Validate one sandboxed local Knowledge Distiller graph.",
            "inputSchema": {
                "type": "object",
                "required": ["input"],
                "additionalProperties": False,
                "properties": {"input": input_property, "resume": {"type": "boolean"}},
            },
        },
        {
            "name": "knowledge_build",
            "description": "Derive and validate safe local Knowledge Distiller artifacts.",
            "inputSchema": {
                "type": "object",
                "required": ["input"],
                "additionalProperties": False,
                "properties": {
                    "input": input_property,
                    "artifacts": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": len(run_pipeline.ALLOWED_ARTIFACTS),
                        "uniqueItems": True,
                        "items": {"enum": sorted(run_pipeline.ALLOWED_ARTIFACTS)},
                    },
                    "resume": {"type": "boolean"},
                },
            },
        },
    ]


def _jsonrpc_error(request_id: Any, code: int, message: str, data: Any = None) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": request_id, "error": error}


def dispatch_mcp(config: ServerConfig, request: Any) -> tuple[int, dict[str, Any] | None]:
    """Dispatch a minimal MCP-compatible JSON-RPC 2.0 request."""
    if not isinstance(request, dict):
        return HTTPStatus.BAD_REQUEST, _jsonrpc_error(None, -32600, "Invalid Request")
    request_id = request.get("id")
    if request.get("jsonrpc") != "2.0" or not isinstance(request.get("method"), str):
        return HTTPStatus.BAD_REQUEST, _jsonrpc_error(request_id, -32600, "Invalid Request")
    if "id" in request and not (
        request_id is None
        or isinstance(request_id, str)
        or (isinstance(request_id, (int, float)) and not isinstance(request_id, bool))
    ):
        return HTTPStatus.BAD_REQUEST, _jsonrpc_error(None, -32600, "Invalid Request")
    unknown = set(request) - {"jsonrpc", "id", "method", "params"}
    if unknown:
        return HTTPStatus.BAD_REQUEST, _jsonrpc_error(request_id, -32600, "Invalid Request")
    method = request["method"]
    params = request.get("params", {})
    if not isinstance(params, dict):
        return HTTPStatus.OK, _jsonrpc_error(request_id, -32602, "Invalid params")

    if method == "initialize":
        result = {
            "protocolVersion": MCP_PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "knowledge-distiller-local", "version": API_VERSION},
            "instructions": "Local-only validation and deterministic artifact derivation.",
        }
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        try:
            _only_keys(params, {"cursor"}, "params")
        except RequestProblem as exc:
            return HTTPStatus.OK, _jsonrpc_error(request_id, -32602, "Invalid params", exc.message)
        if params.get("cursor") not in (None, ""):
            return HTTPStatus.OK, _jsonrpc_error(request_id, -32602, "Invalid cursor")
        result = {"tools": _tool_definitions()}
    elif method == "tools/call":
        try:
            _only_keys(params, {"name", "arguments"}, "params")
            name = params.get("name")
            arguments = _require_object(params.get("arguments", {}), "arguments")
            if name == "knowledge_validate":
                _, summary = dispatch_operation(config, "validate", arguments)
            elif name == "knowledge_build":
                _, summary = dispatch_operation(config, "build", arguments)
            else:
                return HTTPStatus.OK, _jsonrpc_error(request_id, -32602, "Unknown tool")
            result = {
                "content": [{"type": "text", "text": json.dumps(summary, ensure_ascii=False, sort_keys=True)}],
                "structuredContent": summary,
                "isError": summary.get("status") != "succeeded",
            }
        except RequestProblem as exc:
            result = {
                "content": [{"type": "text", "text": exc.message}],
                "structuredContent": {"error": exc.code, "message": exc.message},
                "isError": True,
            }
    elif method == "notifications/initialized":
        # JSON-RPC notifications have no response.
        return HTTPStatus.NO_CONTENT, None
    else:
        return HTTPStatus.OK, _jsonrpc_error(request_id, -32601, "Method not found")

    if "id" not in request:
        return HTTPStatus.NO_CONTENT, None
    return HTTPStatus.OK, {"jsonrpc": "2.0", "id": request_id, "result": result}


class LocalAPIServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        address: tuple[str, int],
        config: ServerConfig,
        *,
        bearer_token: str,
        max_concurrent_requests: int,
        max_concurrent_runs: int,
    ):
        self.config = config
        self.bearer_token = bearer_token
        self.request_slots = threading.BoundedSemaphore(max_concurrent_requests)
        self.run_slots = threading.BoundedSemaphore(max_concurrent_runs)
        self.max_concurrent_requests = max_concurrent_requests
        self.max_concurrent_runs = max_concurrent_runs
        super().__init__(address, LocalAPIHandler)

    def _capacity_response(self, request: Any) -> None:
        body = _json_bytes(
            {"error": "request_capacity", "message": "server request capacity is full"}
        )
        if len(body) > self.config.max_response_bytes:
            body = b""
        response = (
            b"HTTP/1.1 503 Service Unavailable\r\n"
            b"Content-Type: application/json; charset=utf-8\r\n"
            + f"Content-Length: {len(body)}\r\n".encode("ascii")
            + b"Cache-Control: no-store\r\n"
            + b"X-Content-Type-Options: nosniff\r\n"
            + b"Connection: close\r\n\r\n"
            + body
        )
        with contextlib.suppress(OSError):
            request.sendall(response)
        self.shutdown_request(request)

    def process_request(self, request: Any, client_address: Any) -> None:
        if not self.request_slots.acquire(blocking=False):
            self._capacity_response(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self.request_slots.release()
            raise

    def process_request_thread(self, request: Any, client_address: Any) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.request_slots.release()

    def sanitize_payload(self, value: Any) -> Any:
        """Remove process secrets and configured absolute roots from responses."""
        if isinstance(value, str):
            redacted = value.replace(self.bearer_token, "[redacted]")
            for root in sorted(self.config.redaction_roots, key=len, reverse=True):
                redacted = redacted.replace(root, "[redacted-path]")
            return redacted
        if isinstance(value, list):
            return [self.sanitize_payload(item) for item in value]
        if isinstance(value, dict):
            return {
                self.sanitize_payload(key): self.sanitize_payload(item)
                for key, item in value.items()
            }
        return value


class LocalAPIHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "KnowledgeDistillerLocal/" + API_VERSION
    sys_version = ""
    server: LocalAPIServer

    def setup(self) -> None:
        super().setup()
        self.connection.settimeout(10)

    def log_message(self, _format: str, *args: Any) -> None:
        # Avoid writing request paths or user-controlled values to an implicit log.
        return

    def _peer_is_loopback(self) -> bool:
        try:
            return ipaddress.ip_address(self.client_address[0]).is_loopback
        except ValueError:
            return False

    def _expected_authority(self) -> str:
        return f"127.0.0.1:{self.server.server_port}"

    def _guard_request(self) -> None:
        if not self._peer_is_loopback():
            raise RequestProblem(
                HTTPStatus.FORBIDDEN,
                "loopback_only",
                "server accepts loopback clients only",
            )
        hosts = self.headers.get_all("Host", failobj=[])
        if len(hosts) != 1 or hosts[0] != self._expected_authority():
            raise RequestProblem(
                HTTPStatus.BAD_REQUEST,
                "host_rejected",
                "Host must match the bound loopback address and port",
            )
        origins = self.headers.get_all("Origin", failobj=[])
        expected_origin = "http://" + self._expected_authority()
        if len(origins) > 1 or (origins and origins[0] != expected_origin):
            raise RequestProblem(
                HTTPStatus.FORBIDDEN,
                "origin_rejected",
                "cross-origin requests are not accepted",
            )
        authorizations = self.headers.get_all("Authorization", failobj=[])
        if len(authorizations) != 1 or not authorizations[0].startswith("Bearer "):
            raise RequestProblem(
                HTTPStatus.UNAUTHORIZED,
                "authentication_required",
                "a valid bearer token is required",
            )
        supplied = authorizations[0][len("Bearer ") :]
        if (
            not supplied
            or any(ord(character) < 33 or ord(character) > 126 for character in supplied)
            or not hmac.compare_digest(supplied, self.server.bearer_token)
        ):
            raise RequestProblem(
                HTTPStatus.UNAUTHORIZED,
                "authentication_required",
                "a valid bearer token is required",
            )

    def _target(self):
        target = urlsplit(self.path)
        if (
            not self.path.startswith("/")
            or target.scheme
            or target.netloc
            or target.fragment
        ):
            raise RequestProblem(
                HTTPStatus.BAD_REQUEST,
                "target_rejected",
                "request target must be a local origin-form path",
            )
        return target

    def _send(
        self,
        status: int,
        payload: Any | None,
        *,
        extra_headers: tuple[tuple[str, str], ...] = (),
    ) -> None:
        if payload is None:
            body = b""
        else:
            body = _json_bytes(self.server.sanitize_payload(payload))
            if len(body) > self.server.config.max_response_bytes:
                status = HTTPStatus.INTERNAL_SERVER_ERROR
                body = _json_bytes({"error": "response_too_large", "message": "bounded response limit exceeded"})
                if len(body) > self.server.config.max_response_bytes:
                    body = b""
        self.send_response(int(status))
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        for name, value in extra_headers:
            self.send_header(name, value)
        self.send_header("Connection", "close")
        self.end_headers()
        if body:
            self.wfile.write(body)
        self.close_connection = True

    def _problem(self, problem: RequestProblem) -> None:
        headers = (
            (("WWW-Authenticate", 'Bearer realm="knowledge-distiller-local"'),)
            if problem.status == HTTPStatus.UNAUTHORIZED
            else ()
        )
        self._send(
            problem.status,
            {"error": problem.code, "message": problem.message},
            extra_headers=headers,
        )

    def _read_json(self) -> Any:
        if self.headers.get("Transfer-Encoding"):
            raise RequestProblem(HTTPStatus.BAD_REQUEST, "transfer_encoding_rejected", "chunked request bodies are not accepted")
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if content_type != "application/json":
            raise RequestProblem(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "content_type", "Content-Type must be application/json")
        length_header = self.headers.get("Content-Length")
        if length_header is None:
            raise RequestProblem(HTTPStatus.LENGTH_REQUIRED, "length_required", "Content-Length is required")
        try:
            length = int(length_header)
        except ValueError as exc:
            raise RequestProblem(HTTPStatus.BAD_REQUEST, "invalid_length", "invalid Content-Length") from exc
        if length < 1:
            raise RequestProblem(HTTPStatus.BAD_REQUEST, "empty_body", "request body is empty")
        if length > self.server.config.max_body_bytes:
            raise RequestProblem(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "body_too_large", "request body exceeds the configured limit")
        raw = self.rfile.read(length)
        if len(raw) != length:
            raise RequestProblem(HTTPStatus.BAD_REQUEST, "truncated_body", "request body is truncated")
        try:
            return run_pipeline.strict_json.loads(raw, source="request body")
        except run_pipeline.strict_json.StrictJsonError as exc:
            raise RequestProblem(HTTPStatus.BAD_REQUEST, "invalid_json", "request body is not valid JSON") from exc

    def do_GET(self) -> None:  # noqa: N802
        try:
            self._guard_request()
            target = self._target()
            if target.query:
                raise RequestProblem(
                    HTTPStatus.BAD_REQUEST,
                    "query_rejected",
                    "query strings are not accepted",
                )
            if target.path == "/health" and not target.query:
                self._send(
                    HTTPStatus.OK,
                    {
                        "status": "ok",
                        "service": "knowledge-distiller-local",
                        "version": API_VERSION,
                        "runner_version": run_pipeline.RUNNER_VERSION,
                        "network_access": False,
                        "telemetry": False,
                    },
                )
            else:
                raise RequestProblem(
                    HTTPStatus.NOT_FOUND, "not_found", "endpoint not found"
                )
        except RequestProblem as exc:
            self._problem(exc)
        except Exception:
            self._problem(
                RequestProblem(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    "internal_error",
                    "internal server error",
                )
            )

    def do_POST(self) -> None:  # noqa: N802
        try:
            self._guard_request()
            target = self._target()
            if target.query:
                raise RequestProblem(
                    HTTPStatus.BAD_REQUEST,
                    "query_rejected",
                    "query strings are not accepted",
                )
            payload = self._read_json()
            needs_run = target.path in {"/v1/validate", "/v1/build", "/v1/run"} or (
                target.path == "/mcp"
                and isinstance(payload, dict)
                and payload.get("method") == "tools/call"
            )
            if needs_run and not self.server.run_slots.acquire(blocking=False):
                raise RequestProblem(
                    HTTPStatus.SERVICE_UNAVAILABLE,
                    "run_capacity",
                    "server run capacity is full",
                )
            try:
                if target.path == "/v1/validate":
                    status, response = dispatch_operation(self.server.config, "validate", payload)
                elif target.path in {"/v1/build", "/v1/run"}:
                    status, response = dispatch_operation(self.server.config, "build", payload)
                elif target.path == "/mcp":
                    status, response = dispatch_mcp(self.server.config, payload)
                else:
                    raise RequestProblem(
                        HTTPStatus.NOT_FOUND, "not_found", "endpoint not found"
                    )
            finally:
                if needs_run:
                    self.server.run_slots.release()
            self._send(status, response)
        except RequestProblem as exc:
            self._problem(exc)
        except Exception:
            # Do not expose tracebacks, filesystem paths, or request data.
            self._problem(RequestProblem(HTTPStatus.INTERNAL_SERVER_ERROR, "internal_error", "internal server error"))

    def do_OPTIONS(self) -> None:  # noqa: N802
        try:
            self._guard_request()
            self._target()
            raise RequestProblem(
                HTTPStatus.METHOD_NOT_ALLOWED,
                "method_not_allowed",
                "CORS preflight is not supported",
            )
        except RequestProblem as exc:
            self._problem(exc)

    def _unsupported_method(self) -> None:
        try:
            self._guard_request()
            self._target()
            raise RequestProblem(
                HTTPStatus.METHOD_NOT_ALLOWED,
                "method_not_allowed",
                "HTTP method is not supported",
            )
        except RequestProblem as exc:
            self._problem(exc)

    do_HEAD = _unsupported_method
    do_PUT = _unsupported_method
    do_PATCH = _unsupported_method
    do_DELETE = _unsupported_method
    do_TRACE = _unsupported_method
    do_CONNECT = _unsupported_method


def create_server(
    *,
    input_root: str | os.PathLike[str],
    output_root: str | os.PathLike[str],
    host: str = "127.0.0.1",
    port: int = 8765,
    max_body_bytes: int = DEFAULT_MAX_BODY_BYTES,
    max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
    max_input_bytes: int = run_pipeline.DEFAULT_MAX_INPUT_BYTES,
    max_run_bytes: int = run_pipeline.DEFAULT_MAX_RUN_BYTES,
    max_output_root_bytes: int = run_pipeline.DEFAULT_MAX_OUTPUT_ROOT_BYTES,
    bearer_token: str | None = None,
    max_concurrent_requests: int = DEFAULT_MAX_CONCURRENT_REQUESTS,
    max_concurrent_runs: int = DEFAULT_MAX_CONCURRENT_RUNS,
) -> LocalAPIServer:
    if host != "127.0.0.1":
        raise ValueError("this server may bind only to 127.0.0.1")
    if not isinstance(port, int) or isinstance(port, bool) or not (0 <= port <= 65535):
        raise ValueError("port must be between 0 and 65535")
    size_limits = (
        max_body_bytes,
        max_response_bytes,
        max_input_bytes,
        max_run_bytes,
        max_output_root_bytes,
    )
    if any(
        not isinstance(limit, int) or isinstance(limit, bool) or limit < 1
        for limit in size_limits
    ):
        raise ValueError("size limits must be positive")
    if (
        not isinstance(max_concurrent_requests, int)
        or isinstance(max_concurrent_requests, bool)
        or max_concurrent_requests < 1
        or not isinstance(max_concurrent_runs, int)
        or isinstance(max_concurrent_runs, bool)
        or max_concurrent_runs < 1
        or max_concurrent_runs > max_concurrent_requests
    ):
        raise ValueError(
            "concurrency limits must be positive integers and runs may not exceed requests"
        )
    token = bearer_token if bearer_token is not None else secrets.token_urlsafe(32)
    if (
        not isinstance(token, str)
        or not (MIN_BEARER_TOKEN_CHARS <= len(token) <= MAX_BEARER_TOKEN_CHARS)
        or any(ord(character) < 33 or ord(character) > 126 for character in token)
    ):
        raise ValueError(
            f"bearer token must contain {MIN_BEARER_TOKEN_CHARS}-{MAX_BEARER_TOKEN_CHARS} visible ASCII characters"
        )
    declared_in_root = Path(input_root).expanduser().absolute()
    in_root = declared_in_root.resolve(strict=True)
    if not in_root.is_dir():
        raise ValueError("input root must be a directory")
    try:
        toolchain_fingerprint = run_pipeline.assert_toolchain_unchanged()
    except run_pipeline.ToolchainChangedError as exc:
        raise ValueError("toolchain changed before server startup; restart required") from exc
    declared_out_root = Path(output_root).expanduser().absolute()
    try:
        out_root = run_pipeline._prepare_output_root(declared_out_root)
    except run_pipeline.PathSandboxError as exc:
        raise ValueError("output root cannot be anchored safely") from exc
    config = ServerConfig(
        input_root=in_root,
        output_root=out_root,
        toolchain_fingerprint=toolchain_fingerprint,
        redaction_roots=tuple(
            sorted(
                {
                    str(declared_in_root),
                    str(in_root),
                    str(declared_out_root),
                    str(out_root),
                }
            )
        ),
        max_body_bytes=max_body_bytes,
        max_response_bytes=max_response_bytes,
        max_input_bytes=max_input_bytes,
        max_run_bytes=max_run_bytes,
        max_output_root_bytes=max_output_root_bytes,
    )
    return LocalAPIServer(
        (host, port),
        config,
        bearer_token=token,
        max_concurrent_requests=max_concurrent_requests,
        max_concurrent_runs=max_concurrent_runs,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Serve the guarded local Knowledge Distiller API")
    parser.add_argument("--input-root", required=True, help="Sandbox root for readable graph inputs")
    parser.add_argument("--output-root", required=True, help="Explicit root for all generated runs")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--max-body-bytes", type=int, default=DEFAULT_MAX_BODY_BYTES)
    parser.add_argument("--max-response-bytes", type=int, default=DEFAULT_MAX_RESPONSE_BYTES)
    parser.add_argument("--max-input-bytes", type=int, default=run_pipeline.DEFAULT_MAX_INPUT_BYTES)
    parser.add_argument(
        "--max-run-bytes",
        type=int,
        default=run_pipeline.DEFAULT_MAX_RUN_BYTES,
    )
    parser.add_argument(
        "--max-output-root-bytes",
        type=int,
        default=run_pipeline.DEFAULT_MAX_OUTPUT_ROOT_BYTES,
    )
    token_source = parser.add_mutually_exclusive_group()
    token_source.add_argument(
        "--token",
        help=(
            f"Explicit {MIN_BEARER_TOKEN_CHARS}+-character bearer token; "
            "if omitted, a random token is printed once at startup"
        ),
    )
    token_source.add_argument(
        "--token-file",
        help=(
            "Read the bearer token from an owner-only 0400/0600 regular file "
            f"of at most {MAX_TOKEN_FILE_BYTES} bytes"
        ),
    )
    parser.add_argument(
        "--max-concurrent-requests",
        type=int,
        default=DEFAULT_MAX_CONCURRENT_REQUESTS,
    )
    parser.add_argument(
        "--max-concurrent-runs",
        type=int,
        default=DEFAULT_MAX_CONCURRENT_RUNS,
    )
    args = parser.parse_args(argv)
    try:
        bearer_token = (
            _read_token_file(args.token_file)
            if args.token_file is not None
            else args.token
        )
        server = create_server(
            input_root=args.input_root,
            output_root=args.output_root,
            port=args.port,
            max_body_bytes=args.max_body_bytes,
            max_response_bytes=args.max_response_bytes,
            max_input_bytes=args.max_input_bytes,
            max_run_bytes=args.max_run_bytes,
            max_output_root_bytes=args.max_output_root_bytes,
            bearer_token=bearer_token,
            max_concurrent_requests=args.max_concurrent_requests,
            max_concurrent_runs=args.max_concurrent_runs,
        )
    except (OSError, ValueError) as exc:
        print(f"server configuration rejected: {exc}", file=sys.stderr)
        return 2
    print(f"Knowledge Distiller local API listening on http://127.0.0.1:{server.server_port}")
    if args.token is None and args.token_file is None:
        print(
            "One-time local bearer token (store it securely; it will not be shown again): "
            + server.bearer_token,
            file=sys.stderr,
        )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
