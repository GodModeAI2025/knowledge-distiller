#!/usr/bin/env python3
"""Deterministic, local-only runner for Knowledge Distiller graphs.

The runner deliberately does not extract documents or call a model.  It is the
small production-shaped layer around the deterministic tools already in this
repository:

* validate an existing ``*.knowledge.json`` graph; or
* derive the canonical graph and selected local artifacts, then validate them.

Every execution lives below an explicit output root.  Requests are content
addressed, stage receipts and final manifests are write-once, successful runs
are reused only after their output hashes have been verified, and an interrupted
attempt can resume from verified stage receipts.  No subprocess, network, or
telemetry facility is used here.
"""

from __future__ import annotations

import argparse
import contextlib
import copy
import datetime as dt
import errno
import hashlib
import io
import importlib.metadata
import json
import os
import platform
import re
import secrets
import stat
import sys
import tempfile
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterable


SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(SCRIPT_DIR))

import build_bundle  # noqa: E402
import build_exports  # noqa: E402
import build_graph  # noqa: E402
import build_md  # noqa: E402
import build_viewer  # noqa: E402
import strict_json  # noqa: E402
import validate_knowledge  # noqa: E402


RUNNER_VERSION = "0.3.0"
MANIFEST_VERSION = "1.0"
DEFAULT_MAX_INPUT_BYTES = 64 * 1024 * 1024
DEFAULT_MAX_RUN_BYTES = 512 * 1024 * 1024
DEFAULT_MAX_OUTPUT_ROOT_BYTES = 4 * 1024 * 1024 * 1024
MAX_INTERNAL_RECORD_BYTES = 64 * 1024 * 1024
SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")
TEXT_ARTIFACTS: tuple[tuple[str, str, str], ...] = (
    ("md", "graph.knowledge.md", "knowledge_markdown"),
    ("html", "graph.knowledge.html", "knowledge_html"),
    ("cypher", "graph.knowledge.cypher", "knowledge_cypher"),
    ("ctxt", "graph.knowledge.ctxt", "knowledge_ctxt"),
    ("canvas", "graph.knowledge.canvas", "knowledge_canvas"),
)
ALLOWED_ARTIFACTS = frozenset(
    {"json", "bundle"} | {artifact for artifact, _, _ in TEXT_ARTIFACTS}
)

SPEC_PATH = REPO_ROOT / "SPEC.md"
SCHEMA_PATH = REPO_ROOT / "schema" / "knowledge.schema.json"
TOOL_PATHS = {
    "runner": Path(__file__).resolve(),
    "api_server": SCRIPT_DIR / "serve_api.py",
    "build_graph": SCRIPT_DIR / "build_graph.py",
    "build_md": SCRIPT_DIR / "build_md.py",
    "build_bundle": SCRIPT_DIR / "build_bundle.py",
    "build_exports": SCRIPT_DIR / "build_exports.py",
    "build_viewer": SCRIPT_DIR / "build_viewer.py",
    "viewer_template": REPO_ROOT / "viewer" / "template.html",
    "viewer_css": REPO_ROOT / "viewer" / "viz.css",
    "viewer_javascript": REPO_ROOT / "viewer" / "viz.js",
    "validator": SCRIPT_DIR / "validate_knowledge.py",
    "spec": SPEC_PATH,
    "schema": SCHEMA_PATH,
    "strict_json": SCRIPT_DIR / "strict_json.py",
}
# Python modules are already loaded when an API server is created.  Capture the
# process baseline immediately so receipts describe the code this process loaded,
# not a later on-disk edit that the running interpreter did not import.
_PROCESS_TOOL_HASHES = {
    name: hashlib.sha256(path.read_bytes()).hexdigest()
    for name, path in sorted(TOOL_PATHS.items())
}


class PipelineError(Exception):
    """Base class for expected runner failures."""


class PathSandboxError(PipelineError):
    """A caller tried to access a path outside its configured root."""


class InputTooLargeError(PipelineError):
    """The selected input exceeds the configured byte limit."""


class InvalidRequestError(PipelineError):
    """A runner argument is invalid."""


class OutputQuotaError(PipelineError):
    """A run or its shared output root would exceed its configured byte budget."""


class ToolchainChangedError(PipelineError):
    """Files backing a long-lived process changed and require a restart."""


class StageFailure(PipelineError):
    """A stage failed after it may have produced diagnostic outputs."""

    def __init__(
        self,
        message: str,
        *,
        output_specs: Iterable[tuple[str, str]] = (),
        details: dict[str, Any] | None = None,
        code: str = "stage_failed",
    ) -> None:
        super().__init__(message)
        self.output_specs = list(output_specs)
        self.details = details or {}
        self.code = code


_FALLBACK_LOCKS: dict[str, threading.Lock] = {}
_FALLBACK_LOCKS_GUARD = threading.Lock()
_QUOTA_LOCKS: dict[str, threading.Lock] = {}
_QUOTA_LOCKS_GUARD = threading.Lock()
_TOOLCHAIN_STATE_LOCK = threading.Lock()
_TOOLCHAIN_CHANGED = False


@dataclass(frozen=True)
class QuotaPolicy:
    output_root: Path
    run_dir: Path
    max_run_bytes: int
    max_output_root_bytes: int


