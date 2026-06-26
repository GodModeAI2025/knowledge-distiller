---
type: Concept
id: open-standard-skills-api
label: "Open Standard & Skills API"
cluster: distribution
confidence: high
temporal:
  source_date: 2026-01
  source_period: null
  valid_from: 2026-01
  valid_until: null
  temporal_confidence: inferred
sources: [s1]
---

# Open Standard & Skills API

**Definition:** Anthropic has published Agent Skills as an open standard with the aspiration that skills should be portable across AI platforms (analogous to MCP). The Skills API provides programmatic control via /v1/skills endpoint, container.skills parameter in Messages API, version control through Claude Console, and integration with the Claude Agent SDK.

**Warum relevant:** The open standard signals Anthropic's intent for skills to be an ecosystem-level primitive. The API path unlocks production-scale deployment and agent-system integration beyond Claude.ai.

## Beziehungen

- → based on: [[MCP (Model Context Protocol)]]

## Kernaussagen

- API use cases: applications using skills programmatically, production deployments at scale, automated pipelines and agent systems. Requires Code Execution Tool beta. [1]
- Claude.ai / Claude Code use cases: end users interacting directly, manual testing during development, individual ad-hoc workflows.
- Skills can note platform-specific capabilities in the compatibility field.
- Like MCP, the goal is for skills to be portable — the same skill should work whether using Claude or other AI platforms.
- Public reference: GitHub anthropics/skills repository contains Anthropic-created skills available for customization.

# Citations

[1] Building Skills for Claude — Complete Guide
