---
type: Concept
id: "progressive-disclosure"
label: "Progressive Disclosure"
cluster: "design-principles"
confidence: "high"
temporal:
  source_date: "2026-01"
  source_period: null
  valid_from: "2025"
  valid_until: null
  temporal_confidence: "inferred"
sources: ["s1"]
---

# Progressive Disclosure

**Definition:** A three-level loading system that minimizes token consumption while preserving specialized expertise. Level 1 (YAML frontmatter) is always loaded. Level 2 (SKILL.md body) loads when Claude judges the skill relevant. Level 3 (linked files in references/) loads only when specifically needed within a task.

**Warum relevant:** Without progressive disclosure, having many skills enabled would flood the system prompt with irrelevant instructions, degrading performance. This system allows 20-50 skills to coexist without interference.

## Beziehungen

- → based on: [[YAML Frontmatter]]

## Kernaussagen

- Keep SKILL.md under 5,000 words; move detailed documentation to references/. [1]
- The practical limit before context degradation is 20-50 simultaneously enabled skills.
- References/ files are Claude-navigable: Claude discovers and loads them only as needed.
- Critical instructions should appear at the top of SKILL.md, not buried in later sections.

# Citations

[1] Building Skills for Claude — Complete Guide
