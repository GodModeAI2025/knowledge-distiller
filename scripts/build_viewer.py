#!/usr/bin/env python3
"""Build a self-contained, offline HTML viewer for a Knowledge Distiller graph.

Reads a ``*.knowledge.json`` file and inlines it together with the viewer's CSS
and JS into ``viewer/template.html``, producing a single standalone HTML file
that opens directly from a ``file://`` URL. The viewer has no runtime network
or third-party JavaScript dependency.

The template, CSS and JS are resolved relative to this script's location, so the
tool works regardless of the current working directory.

Usage:
    python3 scripts/build_viewer.py <input.knowledge.json> [-o <output.html>]

Stdlib only (json, argparse, pathlib, sys).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import strict_json  # noqa: E402

# Tokens inside viewer/template.html that get replaced by literal substitution.
TOKEN_CSS = "/*__VIZ_CSS__*/"
TOKEN_JS = "/*__VIZ_JS__*/"
TOKEN_DATA = "/*__GRAPH_DATA__*/"


class ViewerOutputError(ValueError):
    """The requested derived HTML target is not safe for this input."""


class ViewerInputError(ValueError):
    """The strict JSON value cannot be consumed as a knowledge graph."""


def default_output(input_path: Path) -> Path:
    """Derive the canonical output path without ever falling back to the input name."""
    name = input_path.name
    if not name.endswith(".knowledge.json"):
        raise ViewerOutputError(
            "input must end with .knowledge.json when --output is omitted"
        )
    return input_path.with_name(name.removesuffix(".knowledge.json") + ".knowledge.html")


def _output_path(input_path: Path, explicit_output: str | None) -> Path:
    output = Path(explicit_output) if explicit_output is not None else default_output(input_path)
    if output.suffix.lower() != ".html":
        raise ViewerOutputError("output must end with .html")
    if output.is_symlink():
        raise ViewerOutputError(f"output is a symlink; refusing to replace it: {output}")
    if output.exists() and not output.is_file():
        raise ViewerOutputError(f"output is not a regular file: {output}")
    try:
        same_target = output.resolve(strict=False) == input_path.resolve(strict=True)
        if output.exists():
            same_target = same_target or os.path.samefile(input_path, output)
    except OSError as exc:
        raise ViewerOutputError(f"could not validate output target: {exc}") from exc
    if same_target:
        raise ViewerOutputError("output must be different from the input file")
    return output


def _write_html(output: Path, content: str) -> None:
    """Atomically replace one validated regular HTML target."""
    parent = output.parent
    if not parent.exists() or not parent.is_dir():
        raise ViewerOutputError(f"output directory does not exist: {parent}")
    temp_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=parent,
            prefix=".kd-viewer-",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temp_name = handle.name
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_name, 0o644)
        os.replace(temp_name, output)
        temp_name = None
    finally:
        if temp_name is not None:
            try:
                Path(temp_name).unlink()
            except FileNotFoundError:
                pass


def render(data: dict) -> str:
    """Return the self-contained viewer HTML without writing files.

    All state is local to the call, so the runner/API can invoke this function
    concurrently without mutating ``sys.argv`` or process-global renderer state.
    """
    if not isinstance(data, dict):
        raise ViewerInputError("graph root must be a JSON object")
    viewer_dir = Path(__file__).resolve().parent.parent / "viewer"
    template = (viewer_dir / "template.html").read_text(encoding="utf-8")
    css = (viewer_dir / "viz.css").read_text(encoding="utf-8")
    js = (viewer_dir / "viz.js").read_text(encoding="utf-8")

    # Serialize the graph data for an inline script. Escaping every HTML-significant
    # character prevents script-tag breakout through values such as ``<!--`` or
    # ``</script>``. U+2028/U+2029 are escaped for older JavaScript parsers too.
    data_json = json.dumps(data, ensure_ascii=False, allow_nan=False)
    # ``ensure_ascii=False`` deliberately retains readable Unicode, but Python
    # strings can also contain isolated UTF-16 surrogates that UTF-8 and JSON
    # interoperability do not permit.  Reject those for programmatic callers;
    # strict file loading below already enforces the same rule for the CLI.
    data_json.encode("utf-8")
    data_json = (
        data_json.replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )

    # Literal token substitution (order doesn't matter: tokens are disjoint).
    html = template.replace(TOKEN_CSS, css)
    html = html.replace(TOKEN_DATA, data_json)
    return html.replace(TOKEN_JS, js)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build a self-contained HTML viewer for a Knowledge Distiller graph."
    )
    parser.add_argument("input", help="Path to the <name>.knowledge.json file")
    parser.add_argument(
        "-o", "--output",
        help="Output HTML path (default: input with .knowledge.json -> .knowledge.html)",
    )
    args = parser.parse_args(argv)

    input_path = Path(args.input)
    if not input_path.is_file():
        print(f"error: input file not found: {input_path}", file=sys.stderr)
        return 1

    try:
        output_path = _output_path(input_path, args.output)
    except ViewerOutputError as exc:
        print(f"error: refusing viewer build: {exc}", file=sys.stderr)
        return 2

    # Load only unambiguous, interoperable RFC 8259 JSON.
    try:
        data = strict_json.load_path(input_path)
    except strict_json.StrictJsonError as exc:
        print(f"error: could not parse strict JSON in {input_path}: {exc}", file=sys.stderr)
        return 1

    try:
        html = render(data)
    except ViewerInputError as exc:
        print(f"error: refusing viewer build: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"error: could not read viewer asset: {exc}", file=sys.stderr)
        return 1

    try:
        _write_html(output_path, html)
    except (OSError, ViewerOutputError) as exc:
        print(f"error: could not write {output_path}: {exc}", file=sys.stderr)
        return 1

    print(str(output_path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
