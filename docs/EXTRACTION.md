# Local Source Extraction

`scripts/extract_source.py` is a deterministic, standard-library-only adapter from bounded local
files to normalized text segments. It is preprocessing, not distillation: it does not call a
model, infer facts, create a knowledge graph, evaluate source authority, or repair malformed
input.

The adapter is intentionally passive:

- no URL, network, telemetry, subprocess, browser, office application, or plugin is used;
- HTML scripts/styles, CSV formulas, JSON values, Word field instructions, macros, and embedded
  files are never executed;
- the caller supplies an explicit readable `--input-root`; every path component is opened
  descriptor-relative with symlink following disabled, and bytes are read from that same verified
  regular-file descriptor; and
- unsupported or unsafe input fails explicitly instead of being decoded heuristically.

## Command line

The adapter writes UTF-8 JSON to stdout. This keeps output placement in the caller's control and
prevents an input document from selecting a write path.

```bash
python3 scripts/extract_source.py document.md \
  --input-root /absolute/read-root \
  --format json
```

For streaming consumers:

```bash
python3 scripts/extract_source.py tables/data.csv \
  --input-root /absolute/read-root \
  --format jsonl
```

The input may be an absolute path only when it is lexically inside the declared or canonical
`--input-root`. URL-like values, missing files, directories, traversal, and every symlink in the
selected path are rejected. The canonical root is opened first by walking every component from
`/`; child directories and the file are then opened relative to those descriptors with `O_NOFOLLOW`
where available (or an `lstat`/`fstat` identity check on the fallback). This includes
the input-root anchor itself rather than trusting an earlier `resolve()` result. Size checks and
reads use the final `fstat`-verified descriptor, so replacing the root, an intermediate component,
or the filename with a symlink cannot redirect extraction to a different file. The default raw and
DOCX-expanded size limit is 64 MiB and can be reduced with `--max-bytes`. The adapter also enforces
the default-profile ceilings of 10,000 segments, 16,000 characters per segment and 256 MiB per
serialized output. They may only be lowered through `--max-segments`, `--max-segment-chars` and
`--max-output-bytes`; values above the shipped hard ceilings fail. Oversized TXT/Markdown spans
are split at exact character positions. An oversized unit in another format fails because its
selector identifies that row, JSON value, HTML block or DOCX paragraph as one semantic unit.

The hardened path walk requires POSIX descriptor-relative filesystem primitives and fails closed
when they are unavailable. Native Windows is not currently a supported extraction platform; the
Windows-drive check only prevents a drive path from being misclassified as a URI.

| CLI limit | Shipped maximum | Scope |
|---|---:|---|
| `--max-bytes` | 67,108,864 | raw file and expanded DOCX package |
| `--max-segments` | 10,000 | normalized segments |
| `--max-segment-chars` | 16,000 | characters in one segment |
| `--max-output-bytes` | 268,435,456 | selected JSON or JSONL serialization |

Exit status is `0` for a complete output and `2` for rejected input. Failures go only to stderr
as `error [<code>]: <bounded message>`; partial JSON is not written.

## Output contract

JSON mode emits one document:

```json
{
  "adapter_version": "1.0",
  "source": {
    "id": "source-<24 hex>",
    "file": "tables/data.csv",
    "logical_filename": "tables/data.csv",
    "type": "csv",
    "mime_type": "text/csv",
    "content_sha256": "<64 hex of the original bytes>",
    "size_bytes": 1234
  },
  "segment_count": 2,
  "segments": [
    {
      "id": "seg-<24 hex>",
      "index": 0,
      "text": "name\tvalue",
      "text_sha256": "<64 hex of normalized segment text>",
      "locator": "data!A1:B1",
      "selector": {
        "type": "CsvSelector",
        "sheet": "data",
        "cell_range": "A1:B1"
      },
      "selectors": [
        {
          "type": "CsvSelector",
          "sheet": "data",
          "cell_range": "A1:B1"
        }
      ]
    }
  ]
}
```

`selector` is the primary selector for consumers expecting one selector. `selectors` retains all
available refinements, such as a text position plus its exact quote. `locator` is a compact human
label and is not a substitute for the structured selectors.

JSONL mode emits:

1. one `record_type: "source"` line containing the complete source metadata and segment count;
2. one `record_type: "segment"` line per segment, in the same order as JSON mode.

Every JSON key is sorted at serialization time. There are no timestamps, absolute paths, random
values, filesystem mtimes, or environment-dependent identifiers in the output.

### Stable identity

- `source.id` is derived from the SHA-256 of the original file bytes.
- `segment.id` is derived from the source hash, normalized text, and structured selectors.
- Renaming an otherwise identical source leaves its source and segment IDs unchanged; only the
  logical filename changes.
- Editing any source byte intentionally changes the source identity and all of its segment IDs, so
  IDs are not silently reused across revisions. Cross-revision matching must be an explicit later
  operation over content hashes/selectors rather than an extractor assumption.

## Format mapping