def _utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _pretty_json(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)
        + "\n"
    ).encode("utf-8")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    fd = _open_regular_path(path)
    try:
        while True:
            chunk = os.read(fd, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    finally:
        os.close(fd)
    return digest.hexdigest()


def _tree_digest(path: Path) -> tuple[str, int, int]:
    """Return a stable tree hash, total bytes, and file count for ``path``."""
    digest = hashlib.sha256()
    total = 0
    count = 0
    entries = list(path.rglob("*"))
    if any(item.is_symlink() for item in entries):
        raise PipelineError("generated output trees may not contain symlinks")
    for item in sorted((p for p in entries if p.is_file()), key=lambda p: p.as_posix()):
        rel = item.relative_to(path).as_posix()
        size = item.stat().st_size
        item_hash = sha256_file(item)
        digest.update(rel.encode("utf-8"))
        digest.update(b"\0")
        digest.update(item_hash.encode("ascii"))
        digest.update(b"\0")
        digest.update(str(size).encode("ascii"))
        digest.update(b"\n")
        total += size
        count += 1
    return digest.hexdigest(), total, count


def _directory_usage_fd(directory_fd: int) -> int:
    """Count regular-file payload bytes below one descriptor-anchored directory."""
    total = 0
    try:
        with os.scandir(directory_fd) as scanned:
            entries = list(scanned)
    except OSError as exc:
        raise PathSandboxError("output quota tree cannot be inspected safely") from exc
    for entry in entries:
        try:
            identity = entry.stat(follow_symlinks=False)
        except OSError as exc:
            raise PathSandboxError("output quota tree changed during inspection") from exc
        if stat.S_ISLNK(identity.st_mode):
            # Never follow an unsafe historical entry.  Its link payload still
            # counts toward the root budget; attempt validation decides whether
            # that attempt can be reused or resumed.
            total += identity.st_size
            continue
        if stat.S_ISREG(identity.st_mode):
            total += identity.st_size
            continue
        if not stat.S_ISDIR(identity.st_mode):
            raise PathSandboxError(
                "output quota tree may contain only directories and regular files"
            )
        child_fd = _open_component(
            entry.name, directory_fd=directory_fd, directory=True
        )
        try:
            opened = os.fstat(child_fd)
            if (opened.st_dev, opened.st_ino) != (identity.st_dev, identity.st_ino):
                raise PathSandboxError(
                    "output quota tree changed during inspection"
                )
            total += _directory_usage_fd(child_fd)
        finally:
            os.close(child_fd)
    return total


def _directory_usage_bytes(path: Path, *, missing_ok: bool = False) -> int:
    try:
        directory_fd = _open_absolute_directory(path)
    except FileNotFoundError:
        if missing_ok:
            return 0
        raise
    try:
        return _directory_usage_fd(directory_fd)
    finally:
        os.close(directory_fd)


@contextmanager
def _quota_lock(output_root: Path):
    key = str(output_root)
    with _QUOTA_LOCKS_GUARD:
        local_lock = _QUOTA_LOCKS.setdefault(key, threading.Lock())
    with local_lock:
        with _request_lock(output_root, "__quota_global__"):
            yield


@contextmanager
def _quota_reservation(policy: QuotaPolicy | None, additional_bytes: int):
    """Hold the shared quota lock while a bounded output mutation is installed."""
    if policy is None:
        yield
        return
    if additional_bytes < 0:
        raise OutputQuotaError("output quota reservation cannot be negative")
    with _quota_lock(policy.output_root):
        root_bytes = _directory_usage_bytes(policy.output_root)
        run_bytes = _directory_usage_bytes(policy.run_dir, missing_ok=True)
        if root_bytes > policy.max_output_root_bytes:
            raise OutputQuotaError(
                "output root already exceeds its configured byte limit"
            )
        if run_bytes > policy.max_run_bytes:
            raise OutputQuotaError("run directory already exceeds its configured byte limit")
        if root_bytes + additional_bytes > policy.max_output_root_bytes:
            raise OutputQuotaError("output root byte limit would be exceeded")
        if run_bytes + additional_bytes > policy.max_run_bytes:
            raise OutputQuotaError("run directory byte limit would be exceeded")
        yield


def _atomic_write(
    path: Path, data: bytes, *, quota: QuotaPolicy | None = None
) -> None:
    """Atomically replace an unfinished output inside a run attempt."""
    with _quota_reservation(quota, len(data)):
        parent_fd = _open_absolute_directory(path.parent, create=True)
        temp_name = f".{path.name}.{secrets.token_hex(16)}.tmp"
        fd: int | None = None
        try:
            _reject_nonregular_destination(path.name, directory_fd=parent_fd)
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0)
            flags |= getattr(os, "O_NOFOLLOW", 0)
            fd = os.open(temp_name, flags, 0o600, dir_fd=parent_fd)
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "wb") as handle:
                fd = None
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(
                temp_name,
                path.name,
                src_dir_fd=parent_fd,
                dst_dir_fd=parent_fd,
            )
        except Exception:
            if fd is not None:
                os.close(fd)
            with contextlib.suppress(FileNotFoundError):
                os.unlink(temp_name, dir_fd=parent_fd)
            raise
        finally:
            os.close(parent_fd)


def _write_once(
    path: Path, data: bytes, *, quota: QuotaPolicy | None = None
) -> None:
    """Create ``path`` exactly once; never overwrite a receipt or manifest."""
    with _quota_reservation(quota, len(data)):
        parent_fd = _open_absolute_directory(path.parent, create=True)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        try:
            fd = os.open(path.name, flags, 0o600, dir_fd=parent_fd)
        except FileExistsError as exc:
            os.close(parent_fd)
            raise PipelineError(f"write-once record already exists: {path.name}") from exc
        except Exception:
            os.close(parent_fd)
            raise
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
        finally:
            os.close(parent_fd)


def _copy_directory_contents(
    source_fd: int, destination_fd: int, remaining_bytes: list[int]
) -> None:
    """Copy a generated tree between anchored directory descriptors."""
    with os.scandir(source_fd) as scanned:
        entries = list(scanned)
    for entry in entries:
        identity = entry.stat(follow_symlinks=False)
        if stat.S_ISLNK(identity.st_mode):
            raise PipelineError("generated output trees may not contain symlinks")
        if stat.S_ISDIR(identity.st_mode):
            source_child = _open_component(
                entry.name, directory_fd=source_fd, directory=True
            )
            try:
                opened = os.fstat(source_child)
                if (opened.st_dev, opened.st_ino) != (
                    identity.st_dev,
                    identity.st_ino,
                ):
                    raise PathSandboxError(
                        "generated output tree changed during installation"
                    )
                os.mkdir(entry.name, 0o700, dir_fd=destination_fd)
                destination_child = _open_component(
                    entry.name, directory_fd=destination_fd, directory=True
                )
                try:
                    _copy_directory_contents(
                        source_child, destination_child, remaining_bytes
                    )
                finally:
                    os.close(destination_child)
            finally:
                os.close(source_child)
            continue
        if not stat.S_ISREG(identity.st_mode):
            raise PipelineError(
                "generated output trees may contain only directories and regular files"
            )
        if identity.st_size > remaining_bytes[0]:
            raise OutputQuotaError(
                "generated directory grew beyond its reserved byte budget"
            )
        source_file = _open_component(
            entry.name, directory_fd=source_fd, directory=False
        )
        opened = os.fstat(source_file)
        if (opened.st_dev, opened.st_ino) != (identity.st_dev, identity.st_ino):
            os.close(source_file)
            raise PathSandboxError(
                "generated output tree changed during installation"
            )
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        try:
            destination_file = os.open(
                entry.name, flags, 0o600, dir_fd=destination_fd
            )
        except Exception:
            os.close(source_file)
            raise
        copied = 0
        with os.fdopen(source_file, "rb") as source_handle, os.fdopen(
            destination_file, "wb"
        ) as destination_handle:
            while True:
                chunk = source_handle.read(
                    min(1024 * 1024, identity.st_size - copied + 1)
                )
                if not chunk:
                    break
                copied += len(chunk)
                if copied > identity.st_size or copied > remaining_bytes[0]:
                    raise OutputQuotaError(
                        "generated directory grew beyond its reserved byte budget"
                    )
                destination_handle.write(chunk)
            if copied != identity.st_size:
                raise PipelineError(
                    "generated output tree changed during installation"
                )
            destination_handle.flush()
            os.fsync(destination_handle.fileno())
        remaining_bytes[0] -= copied


def _remove_directory_at(parent_fd: int, name: str) -> None:
    """Best-effort descriptor-relative cleanup for a private staging directory."""
    child_fd = _open_component(name, directory_fd=parent_fd, directory=True)
    try:
        with os.scandir(child_fd) as scanned:
            entries = list(scanned)
        for entry in entries:
            identity = entry.stat(follow_symlinks=False)
            if stat.S_ISDIR(identity.st_mode) and not stat.S_ISLNK(identity.st_mode):
                _remove_directory_at(child_fd, entry.name)
            else:
                os.unlink(entry.name, dir_fd=child_fd)
    finally:
        os.close(child_fd)
    os.rmdir(name, dir_fd=parent_fd)


def _install_directory_tree(
    source: Path, destination: Path, *, quota: QuotaPolicy
) -> None:
    """Install a fully rendered generated directory without partial visibility."""
    _, total_bytes, _ = _tree_digest(source)
    with _quota_reservation(quota, total_bytes):
        parent_fd = _open_absolute_directory(destination.parent, create=True)
        staging_name = f".{destination.name}.{secrets.token_hex(16)}.tmp"
        staging_created = False
        try:
            try:
                os.stat(
                    destination.name,
                    dir_fd=parent_fd,
                    follow_symlinks=False,
                )
            except FileNotFoundError:
                pass
            else:
                raise StageFailure(
                    "refusing to overwrite an unfinished bundle destination",
                    code="artifact_collision",
                )
            os.mkdir(staging_name, 0o700, dir_fd=parent_fd)
            staging_created = True
            destination_fd = _open_component(
                staging_name, directory_fd=parent_fd, directory=True
            )
            source_fd = _open_absolute_directory(source)
            try:
                remaining = [total_bytes]
                _copy_directory_contents(source_fd, destination_fd, remaining)
                if remaining[0] != 0:
                    raise PipelineError(
                        "generated output tree changed during installation"
                    )
            finally:
                os.close(source_fd)
                os.close(destination_fd)
            os.rename(
                staging_name,
                destination.name,
                src_dir_fd=parent_fd,
                dst_dir_fd=parent_fd,
            )
            staging_created = False
        finally:
            if staging_created:
                with contextlib.suppress(Exception):
                    _remove_directory_at(parent_fd, staging_name)
            os.close(parent_fd)


