# Deterministic offline exports

`scripts/build_exports.py` projects a Knowledge Distiller graph into three
portable formats without network access, telemetry, subprocesses, or external
Python packages:

- Neo4j 5 Cypher (`.cypher`)
- Knowledge Distiller CTXT (`.ctxt`)
- Obsidian/JSON Canvas (`.canvas`)

The canonical `.knowledge.json` file remains the source of truth. Each export
contains a canonical JSON copy of the complete input graph. This is intentional:
fields introduced after Spec 1.0/1.1, and fields the visual projection does not
understand, are retained instead of being silently dropped.

## Usage

Validate first, then export:

```sh
python3 scripts/validate_knowledge.py graph.knowledge.json
python3 scripts/build_exports.py graph.knowledge.json --output-dir exports
```

The command above writes:

```text
exports/graph.knowledge.cypher
exports/graph.knowledge.ctxt
exports/graph.knowledge.canvas
```

Select one or several formats by repeating `--format`:

```sh
python3 scripts/build_exports.py graph.knowledge.json \
  --output-dir exports \
  --format cypher \
  --format canvas
```

`--basename NAME` overrides the generated filename stem. It must be a single,
safe path component. Output files are written to a temporary sibling and moved
into place atomically.

## Determinism and identity

For the same parsed JSON value, the exporter produces the same UTF-8 bytes:

- object keys are serialized in lexical order with finite JSON numbers only;
- entity collections and generated Canvas items are ordered by stable identity;
- IDs use the first 24 hexadecimal characters of SHA-256 over an internal kind
  prefix and the source entity ID;
- Canvas positions are an integer grid derived from sorted cluster and concept
  IDs;
- CTXT framing and generated line endings are fixed;
- no date, hostname, random value, network result, or environment path is added.

Changing a display label does not change a concept's exported identity. Changing
its source `id` does. Array order inside the canonical source graph is retained
and therefore contributes to the graph-level SHA-256; array order is treated as
source data, not guessed to be irrelevant.

Duplicate IDs are rejected. Generating a new identity from an array index would
look deterministic while making references ambiguous, so the exporter does not
do that for graph entities.

## Cypher

The Cypher output is a statement script for Neo4j 5. It can be imported with a
Neo4j client that accepts a file of semicolon-terminated statements, for example
`cypher-shell -f graph.knowledge.cypher` when `cypher-shell` is already installed
and configured.

The script is safe to re-run against the same database state:

- a uniqueness constraint protects `KDEntity.kd_id`;
- nodes and relationships use `MERGE` with stable `kd_id` values;
- subsequent properties use `SET +=`;
- user content never becomes a label, property name, relationship type, variable,
  or executable fragment;
- strings escape quotes, backslashes, control characters, and Unicode line
  separators;
- nested objects and arrays are encoded as canonical JSON string properties.

Only exporter-owned labels and relationship types occur in syntax. The source
edge type is stored as `relationship_type`, while the navigable relationship is
always `KD_RELATIONSHIP`. Each source edge is also represented as a first-class
`KDEdge` node so evidence, derivation, temporal, spatial, and future metadata can
be attached without pretending that a Neo4j relationship is a node.

### Cypher mapping

| Knowledge graph value | Neo4j projection |
| --- | --- |
| Whole graph | `KDGraph`, including `source_graph_json` and `source_sha256` |
| `metadata.sources[]` | `KDSource` and `HAS_SOURCE` |
| `clusters[]` | `KDCluster` and `HAS_CLUSTER` |
| `nodes[]` | `KDConcept`, `HAS_CONCEPT`, and `IN_CLUSTER` |
| `edges[]` | `KDEdge`, `FROM_CONCEPT`, `TO_CONCEPT`, and `KD_RELATIONSHIP` |
| `evidence[]` | `KDEvidence` and `SUPPORTED_BY` links where references resolve |
| `claims[]` | `KDClaim`, `CLAIM_OF`, and `SUPPORTED_BY` |
| `facts[]` | `KDFact`, plus source/evidence links |
| `chunks[]` | `KDChunk`, `ABOUT`, and evidence links |
| `assessments[]` | `KDAssessment` and evidence links |
| `fact_conflicts[]` | `KDConflict` |
| `open_questions[]` | deterministic `KDQuestion` nodes |
| spatial/temporal/derivation and unknown fields | canonical `*_json` and/or `raw_json` properties |

Every entity has `raw_json`; the graph node has the entire canonical input in
`source_graph_json`. Useful scalar fields are also projected as native Neo4j
properties for querying.

