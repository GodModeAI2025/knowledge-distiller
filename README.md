# Knowledge Distiller v4.0

> Extract knowledge, not text — and keep the evidence attached.

Knowledge Distiller is a local, file-based knowledge-graph toolchain and compiler contract. Its canonical
`.knowledge.json` graph separates concepts, claims, relationships, facts, source evidence,
time, spatial roles and derivations. Deterministic tools validate, merge and render that graph
without requiring a daemon or database and without outbound network access or telemetry. An
authenticated loopback API is an optional facade, not a runtime prerequisite.

The repository currently implements bounded source normalization and deterministic
post-compilation validation, merge, evaluation, rendering and run receipts. It does **not** contain
a Segment-to-Graph semantic compiler: an external agent/compiler must author the evidence-linked
graph first. The extractor and post-compilation runner are separate tools; there is no automatic
end-to-end handoff and the runner neither invokes a model nor applies a compiler profile.

Spec 1.1 is an additive trust layer: existing Spec 1.0 files remain valid.

## Why this version is different

| Weak shortcut | Implemented contract |
|---|---|
| Plausible statement with a source name | Reusable evidence with an exact selector/locator |
| One opaque “quality” number | `conformance_score` plus scoped golden evaluation; semantic truth is never implied |
| “The AI reasoned that …” | Explicit origin, evidence, short derivation summary and review status; no chain-of-thought storage |
| A place string with unclear meaning | Role-qualified spatial context such as jurisdiction, market or mentioned location |
| Conflicting value overwritten | Both facts retained and linked through an explicit conflict |
| Count-only merge guard | Identity- and payload-aware merge with exact prior archive plus mandatory JSON and Markdown diffs |
| CDN-backed viewer | One dependency-free offline HTML file with an interactive SVG graph |
| IDs interpolated into paths | Preflighted, collision-safe bundle paths and atomic writes |

The design deliberately does **not** copy a broad connector/microservice platform. The useful
lesson from workflow products such as Unstract is the separation of stages, immutable run receipts,
versioned profiles and guarded integration boundaries. Knowledge Distiller keeps those properties
inside a small local toolchain. The supplied historical Context Graph draft is evaluated decision
by decision in [docs/HISTORICAL_PR_REVIEW.md](docs/HISTORICAL_PR_REVIEW.md); the current upstream
comparison is recorded in [docs/UNSTRACT_LEARNINGS.md](docs/UNSTRACT_LEARNINGS.md).

## Canonical graph

The authoritative contract is [SPEC.md](SPEC.md); its JSON Schema mirror is
[schema/knowledge.schema.json](schema/knowledge.schema.json). Core validation is implemented in
the Python standard library, so installing `jsonschema` adds a second check but never determines
whether important checks run.

Spec 1.1 adds optional:

- objective source metadata, content hashes and author/publisher/extractor agents;
- W3C-inspired text, page, CSV, JSON, SVG and code selectors;
- stable `claims[]`, while retaining `nodes[].statements` for older consumers;
- `origin`, evidence, `derivation` and `review_status` for inspectable inference boundaries;
- jurisdiction/market/event/mentioned/origin/destination spatial roles with privacy flags;
- provenance-backed `assessments[]` instead of intrinsic source authority;
- `fact_conflicts[]` and retrieval-safe inference chunks.

## Toolchain

| Script | Purpose |
|---|---|
| `validate_knowledge.py` | Complete producer validation, safe URI checks and payload-aware `--prev` merge audit. |
| `build_graph.py` | Recompute counts, cluster membership, canonical edge IDs and both score names idempotently. |
| `build_md.py` | Render the human/Obsidian Markdown view. |
| `build_viewer.py` | Build a self-contained, CSP-hardened offline SVG viewer. |
| `build_bundle.py` | Build the one-file-per-concept bundle with safe deterministic paths. |
| `build_exports.py` | Export deterministic Cypher, CTXT and Obsidian Canvas artifacts. |
| `merge_knowledge.py` | Perform an additive merge, validate it, archive the exact prior bytes and write mandatory JSON and Markdown diffs. |
| `extract_source.py` | Normalize local TXT/MD, HTML, CSV/TSV, JSON and non-macro DOCX into hashed, located segments. |
| `evaluate_golden.py` | Compare explicit graph projections with locally curated golden labels. |
| `verify_evidence.py` | Resolve evidence quotes, positions and copied selectors against `extract_source.py` output. |
| `validate_profile.py` | Validate versioned compiler policy separately from the persistence schema. |
| `run_pipeline.py` | Run content-addressed validate/build stages with immutable receipts and resumable attempts. |
| `serve_api.py` | Optional authenticated loopback-only HTTP and minimal MCP facade for validate/build. |

Each specialized contract is documented under `docs/`. Every script that reads a graph JSON file
reads it under the same 64 MiB house bound the runner and the merge already state, so an oversized
input is rejected with a message instead of consuming memory. The same loader also refuses a
document nested more than 256 containers deep — every validator, audit and merge step walks the
graph recursively, so the bound is stated once rather than left to whichever consumer runs out of
stack first.

## Quick start

