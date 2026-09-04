# Fakten & Daten

| Fakt | Wert | Kontext | Zeitbezug | Raum | Konfidenz | Quelle | Evidence |
|------|------|---------|-----------|------|-----------|--------|----------|
| Skill build time for first skill | 15-30 minutes | Using the skill-creator skill for an initial working skill | 2026-01 |  | medium | [1] |  |
| Description field maximum length | 1024 characters | YAML frontmatter description field constraint | 2026-01 |  | high | [1] |  |
| compatibility field maximum length | 500 characters | YAML frontmatter optional compatibility field | 2026-01 |  | high | [1] |  |
| SKILL.md recommended maximum size | 5,000 words | Above this limit, context performance degrades; move detailed docs to references/ | 2026-01 |  | medium | [1] |  |
| Simultaneous skills practical limit | 20-50 skills | Beyond this, context performance degrades; use selective enablement | 2026-01 |  | medium | [1] |  |
| Organization-level skills feature shipped | December 18, 2025 | Workspace-wide admin deployment with automatic updates | 2025-12-18 |  | high | [1] |  |
| Token consumption with skill (illustrative example) | 6,000 tokens (vs. 12,000 without skill) | Performance comparison example in guide; 50% reduction | 2026-01 |  | low | [1] |  |
| Message count with skill (illustrative example) | 2 messages (vs. 15 without skill) | Performance comparison example in guide; 87% reduction | 2026-01 |  | low | [1] |  |
| Skill triggering aspirational target | 90% on relevant queries | Aspirational benchmark, not a precise threshold; Anthropic developing better measurement tooling | 2026-01 |  | medium | [1] |  |
| Skills API endpoint | /v1/skills | For listing and managing skills programmatically; requires Code Execution Tool beta | 2026-01 |  | high | [1] |  |
| API parameter for adding skills to requests | container.skills | Used in Messages API requests to include skills | 2026-01 |  | high | [1] |  |
| Public skills repository | github.com/anthropics/skills | Anthropic-created reference skills available for customization | 2026-01 |  | high | [1] |  |
| Partner skills directory examples | Asana, Atlassian, Canva, Figma, Sentry, Zapier | Partners with skills in the directory as of guide publication | 2026-01 |  | high | [1] |  |
