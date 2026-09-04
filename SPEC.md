# Knowledge Distiller — Format Specification

**Spec version:** `1.1` · **Distiller version:** `4.0` · **Status:** Stable, additive · **Date:** 2026-09-04

This is the authoritative contract for Knowledge Distiller output. `SKILL.md` describes the
workflow; this document defines the data. The machine-readable mirror is
[`schema/knowledge.schema.json`](schema/knowledge.schema.json), and
[`scripts/validate_knowledge.py`](scripts/validate_knowledge.py) enforces the producer contract.

The words **MUST**, **MUST NOT**, **SHOULD**, **SHOULD NOT**, and **MAY** are used as in RFC 2119.

## 1. Goals and non-goals

Knowledge Distiller is a local, file-based **knowledge compiler**. It turns normalized source
material into one canonical graph and derives all human or integration views from that graph.

It aims to:

1. preserve traceable source evidence instead of only plausible prose;
2. distinguish source statements, paraphrases, synthesis, rules, model inference and human input;
3. make changes reproducible, resumable and auditable;
4. preserve earlier knowledge during merges, including disagreement;
5. keep Spec 1.0 graphs consumable while adding the optional Spec 1.1 trust layer.

It does not define a universal truth score, a database platform, a connector marketplace, a
microservice topology, or a hidden model reasoning format. A passing validator establishes format
conformance, not semantic correctness.

## 2. Terminology

| Term | Meaning |
|---|---|
| Graph | One canonical `.knowledge.json` document. |
| Node | An atomic concept with a stable kebab-case `id`; `resource` is its cross-source identity when known. |
| Claim | A stable, provenance-bearing statement projected into `node.statements` for compatibility. |
| Evidence | A reusable record that points to an exact region of one declared source. It describes support, not truth. |
| Edge | A typed relationship between two nodes. |
| Fact | A discrete value or data point, retained even when another fact disagrees. |
| Spatial context | A role-qualified place such as jurisdiction, market scope, event location, origin or destination. |
| Derivation | A short, inspectable account of an inference activity, its inputs and review state. It is not chain-of-thought. |
| Assessment | A purpose-bound evaluation with assessor, method, date and evidence; never intrinsic source authority. |
| Chunk | Clean retrieval text. Source-derived and inference chunks remain distinguishable. |
| Run manifest | An immutable receipt for one deterministic local runner request and its hashed outputs. |

## 3. Artifacts

The canonical graph is `<name>.knowledge.json`. Every other artifact MUST be derived from it.

| Artifact | Producer | Purpose |
|---|---|---|
| `.knowledge.md` | `scripts/build_md.py` | Human/Obsidian view. |
| `.knowledge.html` | `scripts/build_viewer.py` | Dependency-free offline SVG viewer. |
| bundle directory | `scripts/build_bundle.py` | One file per concept plus provenance indexes. |
| `.knowledge.cypher` | `scripts/build_exports.py` | Deterministic Neo4j import statements. |
| `.knowledge.ctxt` | `scripts/build_exports.py` | Textual context interchange. |
| `.knowledge.canvas` | `scripts/build_exports.py` | Obsidian Canvas. |
| `.knowledge.diff.json`, `.knowledge.diff.md` and version archive | `scripts/merge_knowledge.py` | Auditable merge result. |
| run directory and `manifest.json` | `scripts/run_pipeline.py` | Immutable request, stage and output receipts. |

Counts, cluster membership, canonical edge IDs and conformance scores MUST be recomputed by
`scripts/build_graph.py`, not authored by a model. Exporters MUST NOT become independent sources of
truth.

## 4. Canonical graph

The root MUST contain `metadata`, `clusters`, `nodes` and `edges`. `@context`, `facts`, `chunks`,
`open_questions`, `evidence`, `claims`, `assessments` and `fact_conflicts` are optional. Unknown
fields are tolerated by consumers but reported to producers, because silently ignored producer
data is a loss risk.

Reference CLIs load canonical inputs as strict UTF-8 JSON. Duplicate object keys, non-finite
`NaN`/`Infinity` numbers and isolated Unicode surrogate code points MUST be rejected before
validation, merge or artifact publication.

### 4.1 Metadata and scores

Required metadata fields are:

| Field | Contract |
|---|---|
| `title` | Non-empty string. |
| `distiller_version` | Exactly `"4.0"`. |
| `distiller_spec_version` | Producer target, normally `"1.0"` or `"1.1"`. |
| `sources` | Ordered, non-empty source array. Position 1 resolves citation `[1]`. |
| `distillation_date` | Supported ISO-like date. |
| `domain`, `language` | Strings; language has at least two characters. |
| `depth` | `quick`, `standard` or `deep`. |
| `mode` | `fresh`, `merge` or `batch`. |
| `quality_score` | Required compatibility alias, integer 0–100. |
| `concept_count`, `relationship_count`, `cluster_count` | Exact array counts. |

