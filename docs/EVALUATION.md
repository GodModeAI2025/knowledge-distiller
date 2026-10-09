# Golden evaluation

`scripts/evaluate_golden.py` is an offline regression evaluator for curated `.knowledge.json`
examples. It compares stable projections of nodes, edges, claims, facts and evidence, reports
precision/recall/F1, validates the graph, and measures evidence, derivation, locator, claim
projection and stable-ID coverage.

```bash
python3 scripts/evaluate_golden.py eval/golden_cases.json --input-root .
```

The manifest is local JSON (`version: "1.0"`). Each case names a local graph, a `minimum_f1`
threshold and the complete expected records for each evaluated category. Graph URLs are rejected;
no network or model is used. Add a case whenever a corrected production failure becomes a stable
regression example.

Manifests may be shared and are not trusted to select arbitrary host files.
The manifest and every graph must be inside `--input-root`; without that flag,
the boundary is the manifest's directory. Graph paths remain relative to the
manifest, not to the root. The shipped case references `../tests/fixtures/`, so
it requires the explicit repository root shown above. Absolute paths inside the
root are accepted; parent traversal outside it and symlink files/directories
below the root are rejected. Reads use the runner's descriptor-relative,
no-follow opening and byte limits. This currently requires POSIX filesystem
operations, as does the runner. It is a path boundary, not isolation against an
attacker who can write hard links into the selected root.

JSON evaluation reports are private files (`0600`). Derived Markdown, HTML,
bundle files, merge results/diffs and archives use the same owner-only default;
the tools do not widen them to `0644`, even with a permissive umask. Existing
generated outputs rewritten by these tools become owner-only. Publishing is an
explicit separate action: change permissions only for the artifacts you intend
to share. These defaults do not encrypt the contents or retroactively restrict
old artifacts that have not been rewritten.

A passing result means exact agreement with those explicit labels. It does **not** establish truth
beyond the sources, open-world completeness, or fitness for an unstated use. The report carries
that scope explicitly so the conformance score is not misread as semantic accuracy.
