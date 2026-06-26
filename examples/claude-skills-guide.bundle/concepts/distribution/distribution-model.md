---
type: Concept
id: distribution-model
label: Distribution Model
cluster: distribution
confidence: high
temporal:
  source_date: 2026-01
  source_period: null
  valid_from: 2025-12-18
  valid_until: null
  temporal_confidence: explicit
sources: [s1]
---

# Distribution Model

**Definition:** As of January 2026, skills are distributed by downloading and zipping a skill folder, then uploading to Claude.ai via Settings > Capabilities > Skills, or placing in the Claude Code skills directory. Organization-level deployment (shipped December 18, 2025) allows admins to deploy skills workspace-wide with automatic updates and centralized management.

**Warum relevant:** Understanding the distribution model is necessary for both individual use and enterprise rollout. The API path enables production deployments and automated pipelines.

## Beziehungen

- → extends: [[Open Standard & Skills API]]

## Kernaussagen

- Individual installation: Download folder, zip, upload to Claude.ai, or place in Claude Code skills directory. [1]
- Organization deployment (since December 18, 2025): admins deploy workspace-wide with automatic updates.
- Recommended distribution: host on GitHub (public repo + clear README for humans), document in MCP repo, create installation guide.
- Skills should be positioned by outcomes, not technical details: focus on what users can accomplish, not on the folder structure.
- Note: the skill folder itself should NOT contain a README.md; the repo-level README is for human visitors only.

# Citations

[1] Building Skills for Claude — Complete Guide
