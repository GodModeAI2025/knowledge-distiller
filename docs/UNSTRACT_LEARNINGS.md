# What Knowledge Distiller learned from Unstract

Reviewed on 2026-09-04 against the current public
[Unstract repository](https://github.com/Zipstack/unstract), its
[workflow guide](https://docs.unstract.com/unstract/unstract_platform/features/workflows/workflow_overview/),
[Prompt Studio documentation](https://docs.unstract.com/unstract/unstract_platform/features/prompt_studio/)
and [Platform API v1 reference](https://docs.unstract.com/unstract/unstract_platform/api_documentation/versions/v1/).

Unstract and Knowledge Distiller solve adjacent but different problems. Unstract is a multi-user
document-extraction platform with Prompt Studio, adapters/connectors, workflows, API/ETL/HITL
deployments and a Docker service stack. Knowledge Distiller is a portable local knowledge compiler
whose primary asset is a provenance-rich graph file. The right move is to copy architectural
properties, not platform size. This is a pattern-level comparison: no Unstract source code or
AGPL implementation was copied into this MIT-licensed repository.

## Adopted patterns

| Unstract pattern | Knowledge Distiller interpretation |
|---|---|
| Prompt Studio separates extraction configuration from workflow deployment | Versioned compiler profiles are separate from the canonical graph schema. The post-compilation runner does not falsely claim to have applied them. |
| A project/tool is tested before deployment | Every runner build is spec-gated; explicit local golden cases provide a separate, runnable regression check for known semantic projections. |
| Workflows separate source, processing tool, destination and deployment | Implemented local stages separate source normalization from post-compilation derive, validate and artifact build. Creating the semantic graph from normalized segments remains an external agent/compiler responsibility. |
| API executions have explicit execution/status boundaries | Content-addressed runs use immutable request, stage and final receipts with output hashes and safe resume/reuse. |
| API keys and permission levels constrain platform actions | The local facade is narrower: a per-process bearer token, exact loopback Host/Origin, fixed roots/limits and concurrency, an artifact allowlist and only validate/build tool calls. |
| Adapters make source technology replaceable | Format-specific local adapters emit one normalized segment/selector contract. |
| Human review is a separate workflow concern | Claims and evidence can carry explicit review state, while derivations require it. Assessments instead retain assessor, scope, method, date and evidence rather than hiding judgment in a score. |

## Deliberately not adopted

- React/Django/FastAPI/Celery plus Redis, RabbitMQ and PostgreSQL: valuable for a shared platform,
  unnecessary operational surface for a file-first local compiler.
- Broad filesystem/database connectors: they would expand credential, egress and deletion risk
  before the evidence contract is mature.
- Provider/vector-store abstraction inside the canonical format: model and retrieval choice belong
  in an operational profile or runtime, not persisted knowledge.
- Live configuration changing a deployed endpoint: Knowledge Distiller prefers immutable,
  content-addressed runs and explicit new outputs.
- Remote-by-default API deployments: the current API is intentionally local and guarded.
- Promising every document format through best-effort fallbacks: each format needs a real parser,
  selector contract, limits, adversarial tests and a golden set.

## Resulting product direction

The repository implements source normalization plus post-compilation derive, validate, build and
receipt stages. The evidence-first Segment-to-Graph step is deliberately an external agent/compiler
responsibility today: profiles define its policy contract, but no in-repository semantic compiler or
runner stage currently applies a profile. Together, this defines the direction of a trustworthy
**Knowledge Compiler** without claiming that the full chain is already automated:

```text
local source
  -> deterministic segments + locators + hashes
  -> evidence-first semantic graph
     (external agent/compiler; profile-governed contract, not implemented here)
  -> graph/schema/payload validation
  -> immutable run manifest
  -> Markdown / offline viewer / bundle / Cypher / CTXT / Canvas
```

This keeps the most transferable parts of Unstract—clear stage boundaries, test-before-deploy,
versioned configuration, adapters, execution receipts and guarded APIs—while preserving Knowledge
Distiller's strongest differentiator: a small, auditable, vendor-neutral graph artifact.
