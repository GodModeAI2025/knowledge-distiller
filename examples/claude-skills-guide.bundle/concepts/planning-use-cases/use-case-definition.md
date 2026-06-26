---
type: Concept
id: use-case-definition
label: "Use Case Definition & Success Criteria"
cluster: planning-use-cases
confidence: high
temporal:
  source_date: 2026-01
  source_period: null
  valid_from: 2025
  valid_until: null
  temporal_confidence: inferred
sources: [s1]
---

# Use Case Definition & Success Criteria

**Definition:** Before writing any skill instructions, identify 2-3 concrete use cases. A good use case definition specifies: trigger phrase, ordered steps, tools required, and expected result. Success criteria are defined both quantitatively (trigger rate, tool call count, API error rate) and qualitatively (user autonomy, output consistency).

**Warum relevant:** Skills without defined use cases tend to be vague, triggering incorrectly or failing to complete workflows. Success criteria create measurable targets and a feedback loop for iteration.

## Beziehungen

- → enables: [[Skill Description Field]]

## Kernaussagen

- Quantitative targets: 90% trigger rate on relevant queries; zero failed API calls per workflow; measurable token reduction vs. baseline. [1]
- Qualitative targets: users never need to prompt next steps; workflows complete without user correction; consistent results across sessions.
- Performance comparison benchmark: without skill = 15 messages, 3 failed API calls, 12,000 tokens; with skill = 2 questions, 0 failures, 6,000 tokens.
- Success measurement is partly vibes-based — Anthropic acknowledges active development of more robust tooling.
- Use case definition should answer: what does a user want to accomplish, what multi-step workflows are required, which tools are needed, what domain knowledge should be embedded.

# Citations

[1] Building Skills for Claude — Complete Guide
