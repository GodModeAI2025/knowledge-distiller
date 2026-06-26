---
type: Concept
id: mcp
label: MCP (Model Context Protocol)
cluster: design-principles
confidence: high
resource: "https://modelcontextprotocol.io"
temporal:
  source_date: 2026-01
  source_period: null
  valid_from: 2025
  valid_until: null
  temporal_confidence: inferred
sources: [s1]
---

# MCP (Model Context Protocol)

**Definition:** The Model Context Protocol (MCP) is a connectivity layer that gives Claude access to external services and tools (Notion, Asana, Linear, etc.) and provides real-time data access and tool invocation. In the skills ecosystem, MCP answers 'what Claude can do' while skills answer 'how Claude should do it'.

**Warum relevant:** For skill builders working with MCP integrations, skills serve as the knowledge and workflow layer on top of raw MCP tool access. Without skills, MCP users face a blank slate — they have tool access but no workflow guidance.

## Beziehungen

- → extends: [[Skill]]

## Kernaussagen

- MCP provides connectivity; skills provide the workflow knowledge and best practices on top. [1]
- Without skills, MCP users face each conversation from scratch, inconsistent results, and support requests about how to use the integration.
- With skills, pre-built workflows activate automatically, best practices are embedded in every interaction, and the learning curve is reduced.
- Skills for MCP (Category 3) coordinate multiple MCP calls in sequence and embed domain expertise.
- MCP connection issues should be tested independently from the skill — call an MCP tool directly without the skill to isolate whether the failure is in MCP or the skill.

# Citations

[1] Building Skills for Claude — Complete Guide
