# Compiler and Extraction Profiles

Knowledge Distiller profiles are versioned, local policy inputs for a compilation run. They say
what the compiler and source adapter may do; they are not knowledge and are deliberately separate
from `schema/knowledge.schema.json`.

The shipped `profiles/default.json` is conservative: it describes evidence-first compilation,
disables general and spatial inference, prohibits network access and active content, restricts
graph edges and source formats to explicit allowlists, and bounds every material collection or
byte budget.

## Contract boundary

| Artifact | Contains | Must not contain |
|---|---|---|
| Canonical `.knowledge.json` graph | sources, selectors, evidence, claims, concepts, facts, relationships and reviewed derivations | compiler settings, extraction settings, credentials, private reasoning or prompt templates |
| Versioned profile | allowlists, evidence/inference policy, retrieval policy and resource ceilings | graph data, source text, prompt templates, credentials or access tokens |
| Runtime environment | explicitly supplied local paths and, where another system needs them, runtime credentials | persistent graph policy or knowledge claims |

The graph schema remains a portable data contract. A consumer can validate and render a graph
without knowing which compiler profile produced it. Conversely, changing an operational policy
does not silently change the graph schema or leak that policy into every export.

Profiles are not a prompt store. If a future optional model stage needs a prompt asset, that asset
must be separately versioned, access-controlled and content-hashed, then referenced only from an
out-of-band run request or manifest. It must not be embedded in the canonical graph or profile.
Secrets belong in a runtime secret provider and must never be written to either artifact.

## Identity and versioning

Every profile has two identities:

- `profile_version` identifies the profile contract. This validator supports exactly `1.0` and
  rejects an unknown version rather than guessing its semantics.
- `profile_id` is a stable kebab-case name for a policy configuration, such as `default`.

A compatible addition to the understood contract increments the minor version; a removal,
semantic change or newly required field increments the major version. Implement the new schema and
manual validation before using the new version. Merely placing an unknown key in a `1.0` profile
does not enable a feature: it produces a warning and consumers must ignore it.

The current `scripts/run_pipeline.py` is a post-compilation graph/build runner. It does not extract
source material, perform semantic compilation or apply these profile policies. Its manifest must
therefore attest only the graph validation and build stages it actually executes; it must not
claim that a profile was applied.

A future or external semantic compiler that actually validates and applies a profile must retain
the profile ID, version and SHA-256 of the exact UTF-8 profile bytes in its own run manifest. The
profile itself stays outside the graph. Passing `validate_profile.py` alone proves profile
conformance, not that another process honored the profile during compilation.

## Default policy

### Evidence and graph construction

`compiler.evidence_policy.mode` is fixed to `evidence_first`. Nodes, facts, edges, spatial
contexts and derivations all require evidence coverage, at least one item per claim, and a
format-appropriate selector. The default accepts only `source_explicit` and `parser_derived`
attribution. Unsupported claims are rejected and conflicting evidence is preserved.

`compiler.graph_policy.allowed_edge_types` explicitly allows the canonical vocabulary:

```text
uses, enables, based-on, part-of, tension, replaces, extends, example-of
```

Unknown edge types are rejected. Conflict preservation and monotonic merge are mandatory. A
narrower edge allowlist is valid; an empty, duplicated or unknown entry is not.

### Inference

General inference and spatial inference are independently explicit and both are off by default.
The validator enforces these conditions:

- enabling general inference requires `human_review_required: true`;
- `model_inferred` attribution and inferred retrieval chunks require that reviewed opt-in;
- enabling spatial inference additionally requires general inference, spatial evidence and spatial
  human review;
- unattributed claims remain forbidden; and
- hidden chain-of-thought is never persisted. A derivation may retain only the short,
  inspectable activity summary defined by the graph contract.

A reviewed opt-in therefore changes all relevant fields together, for example:

```json
{
  "compiler": {
    "evidence_policy": {
      "allowed_attribution_basis": [
        "source_explicit",
        "parser_derived",
        "model_inferred"
      ]
    },
    "inference_policy": {
      "enabled": true,
      "human_review_required": true,
      "persist_chain_of_thought": false,
      "allow_unattributed_claims": false
    },
    "spatial_policy": {
      "inference_enabled": true,
      "require_evidence": true,
      "human_review_required": true
    }
  },
  "retrieval": {
    "chunk_policy": {
      "include_inferred_content": true
    }
  }
}
```

This is an illustrative fragment, not a complete profile. Start from the default and preserve all
required fields. Enabling a policy permits a later compiler implementation to perform that action;
the profile validator itself never runs a model or inference.

### Extraction

The local source allowlist is `txt`, `md`, `html`, `csv`, `tsv`, `json` and `docx`. Network access
and active-content execution are fixed to `false`. Input is strict UTF-8 where applicable and an
unsupported type is rejected. These controls complement the adapter rules in `EXTRACTION.md`; a
profile never makes a URL, macro, script, binary or unsupported format safe.

### Retrieval chunks

Chunk retrieval remains evidence first. Every included chunk carries an evidence excerpt and
source metadata. Empty-evidence chunks are excluded by default and inferred content is excluded.
Deduplication uses the exact ordered tuple:

```text
source, selector, text_sha256
```

The stable tuple prevents text from different sources or locations being collapsed solely because
it happens to have the same wording. The default emits at most eight chunks per node.

### Limits

Limits are independent hard ceilings. A consuming stage may use a lower internal ceiling but must
not silently exceed the profile value.

The shipped extractor applies `max_input_bytes`, `max_expanded_bytes`, `max_segments`,
`max_segment_chars` and `max_output_bytes` directly as equal default hard maxima. Its CLI permits
lower values but rejects higher ones. Graph/compiler limits remain obligations for the semantic
compiler that creates the canonical graph; the post-compilation runner does not claim to apply a
profile retroactively.

| Field | Default | Bounded object |
|---|---:|---|
| `max_input_bytes` | 67,108,864 | one input file |
| `max_expanded_bytes` | 67,108,864 | expanded archive/package content |
| `max_segments` | 10,000 | normalized extraction segments |
| `max_segment_chars` | 16,000 | one normalized segment |
| `max_nodes` | 10,000 | graph nodes |
| `max_edges` | 50,000 | graph edges |
| `max_evidence_items` | 100,000 | top-level evidence records |
| `max_chunk_chars` | 4,000 | one retrieval chunk |
| `max_output_bytes` | 268,435,456 | one serialized output |

Booleans are not accepted as integers. Zero, negative and values above the validator's documented
safe maxima fail validation.

## Validation

The authoritative validator is complete and standard-library-first:

```bash
python3 scripts/validate_profile.py profiles/default.json --stdlib-only
```

Without `--stdlib-only`, an installed `jsonschema` package applies
`schema/profile.schema.json` as an additional local mirror check:

```bash
python3 scripts/validate_profile.py profiles/default.json
python3 scripts/validate_profile.py profiles/default.json --json
```

No schema or reference is fetched from the network. The JSON Schema intentionally permits unknown
properties because the authoritative validator reports them as warnings. Its structural constants
and conditional opt-in rules mirror the manual safety contract.

Exit status is `0` for a valid profile, `1` for a loaded but invalid profile and `2` when strict
local JSON loading fails. Output ordering is deterministic. Loading rejects URLs and URI schemes,
missing/non-file paths, profiles over 1 MiB, invalid UTF-8, malformed JSON, duplicate keys,
`NaN`/`Infinity`, invalid Unicode surrogates, nesting deeper than 256 containers and non-object
roots. A profile handed to `validate` directly rather than loaded from disk reports the depth bound
as an ordinary error, and the rest of the profile is still checked.

Unknown keys are warnings and have no effect. Consumers must ignore them until a supported profile
version defines their meaning. Keys that look like prompt, private-reasoning trace/scratchpad,
secret, password, credential, token or private-key storage fail even when otherwise unknown. That
guard catches dangerous field design;
it cannot prove that arbitrary prose contains no secret, so profiles still require normal secret
hygiene and review.

Run the focused suite with:

```bash
python3 -B -m pytest -p no:cacheprovider tests/test_profiles.py
```
