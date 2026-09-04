---
type: Concept
id: "workflow-patterns"
label: "Workflow Patterns"
cluster: "workflow-patterns"
confidence: "high"
temporal:
  source_date: "2026-01"
  source_period: null
  valid_from: "2025"
  valid_until: null
  temporal_confidence: "inferred"
sources: ["s1"]
---

# Workflow Patterns

**Definition:** Five recurring structural patterns for skill instructions, distilled from early adopters and internal teams: (1) Sequential Workflow Orchestration, (2) Multi-MCP Coordination, (3) Iterative Refinement, (4) Context-Aware Tool Selection, (5) Domain-Specific Intelligence. These are heuristics, not prescriptive templates.

**Warum relevant:** Choosing the wrong pattern leads to brittle or incomplete skills. Each pattern targets a specific class of workflow complexity.

> The Home Depot framing: problem-first means user describes outcome and skill orchestrates tools; tool-first means user has tool access and skill provides workflow expertise. Most skills lean one direction.

## Beziehungen

- → uses: [[MCP (Model Context Protocol)]]

## Kernaussagen

- Pattern 1 — Sequential Workflow Orchestration: multi-step processes in a specific order. Key: explicit step ordering, dependencies, validation gates, rollback instructions. [1]
- Pattern 2 — Multi-MCP Coordination: workflows spanning multiple services (e.g., Figma to Drive to Linear to Slack). Key: clear phase separation, data passing between MCPs, centralized error handling.
- Pattern 3 — Iterative Refinement: quality improves with iteration loops (initial draft, quality check, refinement, finalization). Key: explicit quality criteria, validation scripts, termination condition.
- Pattern 4 — Context-Aware Tool Selection: same outcome, different tools depending on context (e.g., file size determines cloud vs. local storage). Key: decision tree, fallback options, transparency.
- Pattern 5 — Domain-Specific Intelligence: skill adds specialized knowledge beyond tool access (e.g., compliance rules, brand standards). Key: domain expertise embedded in logic, compliance-before-action ordering, audit trail.

# Citations

[1] Building Skills for Claude — Complete Guide
