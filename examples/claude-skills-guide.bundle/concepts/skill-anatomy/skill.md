---
type: Concept
id: skill
label: Skill
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

# Skill

**Definition:** A skill is a folder-based instruction package that teaches Claude how to handle specific tasks or workflows. It consists of a required SKILL.md file (Markdown with YAML frontmatter) and optional subdirectories: scripts/ (executable code), references/ (documentation), and assets/ (templates, fonts, icons).

**Warum relevant:** Skills eliminate the need to re-explain preferences and workflows in every conversation. They encode repeatable processes once, making Claude's behavior consistent and automating multi-step workflows. For MCP builders, skills add the knowledge layer on top of raw tool access.

## Beziehungen

- → uses: [[YAML Frontmatter]]
- → enables: [[Progressive Disclosure]]

## Kernaussagen

- A skill must contain exactly one SKILL.md file (case-sensitive); the folder name must be kebab-case. [1]
- Skills work identically across Claude.ai, Claude Code, and the API without modification.
- Claude can load multiple skills simultaneously; each skill must work alongside others.
- Skills are most powerful for repeatable workflows: document generation, consistent research methodology, multi-step process orchestration.
- A first working skill can be built and tested in 15-30 minutes using the skill-creator skill.

# Citations

[1] Building Skills for Claude — Complete Guide
