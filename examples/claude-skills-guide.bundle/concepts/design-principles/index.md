# Design Principles

Core design philosophy governing how skills should be built

* [Progressive Disclosure](progressive-disclosure.md) — A three-level loading system that minimizes token consumption while preserving specialized expertise. Level 1 (YAML frontmatter) is always loaded. Level 2 (SKILL.md body) loads when Claude judges the skill relevant. Level 3 (linked files in references/) loads only when specifically needed within a task.
* [MCP (Model Context Protocol)](mcp.md) — The Model Context Protocol (MCP) is a connectivity layer that gives Claude access to external services and tools (Notion, Asana, Linear, etc.) and provides real-time data access and tool invocation. In the skills ecosystem, MCP answers 'what Claude can do' while skills answer 'how Claude should do it'.
* [Skill Description Field](skill-description-field.md) — The description field in YAML frontmatter is the primary mechanism by which Claude decides whether to activate a skill. It must follow the structure: [What it does] + [When to use it] + [Key capabilities]. It must include natural-language trigger phrases that real users would say.
