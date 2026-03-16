# Knowledge Distiller v3.1

> Extract **Knowledge**, Not Text.

An AI agent skill that distills any document into structured knowledge graphs with temporal awareness. Atomic concepts, explicit relationships, time context, zero redundancy.

**Dual Output:** `.knowledge.md` (human-readable, Obsidian-compatible) + `.knowledge.json` (machine-readable, JSON-LD graph)

## What It Does

| What it does NOT do | What it DOES |
|---|---|
| 1:1 transcript of the document | Identify and describe concepts atomically |
| Copy-paste with Markdown formatting | Make relationships between concepts explicit |
| Rebuild the table of contents | Eliminate redundancy (same idea = one block) |
| | Build temporal knowledge structure |

## Key Features

- **Knowledge Graph** — Rich nodes with definitions, statements, relevance. Weighted, typed edges. JSON-LD with schema.org context.
- **Temporal Dimension** *(v3.1)* — Three temporal layers: source period, validity range, distillation date. Every node, fact, and chunk carries time context.
- **Hierarchical Clusters** — Concepts grouped into thematic clusters with a concept map overview.
- **Embedding-Ready Chunks** — Clean-text chunks without Markdown syntax. Each starts with a natural-language time reference.
- **Per-Block Confidence** — `high` / `medium` / `low` based on evidence in the source document.
- **Obsidian Wikilinks** — `[[concept]]` syntax for cross-references. Drop into your vault.
- **Any File Format** — PDF, DOCX, PPTX, XLSX, CSV, JSON, HTML, TXT, images, URLs.

## Example Output

The `examples/` directory contains a full distillation of Anthropic's Claude Skills Guide:

- **`claude-skills-guide.knowledge.md`** — 13 knowledge blocks, 7 clusters, 13 facts with temporal context
- **`claude-skills-guide.knowledge.json`** — JSON-LD graph with 13 nodes, 15 edges, 13 embedding-ready chunks

## Temporal Dimension (v3.1)

When you distill documents across years, knowledge without a time axis creates contradictions. "Revenue: 28B" and "Revenue: 34B" aren't conflicting facts — they're a time series.

| Layer | Fields | Purpose |
|---|---|---|
| **Source** | `source_date`, `source_period` | When was the knowledge published? |
| **Validity** | `valid_from`, `valid_until` | When does this information apply? |
| **Distillation** | `distilled` | When was the extraction performed? |

**Time formats:** ISO 8601 with flexible granularity — `2024-12-31`, `2024-Q4`, `FY2024`, `2020/2024`

**Temporal confidence:** `explicit` (date stated in document) / `inferred` (derived from context) / `unknown`

## Graph Schema

Eight relationship types:

| Type | Meaning | Symbol |
|---|---|---|
| `uses` | Dependency | → |
| `enables` | Causality | → |
| `based-on` | Foundation | → |
| `part-of` | Composition | → |
| `tension` | Trade-off / Contradiction | ↔ |
| `replaces` | Supersession | → |
| `extends` | Extension | → |
| `example-of` | Instantiation | → |

## Repository Structure

```
knowledge-distiller/
├── SKILL.md                 # The skill definition (v3.1)
├── README.md
├── LICENSE
├── index.html               # Landing page (GitHub Pages)
├── style.css
├── base.css
├── docs/                    # Landing page (copy)
│   ├── index.html
│   ├── style.css
│   └── base.css
└── examples/
    ├── claude-skills-guide.knowledge.md
    └── claude-skills-guide.knowledge.json
```

## Works With

| Tool | How |
|---|---|
| **Obsidian** | Drop `.knowledge.md` files into your vault. Wikilinks create a navigable graph. |
| **Neo4j / Gephi** | Import `.knowledge.json` nodes and edges directly. |
| **OpenAI / Cohere Embeddings** | Use the `chunks[].embedding_text` field — clean text, no syntax artifacts. |
| **RAG Pipelines** | Each chunk is self-contained with time context. Ready for LangChain / LlamaIndex. |

## Version History

| Version | Date | Changes |
|---|---|---|
| **v3.1** | 16 March 2026 | Temporal dimension: three time layers, per-node temporal objects, time-stamped facts and chunks |
| v3.0 | 16 March 2026 | Dual output (.md + .json), rich graph nodes, embedding-ready chunks, JSON-LD |
| v2.0 | 15 March 2026 | Hierarchical clusters, machine-readable graph, per-block confidence |
| v1.0 | 15 March 2026 | Initial release: 6-phase workflow, atomic blocks, Obsidian wikilinks |

## License

MIT

---

*Created with [Perplexity Computer](https://www.perplexity.ai/computer)*
