# Knowledge Distiller — Format Specification

**Spec version:** `1.0` · **Applies to distiller version:** `4.0` · **Status:** Stable · **Date:** 2026-06-26

> This document is the single authoritative definition of the Knowledge Distiller output
> format. When the prose of `SKILL.md` and this specification disagree, **this specification
> wins.** The machine-checkable mirror of this document is
> [`schema/knowledge.schema.json`](schema/knowledge.schema.json), enforced by
> [`scripts/validate_knowledge.py`](scripts/validate_knowledge.py).

The key words **MUST**, **MUST NOT**, **SHOULD**, **SHOULD NOT**, and **MAY** are used as
described in RFC 2119.

---

## 1. Goals & Non-Goals

**Goals.**
1. Define a stable, vendor-neutral structure that the distillation pipeline writes into and
   that downstream consumers (Obsidian, Neo4j, the bundled viewer, RAG pipelines) read from.
2. Make conformance *machine-checkable* so the format is enforced by code, not by an LLM
   grading its own output.
3. Make incremental merges *safe*: a later pass can only enrich the graph, never silently
   drop knowledge.
4. Keep individual claims *auditable* back to a source.

**Non-Goals.**
- Prescribing storage, indexing, or query infrastructure. The format is files.
- Defining a fixed domain taxonomy. Clusters and labels are producer-chosen.
- Replacing domain schemas (schema.org, JSON-LD vocabularies). The format *references* them.

---

## 2. Terminology

| Term | Meaning |
|---|---|
| **Graph** | One distillation result: a set of nodes, edges, clusters, facts and chunks describing the knowledge in one or more sources. |
| **Node / Concept** | An atomic, source-independent unit of knowledge. Identified by a kebab-case `id`. |
| **Edge / Relationship** | A directed, typed, weighted link between two nodes. |
| **Cluster** | A thematic grouping of nodes. |
| **Fact** | A discrete, datable data point (a number/value with a time reference). |
| **Chunk** | A clean, embedding-ready natural-language passage. |
| **Source** | An ingested input (file/URL). Sources are the citation targets. |
| **Bundle** | The optional exploded, one-file-per-concept directory layout (§7). |

---

## 3. Output Artifacts

A distillation produces, depending on `format`:

| Artifact | When | Contents |
|---|---|---|
| `<name>.knowledge.md` | always | Human/Obsidian-facing rendering (§5) |
| `<name>.knowledge.json` | `json` \| `both` \| `all` | The canonical graph (§4) — the source of truth |
| `<name>.knowledge.diff.md` | `mode: merge` | The delta of a merge (§6) |
| `<name>.knowledge.html` | `all` | Self-contained interactive viewer (`scripts/build_viewer.py`) |
| `<name>.knowledge.cypher` | `all` | Neo4j import |
| `<name>.knowledge.ctxt` | `all` | Cognigy knowledge chunks |
| `<name>.knowledge.canvas` | `all` | Obsidian Canvas |
| `<name>/` bundle | `format: bundle` | Exploded directory layout (§7) |

The `.knowledge.json` is **canonical**: the `.md`, viewer, cypher, canvas and ctxt artifacts
are *derived* from it. Derived rollups (counts, the Concept Map, the Mermaid block, cluster
membership, `index.md` files) MUST be (re)generated deterministically from the JSON
(`scripts/build_graph.py`) rather than authored by hand — see §8.

---

## 4. The Canonical Graph (`.knowledge.json`)

A graph document is a JSON object. `metadata`, `clusters`, `nodes` and `edges` are
**REQUIRED**; `facts`, `chunks`, `open_questions` and `@context` are **OPTIONAL**.

### 4.1 `metadata`