| Input | Deterministic unit and order | Selector contract |
|---|---|---|
| `.txt` | non-empty paragraphs in canonical character order | `TextPositionSelector` plus `TextQuoteSelector` with exact text and bounded prefix/suffix |
| `.md`, `.markdown` | same as text; Markdown syntax is retained literally | `TextPositionSelector` plus `TextQuoteSelector` |
| `.html`, `.htm` | visible semantic blocks (`title`, headings, paragraphs, list/definition items, blockquotes, preformatted text, table cells/captions) in source order | pragmatic `FragmentSelector` DOM path, optional source `#id`, plus `TextQuoteSelector` |
| `.csv` | each non-empty row in row order, fields joined by tab in normalized text | `CsvSelector`, logical sheet `data`, exact row cell range |
| `.tsv` | same as CSV with a fixed tab delimiter | `CsvSelector` |
| `.json` | scalar leaves and empty containers; object keys sorted lexically and array indexes retained | RFC 6901-style `JsonPointerSelector` with `~0`/`~1` escaping |
| `.docx` | WordprocessingML paragraphs: main document, footnotes, endnotes, then numbered headers and footers | pragmatic OOXML `FragmentSelector` (`word/document.xml#paragraph=N`) plus `TextQuoteSelector` |

TXT/Markdown text positions address the canonical decoded view after a UTF-8 BOM is removed and
CRLF/bare-CR line endings are normalized to LF. They are Unicode character offsets, not byte
offsets. Oversized text paragraphs are split deterministically at whitespace when possible, with
each position and quote still referring to the exact canonical characters.

HTML is parsed as source markup, not as a browser-rendered DOM. `<script>`, `<style>`,
`<noscript>`, `<template>`, `<svg>`, and `<canvas>` content is omitted. Event attributes and URL
attributes are never included or followed. An element ID is an additional selector; the generated
structural path remains present to disambiguate malformed HTML with duplicate IDs.

CSV uses a fixed delimiter selected by the suffix; it does not use dialect sniffing. A cell that
starts with `=`, `+`, `-`, or `@` remains literal text and is never interpreted as a formula.

JSON rejects duplicate object keys, `NaN`, `Infinity`, invalid syntax, lone Unicode surrogates, and
unsafe nesting instead of accepting implementation-specific values. Sorting object keys makes
segment order independent of source key order; array order remains semantic.

DOCX is read directly from the OOXML ZIP with `zipfile` and `xml.etree.ElementTree`. Nothing is
extracted to the filesystem. The adapter validates archive names, duplicate/encrypted entries,
entry count, total declared expanded size and required package members. CRC/readability, XML syntax
and forbidden DTD/entity declarations are checked for every XML part that the adapter actually
reads; ignored binary/media parts are neither interpreted nor claimed as content validation. The
DTD check reads bytes, so a part that is not UTF-8 — a UTF-16 or UTF-32 byte order mark, or a
declared encoding other than UTF-8 — is rejected before it is parsed rather than decoded into a
second code path where the same check would have to be repeated.
Packages containing `vbaProject.bin` or a macro-enabled content type are rejected. Word field
instructions (`w:instrText`), tracked-deletion text, relationships,
OLE embeddings, images, drawings, comments, and custom XML are ignored; visible `w:t` text, tabs,
and line breaks are retained. The DOCX fragment notation is intentionally pragmatic rather than a
claim of standardized XPath stability.

## Explicitly unsupported

The adapter rejects rather than guesses from:

- URLs and URI schemes, including `file:` and `data:`;
- PDF, images, audio/video, archives other than DOCX, PPTX/XLSX, legacy Office binaries, and
  macro-enabled Office packages;
- binary data disguised with a text suffix (including NUL-containing input);
- text encodings other than strict UTF-8/UTF-8 with BOM; and
- OCR, chart interpretation, layout reconstruction, JavaScript-rendered HTML, formulas, external
  relationships, or encrypted documents.

PDF and OCR support should be implemented as a separate, explicitly trusted local adapter with its
own renderer/OCR dependencies, page selectors, limits, and golden datasets. This adapter does not
silently downgrade those sources to unreliable byte strings.

## Using segments as evidence

The selectors deliberately use the same names as the Knowledge Distiller evidence model. A later
distillation stage can copy an appropriate segment selector into an evidence record and retain the
segment/source hashes for audit. Extraction alone does not prove a claim, assign confidence, or
decide which segment supports which graph node, edge, or fact.

Once a graph exists, the anchors can be checked mechanically against the same adapter output:

```bash
python3 scripts/verify_evidence.py graph.knowledge.json normalized.source.json [--require-all] [--json]
```

Evidence is paired with a normalized source only when the graph source carries the identical
`content_sha256`. `TextQuoteSelector.exact` and any `excerpt` must occur in the extracted text
(compared after Unicode NFC normalization, whitespace runs as one space), a `TextPositionSelector`
must lie inside one text segment and match its `excerpt`, and `FragmentSelector`, `CsvSelector` or
`JsonPointerSelector` must equal a selector the adapter emitted. A cell range inside an emitted row
range of the same sheet and a JSON pointer to a container of emitted leaves also resolve. `not_found` exits with 1. Evidence without a matching source or with
a selector type the adapter never produces is `unverifiable`; `--require-all` turns that into a
failure as well. The report states that semantic support was not evaluated: a resolved anchor proves
that the passage exists, not that it supports the claim.

Run its focused regression suite with:

```bash
python3 -B -m pytest -p no:cacheprovider tests/test_extract_source.py
```

The suite includes pathname- and root-swap regressions: after the trusted file descriptor is open,
the visible path is replaced by a symlink to outside the root, and before root anchoring the root is
replaced the same way. Extraction reads only the already-opened in-root file in the first case and
fails closed before reading in the second.