`fact_count` and overall `temporal_confidence` are optional. Spec 1.1 producers SHOULD emit
`conformance_score`; `build_graph.py` writes it and the legacy `quality_score` to the same value.
The formula is `max(0, 100 − 10×errors − 2×warnings)`. Neither field measures truth, completeness,
model quality or fitness for use. Semantic evaluation belongs in an explicit golden evaluation or
provenance-backed `assessment`. The validator requires both stored aliases to equal each other and
the score implied by all diagnostics collected before score-integrity errors; this avoids a
self-referential penalty while making stale scores a publication error.

Supported date granularity is `YYYY-MM-DD`, `YYYY-MM`, `YYYY-Qn`, `YYYY`, `FY` plus a four-digit
year (for example `FY2026`) and intervals such as `2020/2024`.

### 4.2 Sources and objective provenance

Every source MUST have `id`, `file` and `type`. It MAY add `date`, HTTP(S) `url`, `title`,
`authors[]`, `publisher`, `version`, `retrieved_at`, `content_sha256`, `license` and `agents[]`.
Local file names SHOULD be logical or redacted; absolute host paths leak environment data.

Each source agent has `id`, `label`, `type` (`person`, `organization`, `software`) and `role`
(`author`, `publisher`, `creator`, `editor`, `data_producer`, `host`, `extractor`). These are
objective roles. A field such as `authoritative_source` is unsupported: authority depends on a
purpose and method and therefore belongs in `assessments[]`.

URLs MUST use an allowlisted scheme. Producers MUST NOT embed userinfo credentials or store access
tokens, API keys, passwords or signatures in query parameters; both are validation errors.

### 4.3 Evidence and selectors

An evidence record MUST contain:

```json
{
  "id": "evidence-1",
  "source": "source-1",
  "selector": {"type": "TextQuoteSelector", "exact": "quoted source text"},
  "support": "supports",
  "attribution_basis": "source_explicit"
}
```

`support` is one of `supports`, `contradicts`, `contextualizes`, `mentions`.
`attribution_basis` is one of `source_explicit`, `parser_derived`, `model_inferred`, `human_added`.
Optional `excerpt`, `excerpt_sha256` and `review_status` (`unreviewed`, `reviewed`, `rejected`) make
review and drift checks explicit. `parser_derived` and `model_inferred` attribution MUST also carry
an explicit derivation; a guessed evidence-to-source mapping is not source-explicit provenance.

Selectors are W3C Web Annotation-inspired and format-specific:

| Type | Required locator fields |
|---|---|
| `TextQuoteSelector` | `exact`; optional `prefix`, `suffix` |
| `TextPositionSelector` | integer `start`, `end`, with `0 ≤ start ≤ end` |
| `PageSelector` | integer `page ≥ 1` |
| `FragmentSelector` | `fragment` |
| `CsvSelector` | `sheet`, `cell_range` |
| `SvgSelector` | `xpath` |
| `JsonPointerSelector` | `json_pointer` |
| `CodeSelector` | `file`, `symbol` |

Every evidence reference MUST resolve to a top-level evidence ID. Evidence establishes an audit
path; it does not by itself establish that a claim is true.

### 4.4 Clusters, nodes and claims

A cluster MUST have `id`, `label` and `concepts[]`; `description` is optional. `concepts[]` is a
derived field rebuilt from `node.cluster`.

A node MUST have `id`, `label`, `cluster`, `confidence`, `definition`, `relevance`, at least one
`statement`, `temporal` and `sources[]`. Its cluster and source references MUST resolve. Optional
fields are `resource`, `citations[]`, `note`, `evidence[]`, `claim_ids[]` and
`spatial_contexts[]`. Citation `n` MUST match the ordered source ID, not merely fall inside the
array. A citation MAY carry its own selector.

Spec 1.1 claims give important statements stable IDs. A claim MUST contain `id`, `node`,
`statement`, `confidence`, `origin` and `evidence[]`. Its statement MUST also appear verbatim in
the referenced node's `statements[]`; this is the backward-compatible projection. Optional fields
are temporal/spatial context, derivation and review status.

### 4.5 Origin and derivation

Origin is one of:

- `source_stated` or `paraphrased`: MUST reference evidence;
- `synthesized`, `rule_derived` or `model_inferred`: MUST carry a derivation;
- `human_added`: explicitly attributed human input.