| Field | Req | Type | Notes |
|---|---|---|---|
| `title` | MUST | string | Human title of the graph. |
| `distiller_version` | MUST | `"4.0"` | The producing skill version. |
| `distiller_spec_version` | MUST | string `"x.y"` | The format version this document targets. Consumers that don't know it **MUST** attempt best-effort consumption, not refuse (§9). |
| `sources` | MUST | array | ≥1 `source` object (§4.2). The **ordered** citation list. |
| `distillation_date` | MUST | ISO date | When extraction ran. |
| `domain` | MUST | string | Subject area. |
| `language` | MUST | string | `de`, `en`, … |
| `depth` | MUST | `quick`\|`standard`\|`deep` | |
| `mode` | MUST | `fresh`\|`merge`\|`batch` | |
| `quality_score` | MUST | int 0–100 | Produced by `scripts/validate_knowledge.py`, **not** self-assigned by the model (§8). |
| `concept_count` | MUST | int | = `len(nodes)`. Recomputed, not hand-counted. |
| `relationship_count` | MUST | int | = `len(edges)`. |
| `cluster_count` | MUST | int | = `len(clusters)`. |
| `temporal_confidence` | SHOULD | `explicit`\|`inferred`\|`unknown` | Overall temporal confidence. |
| `fact_count` | MAY | int | = `len(facts)`. |

### 4.2 `source`

The unit of provenance and citation. The position in `metadata.sources` (1-based) is the
citation number used by `[n]` markers in node statements (§4.4).

| Field | Req | Type |
|---|---|---|
| `id` | MUST | string (e.g. `s1`) — referenced by `node.sources[]` and `fact.source` |
| `file` | MUST | string |
| `type` | MUST | string (`pdf`, `url`, …) |
| `date` | MAY | ISO date \| null |
| `url` | MAY | string \| null |

### 4.3 `cluster`

| Field | Req | Type | Notes |
|---|---|---|---|
| `id` | MUST | string | |
| `label` | MUST | string | Display name. (Renamed from v3.1 `name`.) |
| `concepts` | MUST | array of node-id | Recomputed from `node.cluster` (§8). |
| `description` | SHOULD | string | |

### 4.4 `node`

| Field | Req | Type | Notes |
|---|---|---|---|
| `id` | MUST | kebab-case string | Stable identity. Pattern `^[a-z0-9]+(-[a-z0-9]+)*$`. |
| `label` | MUST | string | |
| `cluster` | MUST | cluster-id | MUST resolve to a declared cluster. |
| `confidence` | MUST | `high`\|`medium`\|`low` | Hand-assigned from source evidence. |
| `definition` | MUST | string | Self-contained; understandable without the source. |
| `relevance` | MUST | string | Why it matters in practice. |
| `statements` | MUST | array (≥1) of string | Verifiable claims. MAY end with `[n]` citation markers. |
| `temporal` | MUST | object | `source_date` + `temporal_confidence` required; `source_period`, `valid_from`, `valid_until` optional. |
| `sources` | MUST | array of source-id | Provenance. Each id MUST resolve to a `metadata.sources` id. |
| `resource` | MAY | string \| null | Canonical identity URI of the underlying thing, kept **separate** from citations. Used as a cross-source dedup key (§6). |
| `citations` | SHOULD | array of `citation` | `{n, source, locator?, label?, url?}`. `n` is the 1-based source index a `[n]` marker refers to. |

### 4.5 `edge`

| Field | Req | Type | Notes |
|---|---|---|---|
| `source` | MUST | node-id | MUST resolve to a node. |
| `target` | MUST | node-id | MUST resolve to a node. |
| `type` | MUST | enum (8) | `uses`, `enables`, `based-on`, `part-of`, `tension`, `replaces`, `extends`, `example-of`. |
| `weight` | MUST | number 0–1 | |
| `confidence` | MUST | `high`\|`medium`\|`low` | |
| `label` | SHOULD | string | |
| `id` | MAY | string | Canonical, order-stable id (§8.1). |
| `temporal` | MAY | object | `valid_from`, `valid_until`. |

`tension` is **symmetric**; all other types are directed from `source` to `target`.

### 4.6 `fact`