## KD-CTXT/1

CTXT is a Knowledge Distiller interchange convention, not a claim of
compatibility with an unrelated third-party format also named “context”. It is
UTF-8, line-oriented metadata followed by readable context bodies.

A file begins with:

```text
#!KD-CTXT/1
graph-sha256: <64 lowercase hex characters>
spec-version-json: "1.1"
title-json: "Example"
graph-json: {<canonical one-line JSON>}
```

Each record then has this form:

```text
===
id-json: "chunk-id"
title-json: "Context title"
kind-json: "source_claims"
concepts-json: ["concept-id"]
evidence-json: ["evidence-id"]
origin-json: "source_stated"
spatial-contexts-json: [{...}]
derivation-json: {...}
record-json: {...}
body-utf8-bytes: 123
body-sha256: <64 lowercase hex characters>
---
Readable context body
```

All `*-json` values occupy exactly one physical line. Unicode NEL and line/paragraph
separators are escaped to preserve that invariant. A parser must consume exactly
`body-utf8-bytes` bytes after the `---` line and then consume the one framing LF;
separators such as `===` may legally occur inside a body. The framing LF is not
part of the byte count or hash. `body-sha256` verifies the readable projection.

`record-json` is authoritative for a record and includes the original chunk in
`raw`. `graph-json` is authoritative for the whole input, including future and
unknown fields. The readable body normalizes CRLF/CR to LF, while `record-json`
retains the original text exactly.

If the graph has no chunks, the exporter creates one readable, explicitly
`synthetic` context record per concept from its definition, relevance, and
statements. This does not create or add claims to the embedded source graph.

## Obsidian Canvas

The `.canvas` file uses JSON Canvas core item shapes:

- clusters become `group` nodes in sorted columns;
- concepts become `text` nodes in sorted rows;
- graph edges become Canvas edges with stable IDs;
- concept cards show definition, relevance, confidence, statements, spatial
  contexts, claims, derivation summaries, and referenced evidence where present.

Source-provided Markdown/HTML control characters are escaped in generated concept
cards. The original content remains unchanged in the extension data.

The namespaced `x-knowledge-distiller` extension appears at the document and item
levels. At document level it contains:

```json
{
  "format": "Knowledge Distiller Canvas Extension",
  "format_version": "1",
  "source_sha256": "...",
  "source_graph": {},
  "visible_concepts": []
}
```

The full `source_graph` makes the exported file lossless at creation time. Per-item
extension objects retain the corresponding raw cluster, concept, claim, or edge.
JSON Canvas readers are expected to ignore unknown keys, so the file remains
usable as a Canvas projection.

## Important limits

- The exporter parses JSON and checks identities; it is not a replacement for
  `validate_knowledge.py`. Invalid references are not invented or repaired.
- No geocoder, authority database, external ontology, or LLM is called. Spatial
  labels and identifiers are exported exactly as supplied.
- Confidence, authority assessments, and provenance remain separate source fields.
  The exporter does not infer that a confident statement is authoritative.
- Derivation summaries are data fields, not hidden chain-of-thought. The exporter
  neither generates nor requests private reasoning traces.
- The Cypher script is additive. Re-running it updates matching entities but does
  not delete database entities removed from a later source graph. Import into a
  clean database or perform an explicit, graph-scoped reconciliation when deletion
  semantics are required.
- Cypher entity IDs are global hashes of entity kind plus the source `id`; the
  graph node itself is content-addressed. Two unrelated graphs that reuse local
  IDs such as `overview` will therefore merge those same-kind entities in one
  Neo4j database. Use separate databases or namespace source IDs before export
  when cross-graph identity is not intended.
- `source_graph_json` and per-entity `raw_json` can make Cypher files large. Neo4j
  property-size and client statement-size limits still apply; very large graphs
  need a purpose-built bulk loader.
- Obsidian can display the core Canvas data, but an editor may discard unknown
  extension keys when it rewrites a file. Do not treat a Canvas file edited and
  re-saved by another application as a guaranteed lossless round trip; retain the
  canonical `.knowledge.json`.
- Canvas layout is deterministic, not a semantic layout optimizer. Adding an ID
  that sorts earlier can shift later columns or rows.
- Canvas visibly expands concepts and relationships. Facts, chunks, assessments,
  conflicts, and unknown future entities remain available in `source_graph` even
  when they are not drawn as separate cards.
- None of the formats redacts sensitive content. Full canonical JSON is embedded
  by design, so apply the project's data-handling policy before sharing an export.