A derivation MUST contain `kind`, `activity`, non-empty `inputs[]`, a short `summary` and
`review_status`; it MAY add `rule` and `evidence[]`. The summary explains what transformation was
applied and why its result is bounded. Producers MUST NOT store private chain-of-thought, hidden
scratchpads, secrets or prompts in `reasoning`, `chain_of_thought`, `reasoning_trace`, `scratchpad`
or equivalent private-reasoning fields. The core validator treats these fields as errors even
inside otherwise tolerated extension objects.

### 4.6 Edges

An edge MUST contain resolving `source` and `target` node IDs, `type`, numeric `weight` in `0..1`
and `confidence`. It MAY add a canonical `id`, `label`, validity interval, `evidence`, `origin`, a
short `explanation`, `derivation` and spatial contexts.

The closed type vocabulary is `uses`, `enables`, `based-on`, `part-of`, `tension`, `replaces`,
`extends`, `example-of`. `tension` is symmetric; all others are directed. Its canonical ID sorts
the two endpoints only for `tension`.

### 4.7 Facts and conflicts

A fact MUST contain `id`, non-empty `statement`, string `value`, `confidence` and a resolving
`source`. It MAY add temporal context, `context`, `concept`, `metric`, evidence, origin,
explanation, derivation and spatial contexts.

Different values MUST NOT be overwritten. A `fact_conflicts` item retains two distinct fact IDs,
their `relation` (`tension` or `supersedes`) and a reason; evidence is optional. This models
disagreement without inventing a winner. Temporal facts can coexist as a time series without a
conflict when their periods explain the difference.

### 4.8 Spatial context

Spatial context MUST say what role a place plays; a mentioned location is not automatically a
jurisdiction, market or event location. Each context contains `role`, `place`, `basis`,
`confidence` and at least one evidence reference.

A place has stable URI-like `id`, `label`, and `kind` (`country`, `region`, `city`, `site`,
`address`, `market`, `organization_defined`). It MAY carry external identifiers, geometry,
precision and privacy flags. `sensitive: true` requires `redacted: true`. A redacted place MUST
omit geometry and external identifiers, MUST NOT retain `address`/`site` kind or
`exact`/`address`/`site` precision, and MUST declare a remaining coarse precision such as city,
region or country. These are structural privacy guarantees; the validator cannot prove that a
free-text label or identifier was semantically anonymized. Unknown location is represented by
absence, not by guessed coordinates. Spatial inference is off by default in the shipped profile;
when explicitly enabled it still requires evidence, derivation and review.

### 4.9 Chunks and retrieval

A chunk MUST have `id`, non-empty `text` and `concepts[]`. Text MUST be clean: no Markdown,
wikilinks, emoji, code fences, headings or symbolic arrows. Optional fields are temporal scope,
token estimate, `kind`, evidence, origin, derivation, retrieval inclusion and spatial contexts.

`kind` is `source_claims`, `summary` or `inference`. Inference chunks MUST set
`include_in_default_retrieval: false`; derived prose must not silently outrank source-backed
content in retrieval.

### 4.10 Assessments

An assessment MUST have `id`, `dimension`, `scope`, `value`, `assessor`, `method`, `assessed_at`
and at least one evidence reference. The dimension and value are intentionally open because their
meaning comes from method and scope. Assessments MUST NOT be collapsed into intrinsic source
authority or into the conformance score.

## 5. Rendering and output security

Markdown, bundle and viewer renderers MUST preserve the new provenance fields when present and
remain useful for Spec 1.0 graphs when absent. The standard Markdown order is Concept Map,
Kernwissen, Mermaid, relationship provenance, facts, open questions, chunks, evidence, claims,
assessments, conflicts and sources.

The HTML viewer MUST be self-contained and offline: no CDN, telemetry or network fetch. Embedded
JSON MUST be inert and script-breakout safe. Untrusted text MUST enter the DOM as text, not HTML.
Link targets are allowlisted; unsafe schemes are rendered as text. A restrictive CSP and no-referrer
policy are REQUIRED.

Bundle paths MUST never be formed directly from untrusted IDs. Safe legacy IDs keep readable paths;
unsafe, reserved or case/Unicode-colliding IDs use deterministic hashed components. Writers MUST
reject traversal, symlink escapes and non-regular collisions before the first write, then replace
files atomically without deleting unrelated user files.

## 6. Monotonic merge

`mode: merge` is additive. Identity is `resource` when present, otherwise `id`. A merge MUST:

- preserve every prior source and prior payload item, not merely array counts;
- union list fields deterministically while retaining prior order;
- update all references when a resource-identical node receives a coordinated ID change;
- keep incompatible same-ID payloads explicit instead of choosing silently;
- retain conflicting facts and create a conflict record when applicable;
- validate both the result and `--prev` payload monotonicity before publishing;
- archive the exact validated prior input (at most five active versions) and emit both a
  machine-readable JSON delta and its deterministic Markdown projection.

`scripts/merge_knowledge.py` is the reference implementation. A same-count statement rewrite or
changed fact value is destructive and MUST fail the audit. For output `name.knowledge.json`, the
CLI automatically writes `name.knowledge.diff.json`, `name.knowledge.diff.md` and an archive below
`versions/`; explicit flags may relocate but not disable them. In-place output is permitted only
with `--force`, because the prior bytes are archived automatically before replacement.

## 7. Bundle layout

The reference bundle contains:

```text
<name>.bundle/
├── knowledge.json
├── index.md
├── facts.md                 (when facts exist)
├── evidence.md              (when evidence exists)
├── assessments.md           (when assessments exist)
├── fact-conflicts.md        (when conflicts exist)
└── concepts/
    ├── index.md
    └── <safe-cluster-component>/
        ├── index.md
        └── <safe-node-component>.md
```

Each concept page contains its relationships, claims, evidence and citations. `knowledge.json`
remains canonical.

## 8. Deterministic builds and runs

`build_graph.py` owns rollups, canonical edge IDs and both score names. Running it twice on unchanged
input MUST be byte-semantically idempotent. Renderers and exporters MUST produce stable ordering.

`run_pipeline.py` executes safe in-process stages below explicit input/output roots. Its request key
includes input and tool hashes and parameters. Source snapshots, stage receipts and final manifests
are write-once; successful runs are reused only after output hashes verify, and interrupted runs
resume only from verified receipts. Manifests record durations/statuses, dependency versions,
network access `false` and telemetry `false`.

## 9. Conformance and compatibility

A producer document is conformant when the complete standard-library checks report zero errors.
The optional `jsonschema` dependency adds an independent mirror check; its absence MUST NOT weaken
core validation. Errors include missing/type/enum violations, unsafe or unresolved references,
bad selectors, inference without derivation, unsafe retrieval inclusion, payload-destructive merges
and derived-count mismatches. Credential-bearing URLs and private-reasoning fields are errors.
Warnings include citation gaps, unknown extensions, unknown future spec versions, absolute local
paths and legacy `authoritative_source` fields.

The contract is asymmetric: producers MUST be strict; consumers MUST be permissive. Consumers
SHOULD skip malformed optional items and continue rendering safe content. They MUST NOT reject the
whole graph only because the minor spec version or an extra field is unfamiliar.

## 10. Versioning

The format uses `<major>.<minor>`. Minor releases add optional fields; major releases may change
required contracts and need a migration.

| Spec | Change |
|---|---|
| `1.0` | Formal canonical graph, source IDs/citations, temporal fields, resource identity, bundle and monotonic merge contract. |
| `1.1` | Additive evidence selectors, source agents/hashes, stable claims, origin/derivation/review, role-qualified spatial context, provenance-backed assessments, explicit fact conflicts, safe retrieval flags, conformance naming, hardened viewer/bundle, deterministic runner, exporters, local extraction and golden evaluation. |

Spec 1.0 input remains valid. Spec 1.1 producers retain `quality_score` and `node.statements` so
existing consumers keep working.

## 11. Extraction and profiles

Source normalization is a separate stage. `scripts/extract_source.py` reads only sandboxed local
TXT/Markdown, HTML, CSV/TSV, JSON and non-macro DOCX and emits deterministic segments with source
hashes and format-specific selectors. It never fetches URLs or executes document content. PDF and
unknown binary formats fail explicitly instead of being guessed.

Compiler behavior is versioned separately in `profiles/*.json` and validated by
`scripts/validate_profile.py`. Profiles may set evidence, inference, spatial and chunk policy, but
MUST NOT redefine the graph schema or contain secrets. This separation lets extraction/compilation
instructions evolve without silently changing the persistence contract.

## 12. Local API and MCP boundary

`scripts/serve_api.py` exposes only validation and deterministic artifact builds on loopback. Every
endpoint requires a per-process bearer token; the peer, exact Host and optional Origin must match
the bound local service. Input/output roots, body/input/response size, request/run concurrency,
keys and artifact names are bounded, and CORS is not enabled. The facade invokes Python functions
only: no arbitrary commands, remote fetch, telemetry or non-loopback bind. Its MCP subset exposes
the same narrow operations and rejects unknown methods and parameters. API graph input uses the
same strict JSON and descriptor-relative, no-symlink read contract as the runner.