| Field | Req | Type | Notes |
|---|---|---|---|
| `id` | MUST | string | |
| `statement` | MUST | string | What the value describes. |
| `value` | MUST | string | The datum. |
| `confidence` | MUST | `high`\|`medium`\|`low` | |
| `source` | MUST | source-id | MUST resolve to a `metadata.sources` id. Makes the fact auditable. |
| `temporal` | SHOULD | object | `source_date`/`source_period`/`valid_from`/`valid_until`/`temporal_confidence`. |

### 4.7 `chunk`

| Field | Req | Type | Notes |
|---|---|---|---|
| `id` | MUST | string | |
| `text` | MUST | string | Clean text: **no** markdown, emoji, arrows or `[[wikilinks]]`. Opens with a natural-language time reference. |
| `concepts` | MUST | array of node-id | |
| `temporal_scope` | MAY | string \| null | |
| `token_estimate` | MAY | int | |

---

## 5. Markdown Rendering (`.knowledge.md`)

The `.md` mirrors the JSON for humans and Obsidian. Its YAML frontmatter MUST carry at least
`title`, `distiller_version: "4.0"`, `distiller_spec_version`, `distillation_date`,
`quality_score`, and the three counts. Body sections, in order:

1. `## Concept Map` — generated from edges (§8).
2. `## Kernwissen` — one block per node: confidence, cluster, definition, relevance,
   `Zeitbezug`, relationships (arrows + `[[wikilinks]]`), `Kernaussagen` (statements, carrying
   their `[n]` markers).
3. `## Wissensgraph (Mermaid)` — generated from edges (§8).
4. `## Fakten & Daten` — table; the `Quelle` column uses the `[n]` citation number.
5. `## Offene Fragen` — `deep` depth only.
6. `## Chunks` — block-quoted clean text.
7. `## Quellen` — the **numbered citation list**, one line per source:
   `[n] [label-or-file](url-or-path) — type, date`. This is the resolution target for every
   `[n]` marker in the document.

---

## 6. Incremental Merge (`mode: merge`) — Monotonicity Contract

Merging a new pass into an existing graph is **augmentative, never destructive.**

A merge MUST:
- Preserve every node, edge, statement, citation and fact present in the prior graph. The
  merged graph MUST NOT have fewer nodes, edges, facts or citations than the prior graph.
- Union `statements` and `sources` rather than overwrite.
- Resolve node identity by `resource` (when present) else `id`. The same concept appearing
  under a different `id` but the same `resource` MUST be unified, not forked.

A merge MUST NOT:
- Drop a conflicting value. When two facts give different `value`s for the same
  concept+metric, **keep both** as a time series (distinct `temporal` periods) and add a
  `tension` edge — or, when one supersedes the other, a `replaces` edge with `valid_until`
  set on the superseded entry. The headline "time series instead of conflict" is realized
  here, not assumed.

Each merge MUST archive the prior `.knowledge.json` as `.knowledge.v{N}.json` (keep ≤5) and
emit `.knowledge.diff.md`. The monotonicity contract is **enforced**: running
`validate_knowledge.py --prev <old.json> <new.json>` fails if the new graph shrank.

---

## 7. Bundle Layout (`format: bundle`) — Scalable Output

For large corpora, a graph MAY be emitted as a directory **bundle** (produced by
`scripts/build_bundle.py` from a canonical `.knowledge.json`):

```
<name>/
├── index.md                      # bundle manifest (frontmatter: distiller_spec_version, counts)
├── knowledge.json                # the canonical compiled graph (the source of truth)
├── concepts/
│   ├── index.md                  # lists clusters
│   ├── <cluster-id>/
│   │   ├── index.md              # lists that cluster's concepts (reuses each definition)
│   │   └── <node-id>.md          # one concept: frontmatter + body + "# Citations"
│   └── …
└── facts.md                      # the fact table (optional)
```

Rules:
- A concept's **id is its path** under `concepts/<cluster>/` minus `.md` — identity is
  structural, exactly like the monolithic `id`.
