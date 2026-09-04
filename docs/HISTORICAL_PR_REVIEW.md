# Critical review of the historical Context Graph PR draft

The supplied artifact is a conflict-marked text snapshot (`feature/context-graph-v4` versus
`main`), not a complete commit/PR with executable code and test history. It is therefore useful as
design evidence, but it cannot establish that the proposed behavior ever worked. The current
implementation adopts the durable intent and rejects assumptions that are unsafe, ambiguous or no
longer accurate.

## Decision summary

| Historical idea | Decision | Current implementation |
|---|---|---|
| Add spatial context | Adopt, redesign | `spatial_contexts[]` carries a role, stable place, basis, confidence, evidence, privacy/precision and optional derivation. |
| Improve provenance | Adopt, redesign | Objective source metadata, content hashes, agents/roles and reusable evidence selectors. |
| Explain why an inference exists | Adopt, redesign | `origin`, evidence, short `explanation`, bounded `derivation` and review status. No hidden reasoning trace. |
| Context-aware graph rather than plain summary | Adopt | Claims, temporal/spatial roles, conflicts, assessments and the per-chunk default-retrieval switch remain structured in canonical JSON. Broader retrieval policy remains in the separate compiler profile. |
| Intrinsic `authoritative_source: high/medium/low` | Reject | Purpose-bound `assessments[]` require assessor, scope, method, date and evidence. |
| Generic `spatial_scope` and `locations: [string]` | Reject | Role-qualified places prevent a mentioned city being mistaken for jurisdiction, market or event location. |
| Infer “Global” or location from authority/address | Reject by default as a compiler-profile target | Unknown should remain absent. The default profile disables general and spatial inference and targets evidence, derivation and human review when inference is enabled. No current semantic compiler applies that profile, and graph/profile validators are not coupled: the graph Spec itself permits `model_inferred` with evidence, a valid derivation and `derivation.review_status: unreviewed`. |
| Persist “Reasoning Trace” | Reject | Private chain-of-thought, prompts and scratchpads are neither portable nor appropriate provenance. |
| Fetch URLs and claim support for arbitrary formats | Reject as default | The local adapter supports a tested allowlist and fails explicitly for unsupported/remote input. |
| Let deep mode add inference into normal RAG text | Reject by default | Inference chunks require derivation and are excluded from default retrieval. |
| Fixed parallel multi-agent pipeline | Adopt only the separation of responsibilities | Source normalization, semantic authoring and deterministic validation are distinct concerns. The repository does not require named agents, spawn a model team or claim that parallel execution improves correctness; an external compiler may choose its own orchestration. |
| Monotonic merge, version archive and delta | Adopt, harden | The merger is payload-aware, preserves prior order/content, archives the exact prior bytes and emits both machine and human diffs; count-only checks are insufficient. |
| HTML, bundle, Cypher, CTXT and Canvas outputs | Adopt only as tested deterministic projections | Each shipped renderer is local and regression-tested. `KD-CTXT/1` is explicitly a project convention, not the draft's unverified compatibility claim with another product. |
| Automatic context handling for arbitrarily large documents | Redesign | Adapters emit bounded deterministic segments under explicit byte/count limits. Segment-to-Graph batching and cross-segment semantic synthesis remain external compiler work rather than an unlimited-context promise. |

## Why the redesign is safer

### Spatial meaning is a relation, not a string

The draft treats EU/Germany/APAC and individual locations as generic scope labels. That loses the
difference between where an event happened, where a rule applies, what market a number covers and
what place was merely mentioned. The Spec 1.1 role vocabulary keeps those semantics separate.
Stable URI-like IDs and external identifiers improve interoperability and referenceability for
non-redacted places; the current merger does not deduplicate nested place records by that ID. For
redacted places, the current contract structurally requires coarse kind/precision and omits
geometry and external identifiers. That reduces precise-location exposure, but cannot prove that
a free-text label or stable ID was semantically anonymized.

### Provenance is observable; authority is an assessment

Author, publisher, extractor, source hash and exact locator are observable provenance. “High
authority” is not: it depends on question, jurisdiction, date and method. The historical field
would encourage downstream systems to prefer one source without retaining why. The current
`assessments[]` structure makes any such judgment scoped and auditable and keeps it out of the
format-conformance score.

### An audit trail is not chain-of-thought

The useful requirement behind “Reasoning Trace” is inspectability and auditability. The current
derivation record supports those goals with the assertion origin, named activity, declared inputs,
an optional rule, a short bounded summary, optional evidence and review status. It does **not** by
itself make a `model_inferred` result reproducible: model/version/parameters and hashes of the exact
input and output artifacts are not required. Storing hidden token-by-token reasoning would be
unstable, unnecessarily sensitive and hard for another tool to validate. The profile contract
rejects prompt, secret and private-reasoning key shapes. The graph validator rejects private-
reasoning fields and credential-bearing URLs; unknown graph extensions remain visible warnings
rather than being silently discarded.

### Capability claims must match tested adapters

The draft promises PDF, PPTX, XLSX, images, URLs and broad fallback execution. No accompanying
implementation proves those paths, and the fallbacks would make privacy, reproducibility and parser
behavior environment-dependent. The current adapter is smaller but real: TXT/MD, HTML, CSV/TSV,
strict JSON and hardened non-macro DOCX, each with deterministic selectors and negative security
tests. Unsupported input fails instead of producing low-confidence pseudo-knowledge.

## Vertical-slice evidence

The accepted ideas are implemented across the whole output path rather than only documented:

- `schema/knowledge.schema.json` and `scripts/validate_knowledge.py` define and enforce Spec 1.1;
- `build_md.py`, `build_bundle.py`, the offline viewer and all exporters retain the new context;
- `merge_knowledge.py` preserves the payload and resolves resource-first identity;
- `extract_source.py` supplies stable locators and hashes;
- `profiles/default.json` keeps inference off by default and policy separate from persistence;
- `evaluate_golden.py` measures explicit labeled dimensions without calling conformance “truth”;
- `run_pipeline.py` supplies immutable stage/output receipts, while `serve_api.py` supplies the
  authenticated local API/MCP boundary;
- fixtures and regression tests cover compatibility, provenance, inference, spatial privacy,
  retrieval exclusion, XSS/path traversal, merge preservation and deterministic output.

## Remaining conscious boundaries

- Semantic concept/claim creation is still an agent/compiler responsibility; the deterministic
  runtime normalizes, validates, merges, evaluates labeled cases and renders, but does not pretend
  to be a domain-independent extraction model.
- Golden evaluation measures only curated fields. It cannot prove open-world completeness or
  source truth.
- PDF/OCR, PPTX, XLSX and remote content need separate trusted adapters, selectors, limits and
  golden datasets before they should be advertised.
- The local API is intentionally narrow. A connector platform or multi-service deployment would
  add operational burden without improving this repository's core evidence contract.

These boundaries are deliberate outcomes of the review, not forgotten TODOs.
