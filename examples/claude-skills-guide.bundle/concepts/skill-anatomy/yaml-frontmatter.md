---
type: Concept
id: yaml-frontmatter
label: YAML Frontmatter
cluster: skill-anatomy
confidence: high
temporal:
  source_date: 2026-01
  source_period: null
  valid_from: 2025
  valid_until: null
  temporal_confidence: inferred
sources: [s1]
---

# YAML Frontmatter

**Definition:** The YAML frontmatter block (delimited by ---) at the top of SKILL.md is the metadata section that Claude reads to decide whether to load the skill. It is the first level of the progressive disclosure system, always loaded into the system prompt even before the full skill body.

**Warum relevant:** The frontmatter is the most critical part of a skill. A poorly written description causes under-triggering (skill never loads) or over-triggering (skill loads for unrelated tasks). The description field is the primary trigger mechanism.

## Beziehungen

- → part of: [[Skill Description Field]]

## Kernaussagen

- Only two fields are required: name (kebab-case) and description (what it does + when to use it, under 1024 characters, no XML angle brackets). [1]
- description MUST include both what the skill does and trigger conditions with specific phrases users would say.
- Optional fields: license, allowed-tools, compatibility (1-500 chars), metadata (author, version, mcp-server, category, tags).
- Forbidden in frontmatter: XML angle brackets < > and skill names containing 'claude' or 'anthropic' (reserved namespace).
- Debugging: Ask Claude 'When would you use the [skill name] skill?' to surface what is triggering or not triggering it.

# Citations

[1] Building Skills for Claude — Complete Guide