- Every leaf concept file ends in a `# Citations` section.
- `index.md` files are **generated deterministically** (§8); each entry reuses the linked
  concept's `definition` verbatim — no extra model call. Only the bundle-root `index.md`
  carries frontmatter.

The monolithic dual-file output remains the **default**; the bundle is opt-in for scale,
clean git diffs, per-concept atomic edits and progressive disclosure.

---

## 8. Derived Artifacts Are Computed, Not Authored

The model writes *knowledge* (definitions, relevance, statements). All **derived** artifacts
MUST be regenerated by code (`scripts/build_graph.py`) so they can never drift:

- `metadata.concept_count` / `relationship_count` / `cluster_count` / `fact_count`
- `cluster.concepts[]` (from each `node.cluster`)
- the `## Concept Map` section and the `## Wissensgraph (Mermaid)` block
- all `index.md` files in a bundle
- `metadata.quality_score` (from the validator's check pass-rate, §0)

### 8.1 Canonical edge ids
When present, an edge `id` MUST be order-stable. For the symmetric `tension` type, endpoints
are **sorted** before joining (`{a}__tension__{b}` with `a<b`); for directed types the order is
`{source}__{type}__{target}`. This guarantees one stable id per relationship regardless of
the direction it was discovered in.

---

## 9. Conformance & the Consumer Contract

A document is **conformant** when `validate_knowledge.py` reports **0 errors**. The validator
splits findings into:

**ERRORS (a non-conformant document — producers MUST fix):**
- Invalid JSON.
- A missing required field (§4).
- `edge.source`/`edge.target` that does not resolve to a node.
- `node.cluster` that does not resolve to a cluster.
- `node.sources[]` / `fact.source` id that does not resolve to `metadata.sources`.
- `edge.type` outside the 8-value enum.
- A `[n]` citation marker whose `n` exceeds the number of sources.
- A count field that disagrees with the actual array length.
- A chunk containing markdown/wikilink/emoji.
- (with `--prev`) a merge that shrank node/edge/fact/citation counts.

**WARNINGS (still conformant — SHOULD fix):**
- A node with zero citations.
- A cluster with no concepts, or a node not listed in its cluster.
- An unknown `distiller_spec_version`.
- A broken `[[wikilink]]` in the `.md`.
- Unknown extra keys.

**The asymmetric contract (after OKF):** producers SHOULD follow every convention here;
**consumers MUST be permissive.** A consumer (the viewer, an importer, a downstream agent)
MUST NOT reject a document for: unknown extra keys, an unknown `distiller_spec_version`,
missing optional fields, or broken cross-links. It SHOULD degrade gracefully (skip the bad
item, default a missing `type`, keep going). This keeps the format usable as graphs grow, get
refactored, and are partially generated by agents.

---

## 10. Versioning

This spec is versioned `<major>.<minor>` and declared per-document in
`metadata.distiller_spec_version`.

- **Minor** (`1.0 → 1.1`): backward-compatible additions (new optional fields/sections).
  Consumers ignore what they don't know.
- **Major** (`1.x → 2.0`): breaking changes (renaming/removing a required field, changing the
  edge-type enum, changing a reserved filename). A migration note is required.

| Spec | Distiller | Change |
|---|---|---|
| `1.0` | `4.0` | First formal spec: machine-checkable schema, source-id provenance + `[n]` citations, `resource` identity key, monotonic merge contract, bundle layout, computed-not-authored rollups, permissive consumer contract. |

---

## 11. Source Abstraction (informative)

Extraction is best-effort and lives in `SKILL.md`. Conceptually each input maps through one
interface, regardless of file type:

```
extract(input) -> { segments[], source_type, source_date, resource }
```

Adding a new input format means adding one extractor (a fallback chain entry) that returns
this normalized shape. This is an interface contract, not a mandated runtime — the Distiller
is a skill, not a daemon.