def _load_json(path: Path) -> Any:
    fd = _open_regular_path(path)
    try:
        data = _read_fd_bounded(fd, MAX_INTERNAL_RECORD_BYTES)
    finally:
        os.close(fd)
    return strict_json.loads(data, source=str(path))


def _logical_input_path(
    input_value: str | os.PathLike[str], input_root: str | os.PathLike[str]
) -> tuple[Path, str, tuple[int, int]]:
    """Select a lexical input name; descriptor-relative opening is separate."""
    value = os.fspath(input_value)
    if not isinstance(value, str) or not value or "\x00" in value:
        raise PathSandboxError("input path must be a non-empty local path without NUL bytes")
    declared_root = Path(input_root).expanduser().absolute()
    try:
        declared_identity = os.stat(declared_root, follow_symlinks=False)
        if stat.S_ISLNK(declared_identity.st_mode):
            raise PathSandboxError("input root must not be a symlink")
        if not stat.S_ISDIR(declared_identity.st_mode):
            raise PathSandboxError("input root must be a directory")
        root = declared_root.resolve(strict=True)
    except (FileNotFoundError, OSError) as exc:
        raise PathSandboxError("input root does not exist or cannot be resolved") from exc
    try:
        resolved_identity = os.stat(root, follow_symlinks=False)
    except OSError as exc:
        raise PathSandboxError("input root changed while it was being resolved") from exc
    expected_root = (declared_identity.st_dev, declared_identity.st_ino)
    if (
        not stat.S_ISDIR(resolved_identity.st_mode)
        or (resolved_identity.st_dev, resolved_identity.st_ino) != expected_root
    ):
        raise PathSandboxError("input root changed while it was being resolved")
    raw = Path(value).expanduser()
    if raw.is_absolute():
        try:
            relative = raw.relative_to(declared_root)
        except ValueError as exc:
            try:
                relative = raw.relative_to(root)
            except ValueError:
                raise PathSandboxError("input path is outside the configured input root") from exc
    else:
        relative = raw
    if not relative.parts or any(part in {"", ".", ".."} for part in relative.parts):
        raise PathSandboxError("input path traversal is not allowed")
    logical_name = relative.as_posix()
    if any(ord(character) < 32 or ord(character) == 127 for character in logical_name):
        raise PathSandboxError("input path contains control characters")
    if not logical_name.endswith(".knowledge.json"):
        raise InvalidRequestError("input must end with .knowledge.json")
    return root, logical_name, expected_root


