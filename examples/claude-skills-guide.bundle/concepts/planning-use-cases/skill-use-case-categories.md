---
type: Concept
id: "skill-use-case-categories"
label: "Skill Use Case Categories"
cluster: "planning-use-cases"
confidence: "high"
temporal:
  source_date: "2026-01"
  source_period: null
  valid_from: "2025"
  valid_until: null
  temporal_confidence: "inferred"
sources: ["s1"]
---

# Skill Use Case Categories

**Definition:** Anthropic has identified three canonical skill use case categories based on patterns observed across early adopters and internal teams: (1) Document and Asset Creation, (2) Workflow Automation, and (3) MCP Enhancement.

**Warum relevant:** Knowing which category a skill belongs to determines the right design patterns, technical approach, and test strategy. Categories are not mutually exclusive but most skills lean toward one.

## Beziehungen

- → based on: [[Use Case Definition & Success Criteria]]
- → enables: [[Workflow Patterns]]

## Kernaussagen

- Category 1 — Document and Asset Creation: embedded style guides and templates, quality checklists, no external tools required. Real example: frontend-design skill. [1]
- Category 2 — Workflow Automation: step-by-step workflows with validation gates, iterative refinement loops, built-in review suggestions. Real example: skill-creator skill.
- Category 3 — MCP Enhancement: coordinates multiple MCP calls in sequence, embeds domain expertise, handles common MCP errors. Real example: sentry-code-review skill from Sentry.
- Problem-first framing: user describes outcome, skill orchestrates tools. Tool-first framing: user has tool access, skill provides workflow expertise.
- Most skills lean one direction; knowing which framing fits the use case helps choose the right workflow pattern.

# Citations

[1] Building Skills for Claude — Complete Guide
