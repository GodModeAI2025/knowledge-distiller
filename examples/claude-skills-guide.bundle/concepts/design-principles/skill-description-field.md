---
type: Concept
id: skill-description-field
label: Skill Description Field
cluster: design-principles
confidence: high
temporal:
  source_date: 2026-01
  source_period: null
  valid_from: 2025
  valid_until: null
  temporal_confidence: inferred
sources: [s1]
---

# Skill Description Field

**Definition:** The description field in YAML frontmatter is the primary mechanism by which Claude decides whether to activate a skill. It must follow the structure: [What it does] + [When to use it] + [Key capabilities]. It must include natural-language trigger phrases that real users would say.

**Warum relevant:** This single field determines whether a skill is effective in practice. Vague descriptions lead to under-triggering; overly broad descriptions cause over-triggering.

## Beziehungen

- → enables: [[Testing Strategy]]

## Kernaussagen

- Good description: specific, includes trigger phrases users say, mentions file types if relevant, under 1024 characters. [1]
- Bad description examples: 'Helps with projects' (too vague), 'Creates sophisticated multi-page documentation systems' (missing triggers), 'Implements the Project entity model' (too technical).
- Add negative triggers to prevent over-triggering: 'Do NOT use for simple data exploration (use data-viz skill instead).'
- For under-triggering: add more detail and domain-specific keywords. For over-triggering: add negative triggers and scope constraints.
- The description field appears in Claude's system prompt as the first level of progressive disclosure.

# Citations

[1] Building Skills for Claude — Complete Guide
