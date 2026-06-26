#!/usr/bin/env python3
"""Build a self-contained, offline-capable HTML viewer for a Knowledge Distiller graph.

Reads a ``*.knowledge.json`` file and inlines it together with the viewer's CSS
and JS into ``viewer/template.html``, producing a single standalone HTML file
that opens directly from a ``file://`` URL. The only external dependencies are
the two CDN ``<script>`` tags in the template (Cytoscape.js + marked); when
opened offline the viewer degrades gracefully and still shows metadata/facts.

The template, CSS and JS are resolved relative to this script's location, so the
tool works regardless of the current working directory.

Usage:
    python3 scripts/build_viewer.py <input.knowledge.json> [-o <output.html>]

Stdlib only (json, argparse, pathlib, sys).
"""

import argparse
import json
import sys
from pathlib import Path

# Tokens inside viewer/template.html that get replaced by literal substitution.
TOKEN_CSS = "/*__VIZ_CSS__*/"
TOKEN_JS = "/*__VIZ_JS__*/"
TOKEN_DATA = "/*__GRAPH_DATA__*/"


def default_output(input_path: Path) -> Path:
    """Derive the default output path from the input path."""
    name = input_path.name
    if name.endswith(".knowledge.json"):
        return input_path.with_name(name[: -len(".knowledge.json")] + ".knowledge.html")
    return input_path.with_suffix(".html")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build a self-contained HTML viewer for a Knowledge Distiller graph."
    )
    parser.add_argument("input", help="Path to the <name>.knowledge.json file")
    parser.add_argument(
        "-o", "--output",
        help="Output HTML path (default: input with .knowledge.json -> .knowledge.html)",
    )
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.is_file():
        print(f"error: input file not found: {input_path}", file=sys.stderr)
        return 1

    # Load + validate the JSON parses.
    try:
        raw = input_path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(f"error: could not parse JSON in {input_path}: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"error: could not read {input_path}: {exc}", file=sys.stderr)
        return 1

    # Resolve viewer assets relative to this script (repo_root/viewer/...).
    viewer_dir = Path(__file__).resolve().parent.parent / "viewer"
    template_path = viewer_dir / "template.html"
    css_path = viewer_dir / "viz.css"
    js_path = viewer_dir / "viz.js"

    try:
        template = template_path.read_text(encoding="utf-8")
        css = css_path.read_text(encoding="utf-8")
        js = js_path.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"error: could not read viewer asset: {exc}", file=sys.stderr)
        return 1

    # Serialize the graph data; guard against breaking out of the <script> tag.
    data_json = json.dumps(data, ensure_ascii=False)
    data_json = data_json.replace("</", "<\\/")

    # Literal token substitution (order doesn't matter: tokens are disjoint).
    html = template.replace(TOKEN_CSS, css)
    html = html.replace(TOKEN_DATA, data_json)
    html = html.replace(TOKEN_JS, js)

    output_path = Path(args.output) if args.output else default_output(input_path)
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(html, encoding="utf-8")
    except OSError as exc:
        print(f"error: could not write {output_path}: {exc}", file=sys.stderr)
        return 1

    print(str(output_path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