def _open_component(name: str, *, directory_fd: int, directory: bool) -> int:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    if directory:
        flags |= getattr(os, "O_DIRECTORY", 0)
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    before: os.stat_result | None = None
    if nofollow:
        flags |= nofollow
    else:  # pragma: no cover - current supported POSIX platforms provide O_NOFOLLOW
        try:
            before = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        except OSError as exc:
            raise PathSandboxError("input path cannot be inspected safely") from exc
        if stat.S_ISLNK(before.st_mode):
            raise PathSandboxError("symlinks are not accepted in input paths")
    try:
        fd = os.open(name, flags, dir_fd=directory_fd)
    except OSError as exc:
        if exc.errno == errno.ENOENT:
            raise FileNotFoundError("input file not found") from exc
        if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
            raise PathSandboxError("symlinks are not accepted in input paths") from exc
        raise PathSandboxError("input path cannot be opened safely") from exc
    try:
        opened = os.fstat(fd)
        expected_type = stat.S_ISDIR if directory else stat.S_ISREG
        if not expected_type(opened.st_mode):
            raise PathSandboxError(
                "input path component must be a directory"
                if directory
                else "input must be a regular file"
            )
        if before is not None and (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
            raise PathSandboxError("input path changed while it was being opened")
        return fd
    except Exception:
        os.close(fd)
        raise


def _require_descriptor_relative_io() -> None:
    """Fail closed on platforms without the primitives used by the sandbox."""
    if (
        os.name != "posix"
        or os.open not in getattr(os, "supports_dir_fd", set())
        or os.mkdir not in getattr(os, "supports_dir_fd", set())
        or os.unlink not in getattr(os, "supports_dir_fd", set())
    ):
        raise PathSandboxError(
            "safe path access requires POSIX descriptor-relative filesystem operations"
        )


def _open_absolute_directory(path: Path, *, create: bool = False) -> int:
    """Open an absolute directory by walking every component without symlinks.

    The returned descriptor, rather than the visible pathname, is the authority for
    the caller's next operation.  This closes the root-level resolve/open race that
    remains when only child components use ``dir_fd``.
    """
    _require_descriptor_relative_io()
    absolute = path.expanduser().absolute()
    if not absolute.is_absolute():  # defensive; ``absolute`` should guarantee this
        raise PathSandboxError("directory anchor must be absolute")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_DIRECTORY", 0)
    try:
        current_fd = os.open(os.path.sep, flags)
    except OSError as exc:  # pragma: no cover - a usable POSIX host always opens '/'
        raise PathSandboxError("filesystem root cannot be opened safely") from exc
    try:
        for component in absolute.parts[1:]:
            try:
                next_fd = _open_component(
                    component, directory_fd=current_fd, directory=True
                )
            except FileNotFoundError:
                if not create:
                    raise
                try:
                    os.mkdir(component, 0o700, dir_fd=current_fd)
                except FileExistsError:
                    # A racing creator is acceptable only when the no-follow open
                    # below proves that it created a real directory.
                    pass
                except OSError as exc:
                    raise PathSandboxError(
                        "output directory cannot be created safely"
                    ) from exc
                next_fd = _open_component(
                    component, directory_fd=current_fd, directory=True
                )
            os.close(current_fd)
            current_fd = next_fd
        return current_fd
    except Exception:
        os.close(current_fd)
        raise


def _open_regular_path(path: Path) -> int:
    """Open one regular file relative to a symlink-free parent descriptor."""
    absolute = path.expanduser().absolute()
    # macOS exposes aliases such as /var -> /private/var.  Canonicalise only the
    # parent; the final file component is still opened with O_NOFOLLOW.
    canonical_parent = absolute.parent.resolve(strict=True)
    parent_fd = _open_absolute_directory(canonical_parent)
    try:
        return _open_component(
            absolute.name, directory_fd=parent_fd, directory=False
        )
    finally:
        os.close(parent_fd)


def _reject_nonregular_destination(name: str, *, directory_fd: int) -> None:
    """Allow an absent or regular destination, but never a link/device/directory."""
    try:
        existing = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    except FileNotFoundError:
        return
    if stat.S_ISLNK(existing.st_mode):
        raise PathSandboxError("generated output destination is a symlink")
    if not stat.S_ISREG(existing.st_mode):
        raise PathSandboxError("generated output destination is not a regular file")


def _prepare_output_root(output_root: str | os.PathLike[str]) -> Path:
    """Create and anchor an output root without accepting a root-level symlink."""
    declared = Path(output_root).expanduser().absolute()
    if declared == Path(declared.anchor):
        canonical = declared
    else:
        # Canonicalise aliases in the parent (notably macOS /var -> /private/var)
        # but leave the selected root itself unresolved so a final symlink can
        # never redirect creation between a pre-check and the descriptor walk.
        canonical = declared.parent.resolve(strict=False) / declared.name
    fd = _open_absolute_directory(canonical, create=True)
    os.close(fd)
    return canonical


def _open_input_fd(
    root: Path, logical_name: str, expected_root: tuple[int, int]
) -> int:
    current_fd = _open_absolute_directory(root)
    try:
        opened_root = os.fstat(current_fd)
        if (opened_root.st_dev, opened_root.st_ino) != expected_root:
            raise PathSandboxError("input root changed while it was being opened")
        parts = Path(logical_name).parts
        for part in parts[:-1]:
            next_fd = _open_component(part, directory_fd=current_fd, directory=True)
            os.close(current_fd)
            current_fd = next_fd
        return _open_component(parts[-1], directory_fd=current_fd, directory=False)
    finally:
        os.close(current_fd)


def _read_fd_bounded(fd: int, max_bytes: int) -> bytes:
    try:
        declared_size = os.fstat(fd).st_size
    except OSError as exc:
        raise PathSandboxError("input file cannot be inspected") from exc
    if declared_size > max_bytes:
        raise InputTooLargeError(f"input is {declared_size} bytes; limit is {max_bytes}")
    chunks: list[bytes] = []
    remaining = max_bytes + 1
    try:
        while remaining:
            chunk = os.read(fd, min(1024 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
    except OSError as exc:
        raise PathSandboxError("input file cannot be read") from exc
    data = b"".join(chunks)
    if len(data) > max_bytes:
        raise InputTooLargeError(f"input grew beyond the configured {max_bytes}-byte limit")
    return data


def _read_bounded_input(
    input_value: str | os.PathLike[str],
    input_root: str | os.PathLike[str],
    max_bytes: int,
) -> tuple[bytes, str]:
    root, logical_name, expected_root = _logical_input_path(input_value, input_root)
    fd = _open_input_fd(root, logical_name, expected_root)
    try:
        return _read_fd_bounded(fd, max_bytes), logical_name
    finally:
        os.close(fd)


def _normalize_artifacts(artifacts: Iterable[str] | None) -> list[str]:
    values = list(artifacts or ("json", "md"))
    if not values:
        values = ["json"]
    if len(values) > len(ALLOWED_ARTIFACTS):
        raise InvalidRequestError("too many artifact entries")
    unknown = sorted(set(values) - ALLOWED_ARTIFACTS)
    if unknown:
        raise InvalidRequestError(f"unsupported artifact(s): {', '.join(unknown)}")
    # The derived JSON is the canonical source for every other artifact.
    return sorted(set(values) | {"json"})


def _optional_dependency_versions() -> dict[str, str | None]:
    try:
        return {"jsonschema": importlib.metadata.version("jsonschema")}
    except importlib.metadata.PackageNotFoundError:
        return {"jsonschema": None}


def _toolchain() -> dict[str, Any]:
    return {
        "runner_version": RUNNER_VERSION,
        "python_version": platform.python_version(),
        "dependencies": _optional_dependency_versions(),
        "hashes": dict(_PROCESS_TOOL_HASHES),
    }


def _current_tool_hashes() -> dict[str, str]:
    return {name: sha256_file(path) for name, path in sorted(TOOL_PATHS.items())}


def process_toolchain_fingerprint() -> str:
    """Return the immutable fingerprint captured by this Python process."""
    return sha256_bytes(_canonical_json(_toolchain()))


def assert_toolchain_unchanged(expected_fingerprint: str | None = None) -> str:
    """Latch process failure when loaded tool files no longer match disk."""
    global _TOOLCHAIN_CHANGED
    process_fingerprint = process_toolchain_fingerprint()
    if (
        expected_fingerprint is not None
        and expected_fingerprint != process_fingerprint
    ):
        raise ToolchainChangedError(
            "API process toolchain identity does not match its startup fingerprint; restart required"
        )
    with _TOOLCHAIN_STATE_LOCK:
        if _TOOLCHAIN_CHANGED:
            raise ToolchainChangedError(
                "toolchain changed after process start; restart required"
            )
        try:
            current_hashes = _current_tool_hashes()
        except (OSError, PipelineError) as exc:
            _TOOLCHAIN_CHANGED = True
            raise ToolchainChangedError(
                "toolchain cannot be verified after process start; restart required"
            ) from exc
        if current_hashes != _PROCESS_TOOL_HASHES:
            _TOOLCHAIN_CHANGED = True
            raise ToolchainChangedError(
                "toolchain changed after process start; restart required"
            )
    return process_fingerprint


def _request_document(
    *,
    operation: str,
    input_relative: str,
    input_hash: str,
    input_size: int,
    artifacts: list[str],
    max_input_bytes: int,
    max_run_bytes: int,
    max_output_root_bytes: int,
    toolchain: dict[str, Any],
) -> dict[str, Any]:
    return {
        "operation": operation,
        "input": {
            "relative_path": input_relative,
            "sha256": input_hash,
            "size": input_size,
        },
        "parameters": {
            "artifacts": artifacts,
            "max_input_bytes": max_input_bytes,
            "max_run_bytes": max_run_bytes,
            "max_output_root_bytes": max_output_root_bytes,
            "network": False,
            "telemetry": False,
        },
        "toolchain": toolchain,
    }


def _output_record(run_dir: Path, relative: str, kind: str) -> dict[str, Any]:
    if not isinstance(relative, str) or not isinstance(kind, str) or not kind:
        raise PipelineError("generated output record is malformed")
    logical = PurePosixPath(relative)
    if (
        logical.is_absolute()
        or not logical.parts
        or any(part in {"", ".", ".."} for part in logical.parts)
        or logical.as_posix() != relative
    ):
        raise PipelineError("generated output escaped its run directory")
    anchor_fd = _open_absolute_directory(run_dir)
    os.close(anchor_fd)
    path = run_dir.joinpath(*logical.parts)
    try:
        opened = os.lstat(path)
    except OSError as exc:
        raise PipelineError("generated output is missing or inaccessible") from exc
    if stat.S_ISLNK(opened.st_mode):
        raise PipelineError("generated outputs may not be symlinks")
    if stat.S_ISDIR(opened.st_mode):
        digest, size, file_count = _tree_digest(path)
        return {
            "kind": kind,
            "path": relative,
            "type": "directory",
            "sha256": digest,
            "size": size,
            "file_count": file_count,
        }
    if not stat.S_ISREG(opened.st_mode):
        raise PipelineError("generated output must be a regular file or directory")
    return {
        "kind": kind,
        "path": relative,
        "type": "file",
        "sha256": sha256_file(path),
        "size": path.stat().st_size,
    }


def _records_for(run_dir: Path, specs: Iterable[tuple[str, str]]) -> list[dict[str, Any]]:
    return [_output_record(run_dir, relative, kind) for relative, kind in specs]


def _record_is_complete(record: Any) -> bool:
    if not isinstance(record, dict):
        return False
    record_type = record.get("type")
    expected_keys = {"kind", "path", "type", "sha256", "size"}
    if record_type == "directory":
        expected_keys.add("file_count")
    elif record_type != "file":
        return False
    if set(record) != expected_keys:
        return False
    path = record.get("path")
    kind = record.get("kind")
    digest = record.get("sha256")
    size = record.get("size")
    logical = PurePosixPath(path) if isinstance(path, str) else None
    if (
        logical is None
        or logical.is_absolute()
        or not logical.parts
        or any(part in {"", ".", ".."} for part in logical.parts)
        or logical.as_posix() != path
        or not isinstance(kind, str)
        or not kind
        or not isinstance(digest, str)
        or SHA256_HEX.fullmatch(digest) is None
        or not isinstance(size, int)
        or isinstance(size, bool)
        or size < 0
    ):
        return False
    if record_type == "directory":
        count = record.get("file_count")
        if not isinstance(count, int) or isinstance(count, bool) or count < 0:
            return False
    return True


def _records_valid(
    run_dir: Path,
    records: Iterable[dict[str, Any]],
    expected_specs: Iterable[tuple[str, str]] | None = None,
) -> bool:
    try:
        if not isinstance(records, list) or not records:
            return False
        if not all(_record_is_complete(record) for record in records):
            return False
        expected_list = list(expected_specs) if expected_specs is not None else None
        if expected_list is not None:
            identities = [(record["path"], record["kind"]) for record in records]
            if identities != expected_list:
                return False
        for expected in records:
            current = _output_record(run_dir, expected["path"], expected["kind"])
            if current != expected:
                return False
        return True
    except (OSError, KeyError, TypeError, ValueError, PipelineError):
        return False


def _expected_stage_plan(
    request: dict[str, Any],
) -> list[tuple[int, str, list[tuple[str, str]]]]:
    """Return the exact receipt/output contract for one normalized request."""
    operation = request.get("operation")
    artifacts = (request.get("parameters") or {}).get("artifacts")
    if operation not in {"validate", "build"} or not isinstance(artifacts, list):
        return []
    plan: list[tuple[int, str, list[tuple[str, str]]]] = [
        (1, "load", [("source.knowledge.json", "input_snapshot")])
    ]
    if operation == "build":
        plan.append(
            (2, "derive", [("artifacts/graph.knowledge.json", "knowledge_json")])
        )
        validation_index = 3
    else:
        validation_index = 2
    plan.append(
        (validation_index, "validate", [("validation.json", "validation_report")])
    )
    if operation == "build" and any(item != "json" for item in artifacts):
        artifact_specs = [
            (f"artifacts/{filename}", kind)
            for artifact, filename, kind in TEXT_ARTIFACTS
            if artifact in artifacts
        ]
        if "bundle" in artifacts:
            artifact_specs.append(("artifacts/bundle", "knowledge_bundle"))
        plan.append((4, "artifacts", artifact_specs))
    return plan


def _expected_manifest_specs(request: dict[str, Any]) -> list[tuple[str, str]]:
    return [spec for _, _, specs in _expected_stage_plan(request) for spec in specs]


@contextmanager
def _request_lock(output_root: Path, request_key: str):
    """Serialize requests through a bounded lock-file stripe set."""
    lock_dir = output_root / ".locks"
    lock_dir_fd = _open_absolute_directory(lock_dir, create=True)
    if request_key == "__quota_global__":
        lock_name = "quota-global.lock"
    else:
        stripe = sha256_bytes(request_key.encode("utf-8"))[:2]
        lock_name = f"request-{stripe}.lock"
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        lock_fd = os.open(lock_name, flags, 0o600, dir_fd=lock_dir_fd)
    except OSError as exc:
        os.close(lock_dir_fd)
        raise PathSandboxError("request lock cannot be opened safely") from exc
    opened = os.fstat(lock_fd)
    if not stat.S_ISREG(opened.st_mode):
        os.close(lock_fd)
        os.close(lock_dir_fd)
        raise PathSandboxError("request lock must be a regular file")
    handle = os.fdopen(lock_fd, "a+b")
    # The import is resolved BEFORE the yield.  With it inside the same ``try``, an
    # ImportError raised anywhere in the CALLER's with-block propagated into the
    # generator at the yield, hit the ImportError handler and yielded a second time --
    # so contextlib raised "generator didn't stop after throw" and masked the caller's
    # real error.  Optional imports elsewhere in the toolchain make that reachable.
    try:
        import fcntl  # type: ignore
    except ImportError:  # pragma: no cover - exercised only on non-POSIX systems
        fcntl = None  # type: ignore[assignment]
    try:
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                # An exception from the caller used to skip the unlock and leave it to
                # handle.close(); releasing it here makes the pairing explicit.
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        else:
            fallback_key = f"{output_root}:{lock_name}"
            with _FALLBACK_LOCKS_GUARD:
                lock = _FALLBACK_LOCKS.setdefault(fallback_key, threading.Lock())
            with lock:
                yield
    finally:
        handle.close()
        os.close(lock_dir_fd)


def _attempt_number(path: Path, request_key: str) -> int | None:
    prefix = f"{request_key}-a"
    if not path.name.startswith(prefix):
        return None
    suffix = path.name[len(prefix) :]
    return int(suffix) if suffix.isdigit() else None


def _reject_unsafe_attempt_tree(path: Path) -> None:
    """Reject links and special files anywhere in generated attempt state."""
    anchor_fd = _open_absolute_directory(path)
    os.close(anchor_fd)
    for directory, names, files in os.walk(path, followlinks=False):
        for name in [*names, *files]:
            candidate = Path(directory) / name
            try:
                opened = os.lstat(candidate)
            except OSError as exc:
                raise PathSandboxError("run attempt changed during safety inspection") from exc
            if stat.S_ISLNK(opened.st_mode):
                raise PathSandboxError("run attempts may not contain symlinks")
            if not (stat.S_ISDIR(opened.st_mode) or stat.S_ISREG(opened.st_mode)):
                raise PathSandboxError("run attempts may contain only directories and regular files")


def _receipt_is_valid(
    run_dir: Path,
    receipt: Any,
    *,
    expected_name: str,
    expected_specs: list[tuple[str, str]],
) -> bool:
    required = {
        "name",
        "status",
        "started_at",
        "finished_at",
        "duration_ms",
        "details",
        "outputs",
        "failure",
    }
    if not isinstance(receipt, dict) or set(receipt) != required:
        return False
    duration = receipt.get("duration_ms")
    if (
        receipt.get("name") != expected_name
        or receipt.get("status") != "succeeded"
        or receipt.get("failure") is not None
        or not isinstance(receipt.get("started_at"), str)
        or not isinstance(receipt.get("finished_at"), str)
        or not isinstance(duration, (int, float))
        or isinstance(duration, bool)
        or duration < 0
        or not isinstance(receipt.get("details"), dict)
    ):
        return False
    return _records_valid(run_dir, receipt.get("outputs"), expected_specs)


def _manifest_is_reusable(
    run_dir: Path,
    manifest: Any,
    *,
    request_key: str,
    request: dict[str, Any],
) -> bool:
    required = {
        "manifest_version",
        "run_id",
        "request_key",
        "status",
        "created_at",
        "finished_at",
        "operation",
        "input",
        "parameters",
        "toolchain",
        "distiller",
        "stages",
        "outputs",
        "validation",
        "failure",
        "network_access",
        "telemetry",
    }
    if not isinstance(manifest, dict) or set(manifest) != required:
        return False
    if (
        manifest.get("manifest_version") != MANIFEST_VERSION
        or manifest.get("run_id") != run_dir.name
        or manifest.get("request_key") != request_key
        or manifest.get("status") != "succeeded"
        or manifest.get("operation") != request.get("operation")
        or manifest.get("input") != request.get("input")
        or manifest.get("parameters") != request.get("parameters")
        or manifest.get("toolchain") != request.get("toolchain")
        or manifest.get("failure") is not None
        or manifest.get("network_access") is not False
        or manifest.get("telemetry") is not False
        or not isinstance(manifest.get("created_at"), str)
        or not isinstance(manifest.get("finished_at"), str)
        or not isinstance(manifest.get("validation"), dict)
        or manifest["validation"].get("ok") is not True
    ):
        return False

    try:
        envelope = _load_json(run_dir / "request.json")
        validation = _load_json(run_dir / "validation.json")
        target_relative = (
            "artifacts/graph.knowledge.json"
            if request.get("operation") == "build"
            else "source.knowledge.json"
        )
        target = _load_json(run_dir / target_relative)
    except Exception:
        return False
    if (
        not isinstance(envelope, dict)
        or set(envelope) != {"created_at", "request"}
        or envelope.get("request") != request
        or envelope.get("created_at") != manifest.get("created_at")
        or manifest.get("validation") != validation
        or not isinstance(target, dict)
    ):
        return False
    metadata = target.get("metadata") or {}
    expected_distiller = {
        "version": metadata.get("distiller_version")
        if isinstance(metadata, dict)
        else None,
        "spec_version": metadata.get("distiller_spec_version")
        if isinstance(metadata, dict)
        else None,
    }
    if manifest.get("distiller") != expected_distiller:
        return False

    plan = _expected_stage_plan(request)
    stages = manifest.get("stages")
    if not plan or not isinstance(stages, list) or len(stages) != len(plan):
        return False
    for stage, (index, name, specs) in zip(stages, plan):
        if not _receipt_is_valid(
            run_dir, stage, expected_name=name, expected_specs=specs
        ):
            return False
        receipt_path = run_dir / "stages" / f"{index:02d}-{name}.json"
        try:
            if _load_json(receipt_path) != stage:
                return False
        except Exception:
            return False
    return _records_valid(
        run_dir, manifest.get("outputs"), _expected_manifest_specs(request)
    )


def _resume_receipts_are_valid(run_dir: Path, request: dict[str, Any]) -> bool:
    plan = _expected_stage_plan(request)
    if not plan:
        return False
    stages_dir = run_dir / "stages"
    if not stages_dir.exists():
        return True
    try:
        stages_fd = _open_absolute_directory(stages_dir)
    except (FileNotFoundError, PathSandboxError):
        return False
    try:
        with os.scandir(stages_fd) as entries:
            names = sorted(entry.name for entry in entries)
    finally:
        os.close(stages_fd)
    if len(names) > len(plan):
        return False
    expected_names = [f"{index:02d}-{name}.json" for index, name, _ in plan]
    if names != expected_names[: len(names)]:
        return False
    for filename, (_, name, specs) in zip(names, plan):
        try:
            receipt = _load_json(stages_dir / filename)
        except Exception:
            return False
        if not _receipt_is_valid(
            run_dir, receipt, expected_name=name, expected_specs=specs
        ):
            return False
    return True


def _find_or_create_attempt(
    runs_root: Path,
    request_key: str,
    request: dict[str, Any],
    *,
    resume: bool,
    max_run_bytes: int,
    max_output_root_bytes: int,
) -> tuple[Path, bool, dict[str, Any] | None]:
    output_root = runs_root.parent
    candidate_policy = QuotaPolicy(
        output_root=output_root,
        run_dir=runs_root / f"{request_key}-pending",
        max_run_bytes=max_run_bytes,
        max_output_root_bytes=max_output_root_bytes,
    )
    # Reject an already-over-budget root before scanning or creating an attempt.
    with _quota_reservation(candidate_policy, 0):
        pass
    attempts: list[tuple[int, Path]] = []
    runs_fd = _open_absolute_directory(runs_root)
    try:
        with os.scandir(runs_fd) as scanned:
            entries = list(scanned)
        for entry in entries:
            path = runs_root / entry.name
            number = _attempt_number(path, request_key)
            if number is None:
                continue
            if entry.is_symlink() or not entry.is_dir(follow_symlinks=False):
                raise PathSandboxError("matching run attempt is not a safe directory")
            attempts.append((number, path))
    finally:
        os.close(runs_fd)
    attempts.sort()

    # Reuse only a verified successful immutable run.
    for _, path in reversed(attempts):
        existing_policy = QuotaPolicy(
            output_root=output_root,
            run_dir=path,
            max_run_bytes=max_run_bytes,
            max_output_root_bytes=max_output_root_bytes,
        )
        with _quota_reservation(existing_policy, 0):
            pass
        manifest_path = path / "manifest.json"
        if not manifest_path.exists():
            continue
        try:
            _reject_unsafe_attempt_tree(path)
            manifest = _load_json(manifest_path)
        except Exception:
            continue
        if _manifest_is_reusable(
            path, manifest, request_key=request_key, request=request
        ):
            return path, False, manifest

    # A hard-interrupted attempt has no final manifest.  Resume it only if its
    # immutable request matches and every existing receipt/output still verifies.
    if resume:
        for _, path in reversed(attempts):
            existing_policy = QuotaPolicy(
                output_root=output_root,
                run_dir=path,
                max_run_bytes=max_run_bytes,
                max_output_root_bytes=max_output_root_bytes,
            )
            with _quota_reservation(existing_policy, 0):
                pass
            if (path / "manifest.json").exists() or not (path / "request.json").is_file():
                continue
            try:
                _reject_unsafe_attempt_tree(path)
                envelope = _load_json(path / "request.json")
            except Exception:
                continue
            if (
                not isinstance(envelope, dict)
                or set(envelope) != {"created_at", "request"}
                or not isinstance(envelope.get("created_at"), str)
                or envelope.get("request") != request
            ):
                continue
            if not _resume_receipts_are_valid(path, request):
                continue
            return path, True, None

    next_number = (attempts[-1][0] + 1) if attempts else 1
    path = runs_root / f"{request_key}-a{next_number:04d}"
    envelope_bytes = _pretty_json({"created_at": _utc_now(), "request": request})
    policy = QuotaPolicy(
        output_root=output_root,
        run_dir=path,
        max_run_bytes=max_run_bytes,
        max_output_root_bytes=max_output_root_bytes,
    )
    # The request envelope is reserved before the attempt directory exists.  A
    # rejected budget therefore cannot leave an empty, attacker-amplifiable run.
    with _quota_reservation(policy, len(envelope_bytes)):
        runs_fd = _open_absolute_directory(runs_root)
        try:
            os.mkdir(path.name, 0o700, dir_fd=runs_fd)
            attempt_fd = _open_component(
                path.name, directory_fd=runs_fd, directory=True
            )
            os.close(attempt_fd)
        except FileExistsError as exc:
            raise PathSandboxError("run attempt path appeared concurrently") from exc
        finally:
            os.close(runs_fd)
        _write_once(
            path / "request.json",
            envelope_bytes,
            quota=None,
        )
    return path, False, None


def _validate_document(doc: dict[str, Any]) -> dict[str, Any]:
    schema_report = validate_knowledge.Report()
    schema_checked = validate_knowledge.schema_validate(doc, schema_report)
    report = validate_knowledge.validate(doc, ran_schema=schema_checked)
    if schema_checked:
        report.errors = schema_report.errors + report.errors
        report.warnings = schema_report.warnings + report.warnings
    return {
        "ok": report.ok,
        "errors": report.errors,
        "warnings": report.warnings,
        "conformance_score": report.conformance_score(),
        "quality_score": report.quality_score(),  # compatibility alias
        "semantic_accuracy_evaluated": False,
        "schema_checked": schema_checked,
    }


def _read_receipt_if_valid(
    run_dir: Path,
    receipt_path: Path,
    *,
    expected_name: str,
    expected_specs: list[tuple[str, str]],
) -> dict[str, Any] | None:
    if not receipt_path.is_file():
        return None
    try:
        receipt = _load_json(receipt_path)
    except Exception:
        return None
    if _receipt_is_valid(
        run_dir,
        receipt,
        expected_name=expected_name,
        expected_specs=expected_specs,
    ):
        return receipt
    return None


def _run_stage(
    run_dir: Path,
    index: int,
    name: str,
    function: Callable[[], tuple[Iterable[tuple[str, str]], dict[str, Any]]],
    *,
    allow_resume: bool,
    expected_specs: Iterable[tuple[str, str]],
    quota: QuotaPolicy,
) -> tuple[dict[str, Any], bool]:
    receipt_path = run_dir / "stages" / f"{index:02d}-{name}.json"
    expected = list(expected_specs)
    if allow_resume:
        existing = _read_receipt_if_valid(
            run_dir,
            receipt_path,
            expected_name=name,
            expected_specs=expected,
        )
        if existing is not None:
            return existing, True

    started_at = _utc_now()
    started = time.perf_counter()
    try:
        specs, details = function()
        actual_specs = list(specs)
        if actual_specs != expected:
            raise PipelineError(
                f"stage {name} produced outputs outside its fixed receipt contract"
            )
        outputs = _records_for(run_dir, actual_specs)
        receipt = {
            "name": name,
            "status": "succeeded",
            "started_at": started_at,
            "finished_at": _utc_now(),
            "duration_ms": round((time.perf_counter() - started) * 1000, 3),
            "details": details,
            "outputs": outputs,
            "failure": None,
        }
    except Exception as exc:
        specs = exc.output_specs if isinstance(exc, StageFailure) else []
        outputs: list[dict[str, Any]] = []
        with contextlib.suppress(Exception):
            outputs = _records_for(run_dir, specs)
        receipt = {
            "name": name,
            "status": "failed",
            "started_at": started_at,
            "finished_at": _utc_now(),
            "duration_ms": round((time.perf_counter() - started) * 1000, 3),
            "details": exc.details if isinstance(exc, StageFailure) else {},
            "outputs": outputs,
            "failure": {
                "code": exc.code if isinstance(exc, StageFailure) else "stage_exception",
                "type": type(exc).__name__,
                "message": str(exc)[:1000],
            },
        }
        _write_once(receipt_path, _pretty_json(receipt), quota=quota)
        raise

    _write_once(receipt_path, _pretty_json(receipt), quota=quota)
    return receipt, False


def _collect_outputs(stages: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    outputs: list[dict[str, Any]] = []
    for stage in stages:
        for output in stage.get("outputs", []):
            path = output.get("path")
            if path and path not in seen:
                outputs.append(output)
                seen.add(path)
    return outputs


def _public_result(run_dir: Path, manifest: dict[str, Any], *, reused: bool, resumed: bool) -> dict[str, Any]:
    return {
        "run_id": manifest["run_id"],
        "status": manifest["status"],
        "reused": reused,
        "resumed": resumed,
        "run_dir": str(run_dir),
        "manifest_path": str(run_dir / "manifest.json"),
        "manifest": manifest,
    }


def execute_pipeline(
    input_path: str | os.PathLike[str],
    *,
    input_root: str | os.PathLike[str],
    output_root: str | os.PathLike[str],
    operation: str = "build",
    artifacts: Iterable[str] | None = None,
    max_input_bytes: int = DEFAULT_MAX_INPUT_BYTES,
    max_run_bytes: int = DEFAULT_MAX_RUN_BYTES,
    max_output_root_bytes: int = DEFAULT_MAX_OUTPUT_ROOT_BYTES,
    resume: bool = True,
    expected_toolchain_fingerprint: str | None = None,
) -> dict[str, Any]:
    """Execute one content-addressed local run and return its immutable manifest.

    ``operation`` is ``validate`` or ``build``.  ``build`` always emits the
    canonical JSON and may additionally emit the closed set of documented
    Markdown, bundle, HTML, Cypher, CTXT, and Canvas projections.
    """
    if operation == "run":
        operation = "build"
    if operation not in {"validate", "build"}:
        raise InvalidRequestError("operation must be validate or build")
    limits = {
        "max_input_bytes": max_input_bytes,
        "max_run_bytes": max_run_bytes,
        "max_output_root_bytes": max_output_root_bytes,
    }
    for name, limit in limits.items():
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
            raise InvalidRequestError(f"{name} must be a positive integer")

    toolchain_fingerprint = assert_toolchain_unchanged(
        expected_toolchain_fingerprint
    )

    normalized_artifacts = [] if operation == "validate" else _normalize_artifacts(artifacts)
    input_bytes, input_relative = _read_bounded_input(
        input_path, input_root, max_input_bytes
    )
    input_size = len(input_bytes)
    input_hash = sha256_bytes(input_bytes)
    toolchain = _toolchain()
    request = _request_document(
        operation=operation,
        input_relative=input_relative,
        input_hash=input_hash,
        input_size=input_size,
        artifacts=normalized_artifacts,
        max_input_bytes=max_input_bytes,
        max_run_bytes=max_run_bytes,
        max_output_root_bytes=max_output_root_bytes,
        toolchain=toolchain,
    )
    request_key = sha256_bytes(_canonical_json(request))[:32]

    out_root = _prepare_output_root(output_root)
    runs_root = out_root / "runs"
    runs_fd = _open_absolute_directory(runs_root, create=True)
    os.close(runs_fd)

    with _request_lock(out_root, request_key):
        run_dir, resumed, reusable = _find_or_create_attempt(
            runs_root,
            request_key,
            request,
            resume=resume,
            max_run_bytes=max_run_bytes,
            max_output_root_bytes=max_output_root_bytes,
        )
        quota = QuotaPolicy(
            output_root=out_root,
            run_dir=run_dir,
            max_run_bytes=max_run_bytes,
            max_output_root_bytes=max_output_root_bytes,
        )
        if reusable is not None:
            assert_toolchain_unchanged(toolchain_fingerprint)
            return _public_result(run_dir, reusable, reused=True, resumed=False)

        envelope = _load_json(run_dir / "request.json")
        created_at = envelope["created_at"]
        run_id = run_dir.name
        stages: list[dict[str, Any]] = []
        validation_result: dict[str, Any] | None = None
        current_stage = "load"

        try:
            def load_stage() -> tuple[list[tuple[str, str]], dict[str, Any]]:
                try:
                    parsed = strict_json.loads(input_bytes, source=input_relative)
                except strict_json.StrictJsonError as exc:
                    raise StageFailure(f"invalid JSON: {exc}", code="invalid_json") from exc
                if not isinstance(parsed, dict):
                    raise StageFailure("graph document must be a JSON object", code="invalid_graph")
                _atomic_write(
                    run_dir / "source.knowledge.json", input_bytes, quota=quota
                )
                return [("source.knowledge.json", "input_snapshot")], {
                    "input_sha256": input_hash,
                    "input_size": input_size,
                }

            load_specs = [("source.knowledge.json", "input_snapshot")]
            receipt, _ = _run_stage(
                run_dir,
                1,
                "load",
                load_stage,
                allow_resume=resumed,
                expected_specs=load_specs,
                quota=quota,
            )
            stages.append(receipt)

            target_relative = "source.knowledge.json"
            if operation == "build":
                current_stage = "derive"

                def derive_stage() -> tuple[list[tuple[str, str]], dict[str, Any]]:
                    source_doc = _load_json(run_dir / "source.knowledge.json")
                    derived = build_graph.recompute(copy.deepcopy(source_doc))
                    relative = "artifacts/graph.knowledge.json"
                    _atomic_write(
                        run_dir / relative, _pretty_json(derived), quota=quota
                    )
                    return [(relative, "knowledge_json")], {
                        "concept_count": len(derived.get("nodes", []) or []),
                        "relationship_count": len(derived.get("edges", []) or []),
                        "fact_count": len(derived.get("facts", []) or []),
                    }

                derive_specs = [
                    ("artifacts/graph.knowledge.json", "knowledge_json")
                ]
                receipt, _ = _run_stage(
                    run_dir,
                    2,
                    "derive",
                    derive_stage,
                    allow_resume=resumed,
                    expected_specs=derive_specs,
                    quota=quota,
                )
                stages.append(receipt)
                target_relative = "artifacts/graph.knowledge.json"

            current_stage = "validate"

            def validate_stage() -> tuple[list[tuple[str, str]], dict[str, Any]]:
                doc = _load_json(run_dir / target_relative)
                result = _validate_document(doc)
                _atomic_write(
                    run_dir / "validation.json", _pretty_json(result), quota=quota
                )
                specs = [("validation.json", "validation_report")]
                if not result["ok"]:
                    raise StageFailure(
                        f"validation failed with {len(result['errors'])} error(s)",
                        output_specs=specs,
                        details={
                            "ok": False,
                            "error_count": len(result["errors"]),
                            "warning_count": len(result["warnings"]),
                            "conformance_score": result["conformance_score"],
                            "quality_score": result["quality_score"],
                            "semantic_accuracy_evaluated": False,
                            "schema_checked": result["schema_checked"],
                        },
                        code="validation_failed",
                    )
                return specs, {
                    "ok": True,
                    "error_count": 0,
                    "warning_count": len(result["warnings"]),
                    "conformance_score": result["conformance_score"],
                    "quality_score": result["quality_score"],
                    "semantic_accuracy_evaluated": False,
                    "schema_checked": result["schema_checked"],
                }

            validation_index = 3 if operation == "build" else 2
            receipt, _ = _run_stage(
                run_dir,
                validation_index,
                "validate",
                validate_stage,
                allow_resume=resumed,
                expected_specs=[("validation.json", "validation_report")],
                quota=quota,
            )
            stages.append(receipt)
            validation_result = _load_json(run_dir / "validation.json")

            if operation == "build" and any(a != "json" for a in normalized_artifacts):
                current_stage = "artifacts"

                def artifact_stage() -> tuple[list[tuple[str, str]], dict[str, Any]]:
                    doc = _load_json(run_dir / "artifacts/graph.knowledge.json")
                    specs: list[tuple[str, str]] = []
                    renderers: dict[str, Callable[[dict[str, Any]], str]] = {
                        "md": build_md.render,
                        "html": build_viewer.render,
                        "cypher": build_exports.render_cypher,
                        "ctxt": build_exports.render_ctxt,
                        "canvas": build_exports.render_canvas,
                    }
                    for artifact, filename, kind in TEXT_ARTIFACTS:
                        if artifact not in normalized_artifacts:
                            continue
                        relative = f"artifacts/{filename}"
                        rendered = renderers[artifact](copy.deepcopy(doc))
                        _atomic_write(
                            run_dir / relative,
                            rendered.encode("utf-8"),
                            quota=quota,
                        )
                        specs.append((relative, kind))
                    if "bundle" in normalized_artifacts:
                        relative = "artifacts/bundle"
                        bundle_dir = run_dir / relative
                        with tempfile.TemporaryDirectory(
                            prefix="knowledge-distiller-bundle-"
                        ) as temporary:
                            rendered_bundle = Path(temporary).resolve(strict=True) / "bundle"
                            with contextlib.redirect_stdout(io.StringIO()):
                                build_bundle.build(
                                    copy.deepcopy(doc), rendered_bundle
                                )
                            _install_directory_tree(
                                rendered_bundle, bundle_dir, quota=quota
                            )
                        specs.append((relative, "knowledge_bundle"))
                    return specs, {"artifacts": normalized_artifacts}

                artifact_specs = _expected_stage_plan(request)[-1][2]
                receipt, _ = _run_stage(
                    run_dir,
                    4,
                    "artifacts",
                    artifact_stage,
                    allow_resume=resumed,
                    expected_specs=artifact_specs,
                    quota=quota,
                )
                stages.append(receipt)

            manifest_outputs = _collect_outputs(stages)
            if not _records_valid(
                run_dir,
                manifest_outputs,
                _expected_manifest_specs(request),
            ):
                raise PipelineError(
                    "final output set does not match the fixed request contract"
                )
            assert_toolchain_unchanged(toolchain_fingerprint)
            manifest = {
                "manifest_version": MANIFEST_VERSION,
                "run_id": run_id,
                "request_key": request_key,
                "status": "succeeded",
                "created_at": created_at,
                "finished_at": _utc_now(),
                "operation": operation,
                "input": request["input"],
                "parameters": request["parameters"],
                "toolchain": toolchain,
                "distiller": {
                    "version": (_load_json(run_dir / target_relative).get("metadata", {}) or {}).get("distiller_version"),
                    "spec_version": (_load_json(run_dir / target_relative).get("metadata", {}) or {}).get("distiller_spec_version"),
                },
                "stages": stages,
                "outputs": manifest_outputs,
                "validation": validation_result,
                "failure": None,
                "network_access": False,
                "telemetry": False,
            }
        except Exception as exc:
            # Include a failed receipt even when the exception occurred before the
            # caller could append it to ``stages``.
            receipt_paths = sorted((run_dir / "stages").glob("*.json"))
            stages = []
            for path in receipt_paths:
                with contextlib.suppress(Exception):
                    stages.append(_load_json(path))
            if (run_dir / "validation.json").is_file():
                with contextlib.suppress(Exception):
                    validation_result = _load_json(run_dir / "validation.json")
            manifest = {
                "manifest_version": MANIFEST_VERSION,
                "run_id": run_id,
                "request_key": request_key,
                "status": "failed",
                "created_at": created_at,
                "finished_at": _utc_now(),
                "operation": operation,
                "input": request["input"],
                "parameters": request["parameters"],
                "toolchain": toolchain,
                "distiller": None,
                "stages": stages,
                "outputs": _collect_outputs(stages),
                "validation": validation_result,
                "failure": {
                    "stage": current_stage,
                    "code": exc.code if isinstance(exc, StageFailure) else "pipeline_exception",
                    "type": type(exc).__name__,
                    "message": str(exc)[:1000],
                },
                "network_access": False,
                "telemetry": False,
            }

        _write_once(
            run_dir / "manifest.json", _pretty_json(manifest), quota=quota
        )
        return _public_result(run_dir, manifest, reused=False, resumed=resumed)


def _summary(result: dict[str, Any]) -> dict[str, Any]:
    manifest = result["manifest"]
    return {
        "run_id": result["run_id"],
        "status": result["status"],
        "reused": result["reused"],
        "resumed": result["resumed"],
        "manifest": result["manifest_path"],
        "validation": manifest.get("validation"),
        "outputs": manifest.get("outputs", []),
        "failure": manifest.get("failure"),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run deterministic local Knowledge Distiller stages")
    sub = parser.add_subparsers(dest="operation", required=True)
    for operation in ("validate", "build", "run"):
        command = sub.add_parser(operation)
        command.add_argument("input", help="Input path, absolute or relative to --input-root")
        command.add_argument("--input-root", required=True, help="Sandbox root for readable inputs")
        command.add_argument("--output-root", required=True, help="Explicit root for all run outputs")
        command.add_argument("--max-input-bytes", type=int, default=DEFAULT_MAX_INPUT_BYTES)
        command.add_argument("--max-run-bytes", type=int, default=DEFAULT_MAX_RUN_BYTES)
        command.add_argument(
            "--max-output-root-bytes",
            type=int,
            default=DEFAULT_MAX_OUTPUT_ROOT_BYTES,
        )
        command.add_argument("--no-resume", action="store_true", help="Start a new attempt instead of resuming an interrupted one")
        if operation in {"build", "run"}:
            command.add_argument(
                "--artifacts",
                nargs="+",
                choices=sorted(ALLOWED_ARTIFACTS),
                default=["json", "md"],
            )
    args = parser.parse_args(argv)
    try:
        result = execute_pipeline(
            args.input,
            input_root=args.input_root,
            output_root=args.output_root,
            operation=args.operation,
            artifacts=getattr(args, "artifacts", None),
            max_input_bytes=args.max_input_bytes,
            max_run_bytes=args.max_run_bytes,
            max_output_root_bytes=args.max_output_root_bytes,
            resume=not args.no_resume,
        )
    except (PipelineError, FileNotFoundError, OSError) as exc:
        print(json.dumps({"status": "rejected", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps(_summary(result), ensure_ascii=False, indent=2))
    return 0 if result["status"] == "succeeded" else 1


if __name__ == "__main__":
    raise SystemExit(main())
