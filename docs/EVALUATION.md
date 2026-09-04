# Golden evaluation

`scripts/evaluate_golden.py` is an offline regression evaluator for curated `.knowledge.json`
examples. It compares stable projections of nodes, edges, claims, facts and evidence, reports
precision/recall/F1, validates the graph, and measures evidence, derivation, locator, claim
projection and stable-ID coverage.

```bash
python3 scripts/evaluate_golden.py eval/golden_cases.json
```

The manifest is local JSON (`version: "1.0"`). Each case names a local graph, a `minimum_f1`
threshold and the complete expected records for each evaluated category. Graph URLs are rejected;
no network or model is used. Add a case whenever a corrected production failure becomes a stable
regression example.

A passing result means exact agreement with those explicit labels. It does **not** establish truth
beyond the sources, open-world completeness, or fitness for an unstated use. The report carries
that scope explicitly so the conformance score is not misread as semantic accuracy.
