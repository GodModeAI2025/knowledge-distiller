---
type: Concept
id: "troubleshooting-reference"
label: "Troubleshooting Reference"
cluster: "troubleshooting"
confidence: "high"
temporal:
  source_date: "2026-01"
  source_period: null
  valid_from: "2025"
  valid_until: null
  temporal_confidence: "inferred"
sources: ["s1"]
---

# Troubleshooting Reference

**Definition:** A structured catalog of common skill failures mapped to causes and solutions. Covers upload errors, triggering issues, MCP connection failures, instruction non-compliance, and context/performance degradation.

**Warum relevant:** Skill failures are often silent (no error, just wrong behavior). Knowing the failure taxonomy enables faster diagnosis without trial-and-error.

## Beziehungen

- → extends: [[Skill Iteration]]

## Kernaussagen

- Upload errors: SKILL.md must be exactly this spelling (case-sensitive); YAML must have --- delimiters; name must be kebab-case; no XML angle brackets. [1]
- Skill does not trigger: description too generic, missing trigger phrases. Fix by asking Claude 'When would you use [skill name]?' and revising.
- Skill triggers too often: add negative triggers, be more specific, clarify scope explicitly.
- MCP connection issues: verify server connected, check auth tokens, test MCP independently without the skill, verify tool names are case-sensitive.
- Instructions not followed: keep concise, put critical instructions at top with Critical headers, use deterministic language, add performance notes in user prompts rather than SKILL.md.
- Large context / performance degradation: keep SKILL.md under 5,000 words, limit simultaneous skills to 20-50, use progressive disclosure.

# Citations

[1] Building Skills for Claude — Complete Guide