```bash
# Validate both compatibility generations
python3 scripts/validate_knowledge.py tests/fixtures/minimal.knowledge.json
python3 scripts/validate_knowledge.py tests/fixtures/contextual.knowledge.json

# Recompute and render a graph
python3 scripts/build_graph.py examples/claude-skills-guide.knowledge.json --write
python3 scripts/build_md.py examples/claude-skills-guide.knowledge.json
python3 scripts/build_viewer.py examples/claude-skills-guide.knowledge.json

# Build a reproducible local run below explicit roots
python3 scripts/run_pipeline.py build claude-skills-guide.knowledge.json \
  --input-root examples --output-root /tmp/kd-runs --artifacts json md bundle

# Run the shipped golden regression and default test suite
python3 scripts/evaluate_golden.py eval/golden_cases.json
python3 -m pytest -q

# Also activate the optional headless-Chrome security/rendering test
KD_BROWSER_TEST=1 python3 -m pytest -q tests/test_viewer_hardening.py
```

`conformance_score` is reproducible format compliance. `quality_score` remains the same-valued
compatibility alias. The validator always reports that semantic accuracy was not evaluated.

## Local extraction

The source adapter never fetches a URL or executes document content. It accepts paths below an
explicit input root and produces deterministic JSON/JSONL segments with source SHA-256, stable IDs
and format-specific selectors. HTML scripts/styles are ignored; CSV formulas stay literal; DOCX is
read as hardened OOXML without macros. PDF, encrypted/macro Office files, unknown binaries, invalid
UTF-8, unsafe archives and symlink escapes fail explicitly.

This stage normalizes source material; it does not invent concepts or feed the runner automatically.
After compilation, `verify_evidence.py graph.knowledge.json normalized.source.json` checks that
every evidence anchor exists in the normalized source it names by `content_sha256`: quoted text
must occur, positions must fall inside a segment and copied selectors must match. Unmatched records
are reported as unverifiable, never as verified, and a found passage is not proof of support.
Semantic compilation must be performed by an external agent/compiler using a validated
`profiles/*.json` policy and writing the Spec 1.1 evidence/origin fields. `run_pipeline.py` starts
only after a canonical `.knowledge.json` already exists.

## Deterministic runner and guarded API

The runner snapshots the input, hashes the active toolchain, derives artifacts in-process, records
stage duration/status/output hashes and writes a final immutable manifest. Identical successful
runs are reused only after their outputs verify; interrupted attempts resume only from valid stage
receipts. Default quotas cap one attempt at 512 MiB and the complete output root at 4 GiB; guarded
atomic writes and bundle installation fail closed before exceeding them. A process-pinned toolchain
fingerprint rejects code/schema/asset drift, and the API latches `503 toolchain_changed` until
restart after the first mismatch. Manifests record `network_access: false` and `telemetry: false`.

The optional API binds only to loopback and authenticates every endpoint with a per-process bearer
token. It generates a random startup token when none is supplied; supervised deployments should
prefer a current-user-owned `0400`/`0600` `--token-file` over an argv-visible `--token`. Read and
write roots, body/input/response sizes, request/run concurrency, exact Host/Origin, allowed keys and
artifact names are fixed. Its MCP subset exposes only `knowledge_validate` and `knowledge_build`;
there is no command execution, CORS or remote connector capability. See
[docs/RUNNER.md](docs/RUNNER.md) for the operational contract.

## Rendering and retrieval safety

The offline viewer embeds graph JSON inertly, escapes script breakouts, uses a restrictive CSP and
builds untrusted content through DOM text nodes. It provides search, zoom, pan, drag, cluster focus,
backlinks and supersession views without Cytoscape, Marked or a CDN.

Source-backed chunks may participate in default retrieval. `kind: "inference"` requires explicit
derivation and `include_in_default_retrieval: false`, preventing generated synthesis from silently
outranking source claims.

## Repository layout

```text
Knowledge Distiller/
├── SPEC.md, SKILL.md, README.md
├── schema/                     # graph and profile contracts
├── profiles/                   # versioned compiler policy
├── scripts/                    # standard-library-first toolchain
├── viewer/                     # dependency-free viewer assets
├── tests/fixtures/             # Spec 1.0 and 1.1 fixtures
├── eval/                       # explicit golden labels
├── docs/                       # runner/extraction/export/merge/eval/profile contracts
└── examples/                   # canonical graph and generated views
```

## Compatibility and version history

Consumers are permissive: an unknown minor spec version or optional field must not make the entire
graph unreadable. Producers are strict: unknown output fields are visible warnings, and unresolved
or unsafe provenance is an error.

| Version | Change |
|---|---|
| v4.0 / Spec 1.1 (2026-09) | Evidence selectors, stable claims, objective source provenance, origin/derivation/review, spatial roles, assessments/conflicts, hardened viewer/bundle, exact-archive merge with JSON/Markdown diffs, source adapters, exports, quota/toolchain-pinned runner/API, profiles and golden evaluation. |
| v4.0 / Spec 1.0 (2026-06) | Formal schema/validator, citations, resource identity, temporal graph, bundle and initial monotonic merge contract. |
| v3.1 | Temporal source/validity/distillation dimensions. |
| v3.0 | Dual Markdown/JSON graph and embedding chunks. |

## License

MIT
