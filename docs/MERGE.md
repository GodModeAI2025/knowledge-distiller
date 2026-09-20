# Deterministic Knowledge Graph Merge

`scripts/merge_knowledge.py` performs a local, additive merge of two conforming
Knowledge Distiller graphs. It supports format Spec 1.0 and 1.1, invokes the existing
schema/core validator on both inputs, recomputes derived fields, and validates the result
again with the prior graph as `--prev` monotonicity baseline.

It does not extract documents, invoke a model, execute source-derived commands, access the
network, or silently choose one contradictory knowledge payload over another.

## Command line

An output path is always explicit. An existing regular output is replaced only with
`--force`. Every successful CLI merge also writes three required audit artifacts without
needing opt-in flags: the exact prior bytes in `versions/`, a deterministic JSON delta,
and its deterministic Markdown projection.

```bash
python3 scripts/merge_knowledge.py \
  prior.knowledge.json incoming.knowledge.json \
  --output merged.knowledge.json
```

For that command the automatic reports are `merged.knowledge.diff.json` and
`merged.knowledge.diff.md`. `--diff-report` and `--markdown-diff` may select explicit
alternative paths, but neither report can be disabled. The prior input bytes are always
archived below `versions/` next to the output; the accepted legacy `--archive` flag is now
a no-op. A custom archive location can be selected with `--versions-dir`. At most five active versions are
kept by default; older matching versions are moved losslessly into `versions/retired/`
rather than deleted. Use `--max-versions 1..5` to reduce the active window.

Because archiving is automatic, an in-place merge requires only the explicit overwrite
authorization `--force`:

```bash
python3 scripts/merge_knowledge.py graph.knowledge.json update.knowledge.json \
  --output graph.knowledge.json --force
```

The incoming graph can never be selected as the output. Output, JSON delta and Markdown
delta paths must be different from each other. Delta paths cannot overwrite either input.

## Merge contract

The base graph determines stable order. Existing records keep their position; genuinely
new records are appended in incoming order. Exact duplicates are emitted once. For
compatible enrichment, list members are unioned in base-then-incoming order and missing or
null optional fields can be filled. Conflicting scalar or nested values fail the merge.

| Payload | Identity and behavior |
|---|---|
| Sources | `id`; append new sources, merge compatible optional provenance, reject conflicting payloads |
| Clusters | `id`; union membership temporarily, then recompute it from `node.cluster` |
| Nodes | matching `resource` first, otherwise matching `id`; the base node ID wins and all formal incoming references are remapped |
| Edges | `(source,target,type)`, with sorted endpoints for `tension`; compatible evidence/context is unioned |
| Facts | `id`; compatible evidence/context is unioned, contradictory payload fails by default |
| Evidence | `id`; locator/support/attribution conflicts fail |
| Claims | `id`; evidence and compatible contexts are unioned; statement/origin conflicts fail |
| Assessments | `id`; evidence is unioned; assessor/method/value conflicts fail |
| Fact conflicts | `id`; fact references are immutable, while compatible evidence can be added |
| Chunks | `id`; compatible lists can be enriched, contradictory text or metadata fails |
| Open questions | exact-value stable union |

Unknown extension objects are preserved with the same recursive additive rule. Therefore a
consumer extension is not discarded merely because this tool does not interpret it.

### Resource identity and reference rewriting

If incoming node `new-name` and base node `old-name` carry the same non-null `resource`, the
output contains one node under `old-name`. The tool rewrites formal references in incoming
edge endpoints, `fact.concept`, `claim.node`, and `chunk.concepts`. Cluster membership is
then deterministically rebuilt. Ambiguous duplicate resources, or one node ID associated
with two different resources, are errors.

A previously null `resource` may be enriched when the same node ID supplies it. For the
post-merge `--prev` validation only, the validator baseline receives that identity value so
its resource-first lookup follows the same preserved node instead of reporting a false
drop; all prior payload fields are still audited unchanged. The diff records this narrow
identity enrichment explicitly.

Edge IDs are derived, not independent knowledge: after node aliasing they are regenerated
from the canonical endpoints and relationship type by `build_graph.py`.

### Citation numbering

`metadata.sources` is an ordered citation table. Base sources retain their numbers and new
sources append. Before payload union, the tool rewrites incoming statement/claim `[n]`
markers and `node.citations[].n` through their source IDs. Consequently a citation keeps
pointing to the same source even when the two input graphs used different source order.

### Fact conflicts

Same-ID facts with incompatible payloads fail safely by default. A caller can explicitly
retain both:

```bash
python3 scripts/merge_knowledge.py prior.json incoming.json -o merged.json \
  --fact-conflicts record
```

The incoming fact receives a deterministic content-addressed ID and a Spec 1.1
`fact_conflicts` record connects both retained facts with `relation: tension`. Distinct fact
IDs are also connected automatically when they have the same non-empty `concept`, `metric`,
and temporal payload but different values. Different temporal periods remain an ordinary
time series and are not labelled a conflict.

The generated reason deliberately says that review is required. It is operational merge
evidence, not a claim that either value is true.

Those automatic connections are found through an index keyed by `concept`, `metric` and the
canonical temporal payload, so the cost of merging *N* facts follows *N* rather than *N*
squared. The index preserves the order in which the facts occur, so the sequence of generated
`fact_conflicts` records is the same as it has always been. Where one identity genuinely
carries many different values, the number of conflict records is itself quadratic; that is the
output, not the search.

## Required JSON and Markdown deltas

The automatic JSON delta (or the path selected with `--diff-report`) contains:

- canonical SHA-256 hashes of both input documents and the output;
- counts for sources and every graph collection;
- added and enriched identities in stable order;
- node and fact ID aliases;
- explicitly recorded fact conflicts;
- pre/post validator results; and
- `network_access: false` and `semantic_accuracy_evaluated: false` declarations.

No current timestamp or absolute input path is embedded, so identical input payloads and
options produce identical graph and diff bytes.

The sibling Markdown delta (or `--markdown-diff` path) is generated from that exact JSON
object. It presents content hashes, per-collection counts, added/enriched identities,
aliases, conflict IDs and validation outcomes, then embeds the canonical machine delta.
It therefore remains human-auditable without losing any machine-diff field. Identical
JSON deltas produce identical Markdown bytes.

## Write and failure safety

All validation and merge planning completes before graph or delta files are changed. All
inputs use strict JSON loading: duplicate object keys, `NaN`/`Infinity`, invalid UTF-8 and
isolated Unicode surrogates are rejected before any artifact is created. Each generated
file is written to a temporary sibling, flushed, and atomically replaced. Archive
files are published with create-once semantics and are never overwritten. Symlink output
files/directories and non-regular targets are rejected. Unrelated files are not deleted.

Individual files are atomic; the graph, two deltas and archive are not a single multi-file
filesystem transaction. If the graph succeeds and a later delta write encounters an external
I/O failure, rerun with the same inputs and explicit `--force`. The graph content remains
deterministic.

## Deliberate limits

- The tool merges already-produced graphs; semantic truth and extraction completeness
  remain outside deterministic validation.
- Scalar disagreements on nodes, edges, evidence, claims, assessments, or explicit conflict
  records require a producer/human decision. The merger does not invent a winner.
- Only formal schema references are rewritten after node/fact aliasing. Free text and opaque
  extension strings are preserved verbatim because guessing their meaning would be unsafe.
- Inputs are limited to 64 MiB each to bound local resource use.
- Inputs may not nest more than 256 containers deep. Every step of the merge walks the graph
  recursively, so a deeper document is refused with a message before the first copy is taken
  rather than aborting part-way through with an interpreter recursion error.
