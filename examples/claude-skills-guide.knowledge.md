---
title: "Complete Guide to Building Skills for Claude — Knowledge Distillation"
source: "Building Skills for Claude — Complete Guide (Anthropic)"
source_type: PDF
source_date: "2026-01"
source_period: null
temporal_confidence: "inferred"
distilled: "2026-03-16"
concepts:
  - skill
  - SKILL.md
  - YAML frontmatter
  - progressive-disclosure
  - composability
  - portability
  - MCP
  - skill-use-case-categories
  - skill-file-structure
  - skill-description-field
  - testing-strategy
  - skill-triggering
  - distribution-model
  - open-standard
  - skills-api
  - workflow-patterns
  - sequential-orchestration
  - multi-mcp-coordination
  - iterative-refinement
  - troubleshooting
entities:
  - Anthropic
  - Claude
  - Claude.ai
  - Claude Code
  - MCP (Model Context Protocol)
  - skill-creator skill
  - anthropics/skills (GitHub)
  - Claude Developers Discord
  - Claude Agent SDK
  - Sentry
  - Linear
  - Notion
  - Figma
domain: "AI / LLM Customization / Prompt Engineering"
confidence: high
companion: "claude-skills-guide.knowledge.json"
clusters:
  - name: "Skill Anatomy"
    concepts: [skill, SKILL.md, YAML frontmatter, skill-file-structure, progressive-disclosure]
    description: "What a skill is and how it is structured technically"
  - name: "Design Principles"
    concepts: [progressive-disclosure, composability, portability, MCP, skill-description-field]
    description: "Core design philosophy governing how skills should be built"
  - name: "Planning & Use Cases"
    concepts: [skill-use-case-categories, use-case-definition, success-criteria]
    description: "How to identify and define what a skill should do"
  - name: "Testing & Iteration"
    concepts: [testing-strategy, skill-triggering, skill-iteration]
    description: "How to validate, debug, and improve skills over time"
  - name: "Distribution"
    concepts: [distribution-model, open-standard, skills-api]
    description: "How skills are shared, installed, and used programmatically"
  - name: "Workflow Patterns"
    concepts: [sequential-orchestration, multi-mcp-coordination, iterative-refinement, context-aware-tool-selection, domain-specific-intelligence]
    description: "Proven structural patterns for skill instructions"
  - name: "Troubleshooting"
    concepts: [upload-errors, triggering-issues, mcp-connection-issues, instruction-compliance, context-management]
    description: "Common failure modes and their solutions"
---

## Concept Map

> Relationship structure of core concepts, grouped by cluster

### 🏷️ Skill Anatomy: What a skill is and how it is structured

- **[[Skill]]**
  - → part of: [[SKILL.md]]
  - → uses: [[YAML frontmatter]]
  - → enables: [[Progressive Disclosure]]
  - → enables: [[Composability]]
  - → enables: [[Portability]]

- **[[SKILL.md]]**
  - → part of: [[Skill File Structure]]
  - → uses: [[YAML frontmatter]]

- **[[YAML Frontmatter]]**
  - → enables: [[Skill Triggering]]
  - → part of: [[SKILL.md]]

### 🏷️ Design Principles: Core philosophy

- **[[Progressive Disclosure]]**
  - → enables: [[Token Efficiency]]
  - → based on: [[Three-Level Loading System]]

- **[[Composability]]**
  - ↔ tension with: [[Portability]] (skills must be self-contained yet work alongside others)

- **[[MCP]]**
  - → extends: [[Skill]]
  - ↔ tension with: [[Skill]] (MCP = what Claude can do; Skill = how Claude should do it)

### 🏷️ Planning & Use Cases: How to identify skill purpose

- **[[Skill Use Case Categories]]**
  - → example of: [[Document & Asset Creation]]
  - → example of: [[Workflow Automation]]
  - → example of: [[MCP Enhancement]]

- **[[Use Case Definition]]**
  - → enables: [[Success Criteria]]
  - → enables: [[Skill Description Field]]

### 🏷️ Testing & Iteration: Validation loop

- **[[Testing Strategy]]**
  - → uses: [[Skill Triggering]]
  - → uses: [[Functional Tests]]
  - → uses: [[Performance Comparison]]

- **[[Skill Iteration]]**
  - → based on: [[Testing Strategy]]
  - → extends: [[Skill Description Field]]

### 🏷️ Distribution: How skills reach users

- **[[Distribution Model]]**
  - → uses: [[Open Standard]]
  - → uses: [[Skills API]]
  - → enables: [[Organization-Level Skills]]

### 🏷️ Workflow Patterns: Proven instruction structures

- **[[Sequential Orchestration]]**
  - → uses: [[MCP]]
  - → example of: [[Workflow Patterns]]

- **[[Multi-MCP Coordination]]**
  - → extends: [[Sequential Orchestration]]
  - → uses: [[MCP]]

- **[[Iterative Refinement]]**
  - → example of: [[Workflow Patterns]]
  - → uses: [[Functional Tests]]

---

## Kernwissen

### Skill

📊 Confidence: `high` | 🏷️ Cluster: Skill Anatomy | 📅 Stand: 2026-01 (inferred)

**What:** A skill is a folder-based instruction package that teaches Claude how to handle specific tasks or workflows. It consists of a required `SKILL.md` file (Markdown with YAML frontmatter) and optional subdirectories: `scripts/` (executable code), `references/` (documentation), and `assets/` (templates, fonts, icons).

**Why relevant:** Skills eliminate the need to re-explain preferences and workflows in every conversation. They encode repeatable processes once, making Claude's behavior consistent and automating multi-step workflows. For MCP builders, skills add the knowledge layer on top of raw tool access.

**Zeitbezug:** Gültig ab 2025 (feature launch inferred) · Quelle: null (guide published ~January 2026)

**Relationships:**
- Uses → [[SKILL.md]]
- Enables → [[Progressive Disclosure]]
- Extends → [[MCP]] (knowledge layer on top of connectivity layer)

**Kernaussagen:**
- A skill must contain exactly one `SKILL.md` file (case-sensitive); the folder name must be kebab-case.
- Skills work identically across Claude.ai, Claude Code, and the API without modification (portability).
- Claude can load multiple skills simultaneously (composability); each skill must work alongside others.
- Skills are most powerful for repeatable workflows: document generation, consistent research methodology, multi-step process orchestration.
- A first working skill can be built and tested in 15–30 minutes using the `skill-creator` skill.

> 💡 The analogy used in the guide: MCP is the professional kitchen (tools and ingredients); skills are the recipes (how to use them). This cleanly separates connectivity concerns from workflow knowledge.

---

### YAML Frontmatter

📊 Confidence: `high` | 🏷️ Cluster: Skill Anatomy | 📅 Stand: 2026-01 (inferred)

**What:** The YAML frontmatter block (delimited by `---`) at the top of `SKILL.md` is the metadata section that Claude reads to decide whether to load the skill. It is the first level of the progressive disclosure system — always loaded into the system prompt, even before the full skill body.

**Why relevant:** The frontmatter is the most critical part of a skill. A poorly written description causes under-triggering (skill never loads) or over-triggering (skill loads for unrelated tasks). The `description` field is the primary trigger mechanism.

**Zeitbezug:** Gültig ab 2025 · Quelle: null

**Relationships:**
- Part of → [[SKILL.md]]
- Enables → [[Skill Triggering]]

**Kernaussagen:**
- Only two fields are required: `name` (kebab-case, must match folder name) and `description` (what it does + when to use it, under 1024 characters, no XML angle brackets).
- `description` MUST include both what the skill does and trigger conditions (specific phrases users would say).
- Optional fields: `license`, `allowed-tools`, `compatibility` (1–500 chars, environment requirements), `metadata` (custom key-value pairs: author, version, mcp-server, category, tags).
- Forbidden in frontmatter: XML angle brackets `< >`, skill names containing "claude" or "anthropic" (reserved namespace). Violation risk: prompt injection via system prompt.
- Skills named with reserved prefixes or containing XML will be rejected at upload.

> 💡 Debugging tip: Ask Claude "When would you use the [skill name] skill?" — Claude will quote the description back, revealing what is triggering (or not triggering) it.

---

### Progressive Disclosure

📊 Confidence: `high` | 🏷️ Cluster: Design Principles | 📅 Stand: zeitlos (architectural principle)

**What:** A three-level loading system that minimizes token consumption while preserving specialized expertise. Level 1 (YAML frontmatter) is always loaded. Level 2 (SKILL.md body) loads when Claude judges the skill relevant. Level 3 (linked files in `references/`) loads only when specifically needed within a task.

**Why relevant:** Without progressive disclosure, having many skills enabled would flood the system prompt with irrelevant instructions, degrading performance. This system allows 20–50 skills to coexist without interference.

**Zeitbezug:** Gültig ab 2025 · Quelle: null

**Relationships:**
- Based on → [[Three-Level Loading System]]
- Enables → Token Efficiency
- Tension with → [[Composability]] (more skills = more potential conflicts)

**Kernaussagen:**
- Keep `SKILL.md` under 5,000 words; move detailed documentation to `references/`.
- Evaluating 20–50 simultaneous skills is the practical limit before context degradation.
- `references/` files are Claude-navigable: Claude discovers and loads them only as needed within the task.
- Critical instructions should appear at the top of `SKILL.md`, not buried in later sections.

---

### Skill Use Case Categories

📊 Confidence: `high` | 🏷️ Cluster: Planning & Use Cases | 📅 Stand: 2026-01 (inferred)

**What:** Anthropic has identified three canonical skill use case categories based on patterns observed across early adopters and internal teams: (1) Document & Asset Creation, (2) Workflow Automation, and (3) MCP Enhancement.

**Why relevant:** Knowing which category a skill belongs to determines the right design patterns, technical approach, and test strategy. Categories are not mutually exclusive but most skills lean toward one.

**Zeitbezug:** Gültig ab 2025 · Quelle: null

**Relationships:**
- Example of → [[Document & Asset Creation]] (frontend-design skill, office/docx skill)
- Example of → [[Workflow Automation]] (skill-creator skill)
- Example of → [[MCP Enhancement]] (sentry-code-review skill)

**Kernaussagen:**
- **Category 1 — Document & Asset Creation:** Embedded style guides and templates, quality checklists, no external tools required. Real example: `frontend-design` skill.
- **Category 2 — Workflow Automation:** Step-by-step workflows with validation gates, iterative refinement loops, built-in review suggestions. Real example: `skill-creator` skill.
- **Category 3 — MCP Enhancement:** Coordinates multiple MCP calls in sequence, embeds domain expertise, handles common MCP errors. Real example: `sentry-code-review` skill (from Sentry).
- Problem-first framing: user describes an outcome → skill orchestrates tools. Tool-first framing: user has tool access → skill provides workflow expertise. Most skills lean one direction.

---

### Use Case Definition & Success Criteria

📊 Confidence: `high` | 🏷️ Cluster: Planning & Use Cases | 📅 Stand: 2026-01 (inferred)

**What:** Before writing any skill instructions, identify 2–3 concrete use cases. A good use case definition specifies: trigger phrase, ordered steps, tools required, and expected result. Success criteria are defined both quantitatively (trigger rate, tool call count, API error rate) and qualitatively (user autonomy, output consistency).

**Why relevant:** Skills without defined use cases tend to be vague, triggering incorrectly or failing to complete workflows. Success criteria create measurable targets and a feedback loop for iteration.

**Zeitbezug:** Gültig ab 2025 · Quelle: null

**Relationships:**
- Enables → [[Skill Description Field]]
- Enables → [[Testing Strategy]]

**Kernaussagen:**
- Quantitative targets (aspirational, not precise): 90% trigger rate on relevant queries; zero failed API calls per workflow; measurable token reduction vs. baseline.
- Qualitative targets: users never need to prompt next steps; workflows complete without user correction; consistent results across sessions.
- Performance comparison benchmark in the guide: without skill = 15 messages, 3 failed API calls, 12,000 tokens; with skill = 2 questions, 0 failures, 6,000 tokens.
- Measurement is partly "vibes-based" — Anthropic acknowledges active development of more robust tooling.

---

### Skill Description Field

📊 Confidence: `high` | 🏷️ Cluster: Design Principles | 📅 Stand: 2026-01 (inferred)

**What:** The `description` field in YAML frontmatter is the primary mechanism by which Claude decides whether to activate a skill. It must follow the structure: `[What it does] + [When to use it] + [Key capabilities]`. It must include natural-language trigger phrases that real users would say.

**Why relevant:** This single field determines whether a skill is effective in practice. Vague descriptions lead to under-triggering; overly broad descriptions cause over-triggering.

**Zeitbezug:** Gültig ab 2025 · Quelle: null

**Relationships:**
- Part of → [[YAML Frontmatter]]
- Enables → [[Skill Triggering]]
- Based on → [[Use Case Definition & Success Criteria]]

**Kernaussagen:**
- Good description: specific, includes trigger phrases users say, mentions file types if relevant, under 1024 characters.
- Bad description: "Helps with projects" (too vague); "Creates sophisticated multi-page documentation systems" (missing triggers); "Implements the Project entity model" (too technical).
- Add negative triggers to prevent over-triggering: "Do NOT use for simple data exploration (use data-viz skill instead)."
- For under-triggering: add more detail and domain-specific keywords. For over-triggering: add negative triggers and scope constraints.

---

### Testing Strategy

📊 Confidence: `high` | 🏷️ Cluster: Testing & Iteration | 📅 Stand: 2026-01 (inferred)

**What:** Skills should be tested at three increasing levels of rigor: (1) manual testing in Claude.ai (fast iteration), (2) scripted testing in Claude Code (repeatable validation), and (3) programmatic testing via the Skills API (systematic evaluation suites). Effective testing covers three areas: triggering tests, functional tests, and performance comparison.

**Why relevant:** Skills are living documents that degrade or drift without testing. The recommended approach (iterate on one challenging task until it works, then extract the pattern) provides fast signal before broad coverage.

**Zeitbezug:** Gültig ab 2025 · Quelle: null

**Relationships:**
- Uses → [[Skill Triggering]]
- Uses → [[Functional Tests]]
- Uses → [[Performance Comparison]]
- Enables → [[Skill Iteration]]

**Kernaussagen:**
- **Triggering tests:** Verify the skill loads on obvious tasks, paraphrased requests, and does NOT load on unrelated queries. Run 10–20 test queries.
- **Functional tests:** Verify correct outputs, successful API calls, error handling, edge case coverage.
- **Performance comparison:** Count messages, failed API calls, and tokens consumed with vs. without the skill enabled.
- The `skill-creator` skill helps design and refine skills but does NOT run automated test suites or produce quantitative evaluation results.
- Pro tip: Iterate on a single challenging task to success before expanding to coverage testing.

---

### Skill Iteration

📊 Confidence: `high` | 🏷️ Cluster: Testing & Iteration | 📅 Stand: 2026-01 (inferred)

**What:** Skills are living documents that should be iteratively improved based on operational signals. Three failure modes each have distinct solutions: under-triggering, over-triggering, and execution issues.

**Why relevant:** A skill that worked at launch may degrade as users discover edge cases. Iteration based on clear diagnostic signals prevents skill abandonment.

**Zeitbezug:** Gültig ab 2025 · Quelle: null

**Relationships:**
- Based on → [[Testing Strategy]]
- Extends → [[Skill Description Field]]

**Kernaussagen:**
- Under-triggering signals: skill doesn't load automatically, users manually enabling it, support questions about when to use it. Solution: enrich the description with more keywords and trigger phrases.
- Over-triggering signals: skill loads for irrelevant queries, users disabling it, confusion about purpose. Solution: add negative triggers, be more specific about scope.
- Execution issues: inconsistent results, API failures, user corrections needed. Solution: improve instructions, add error handling, use deterministic scripts for critical validations.
- Advanced: For critical validations, use a bundled script (`scripts/validate.py`) rather than language instructions — code is deterministic, language interpretation is not.

---

### Distribution Model

📊 Confidence: `high` | 🏷️ Cluster: Distribution | 📅 Stand: 2026-01 (inferred, references "January 2026" explicitly)

**What:** As of January 2026, skills are distributed by downloading and zipping a skill folder, then uploading to Claude.ai via Settings > Capabilities > Skills, or placing in the Claude Code skills directory. Organization-level deployment (shipped December 18, 2025) allows admins to deploy skills workspace-wide with automatic updates and centralized management.

**Why relevant:** Understanding the distribution model is necessary for both individual use and enterprise rollout. The API path enables production deployments and automated pipelines.

**Zeitbezug:** Gültig ab 2025-12-18 (org-level), 2026-01 (current model) · Quelle: null

**Relationships:**
- Uses → [[Open Standard]]
- Uses → [[Skills API]]
- Enables → [[Organization-Level Skills]]

**Kernaussagen:**
- Individual installation: Download folder → zip → upload to Claude.ai, or place in Claude Code skills directory.
- Organization deployment (since December 18, 2025): admins deploy workspace-wide with automatic updates.
- Recommended distribution: host on GitHub (public repo + clear README for humans), document in MCP repo, create installation guide.
- Skills should be positioned by outcomes, not technical details: "enables teams to set up complete project workspaces in seconds" not "a folder containing YAML frontmatter."

---

### Open Standard & Skills API

📊 Confidence: `high` | 🏷️ Cluster: Distribution | 📅 Stand: 2026-01 (inferred)

**What:** Anthropic has published Agent Skills as an open standard with the aspiration that skills should be portable across AI platforms (similar to MCP). The Skills API provides programmatic control via `/v1/skills` endpoint, `container.skills` parameter in Messages API, and integration with the Claude Agent SDK.

**Why relevant:** The open standard signals Anthropic's intent for skills to be an ecosystem-level primitive, not a Claude-only feature. The API path unlocks production-scale deployment and agent-system integration.

**Zeitbezug:** Gültig ab 2026-01 · Quelle: null

**Relationships:**
- Extends → [[Distribution Model]]
- Uses → [[Claude Agent SDK]]

**Kernaussagen:**
- API use cases: applications using skills programmatically, production deployments at scale, automated pipelines and agent systems. Requires Code Execution Tool beta.
- Claude.ai / Claude Code use cases: end users interacting directly, manual testing during development, individual ad-hoc workflows.
- Skills can note platform-specific capabilities in the `compatibility` field.
- Resources: Skills API Quickstart, Create Custom Skills, Skills in the Agent SDK (all linked in official documentation).

---

### Workflow Patterns

📊 Confidence: `high` | 🏷️ Cluster: Workflow Patterns | 📅 Stand: 2026-01 (inferred)

**What:** Five recurring structural patterns for skill instructions, distilled from early adopters and internal teams. These are heuristics, not prescriptive templates.

**Why relevant:** Choosing the wrong pattern leads to brittle or incomplete skills. Each pattern targets a specific class of workflow complexity.

**Zeitbezug:** Gültig ab 2025 · Quelle: null

**Relationships:**
- Example of → [[Sequential Orchestration]]
- Example of → [[Multi-MCP Coordination]]
- Example of → [[Iterative Refinement]]
- Example of → [[Context-Aware Tool Selection]]
- Example of → [[Domain-Specific Intelligence]]

**Kernaussagen:**
- **Pattern 1 — Sequential Workflow Orchestration:** Multi-step processes in a specific order. Key: explicit step ordering, dependencies, validation gates, rollback instructions.
- **Pattern 2 — Multi-MCP Coordination:** Workflows spanning multiple services (e.g., Figma → Drive → Linear → Slack). Key: clear phase separation, data passing between MCPs, centralized error handling.
- **Pattern 3 — Iterative Refinement:** Quality improves with iteration loops (initial draft → quality check → refinement → finalization). Key: explicit quality criteria, validation scripts, termination condition.
- **Pattern 4 — Context-Aware Tool Selection:** Same outcome, different tools depending on context (e.g., file size → cloud vs. local storage). Key: decision tree, fallback options, transparency about choices.
- **Pattern 5 — Domain-Specific Intelligence:** Skill adds specialized knowledge beyond tool access (e.g., compliance rules, brand standards). Key: domain expertise embedded in logic, compliance-before-action ordering, audit trail.

> 💡 The "Home Depot" framing: problem-first (user describes outcome → skill orchestrates tools) vs. tool-first (user has tool access → skill provides workflow expertise). Most skills lean one direction.

---

### Troubleshooting Reference

📊 Confidence: `high` | 🏷️ Cluster: Troubleshooting | 📅 Stand: 2026-01 (inferred)

**What:** A structured catalog of common skill failures mapped to causes and solutions. Covers upload errors, triggering issues, MCP connection failures, instruction non-compliance, and context/performance degradation.

**Why relevant:** Skill failures are often silent (no error, just wrong behavior). Knowing the failure taxonomy enables faster diagnosis.

**Zeitbezug:** Gültig ab 2025 · Quelle: null

**Relationships:**
- Extends → [[Skill Iteration]]
- Uses → [[YAML Frontmatter]]
- Uses → [[Progressive Disclosure]]

**Kernaussagen:**
- Upload errors: `SKILL.md` must be exactly this spelling (case-sensitive); YAML must have `---` delimiters; `name` must be kebab-case; no XML angle brackets anywhere.
- Skill doesn't trigger: description too generic; missing trigger phrases; fix by asking Claude "When would you use [skill name]?" and revising based on the quoted description.
- Skill triggers too often: add negative triggers; be more specific; clarify scope with "Use specifically for X, not for general Y."
- MCP connection issues: verify MCP server connected (Settings > Extensions); check auth tokens; test MCP independently (without skill) to isolate failure location; verify tool names are case-sensitive.
- Instructions not followed: instructions too verbose (keep concise, use bullets); critical instructions buried (put at top, use `## Critical` headers); ambiguous language (use `CRITICAL: Before calling X, verify: ...`); model "laziness" (add performance notes, more effective in user prompts than SKILL.md).
- Large context/performance degradation: SKILL.md over 5,000 words; too many skills enabled (20–50 simultaneous max); all content loaded instead of using progressive disclosure.

---

## Fakten & Daten

| Fakt | Wert | Kontext | Stand | Confidence |
|------|------|---------|-------|------------|
| Skill build time (first skill) | 15–30 minutes | Using `skill-creator` skill | 2026-01 | medium |
| Description field max length | 1024 characters | YAML frontmatter `description` field | 2026-01 | high |
| `compatibility` field max length | 500 characters | YAML frontmatter optional field | 2026-01 | high |
| `SKILL.md` recommended max size | 5,000 words | Above this, context performance degrades | 2026-01 | medium |
| Simultaneous skills limit (practical) | 20–50 skills | Beyond this, context performance degrades | 2026-01 | medium |
| Organization-level skills shipped | December 18, 2025 | Workspace-wide admin deployment | 2025-12-18 | high |
| Token reduction with skill (example) | 50% (12,000 → 6,000) | Performance comparison in guide (illustrative) | 2026-01 | low |
| Message reduction with skill (example) | 87% (15 → 2) | Performance comparison in guide (illustrative) | 2026-01 | low |
| Skill triggering target rate | 90% | On relevant queries; aspirational benchmark | 2026-01 | medium |
| Skills API endpoint | `/v1/skills` | Requires Code Execution Tool beta | 2026-01 | high |
| API parameter for skills | `container.skills` | Messages API requests | 2026-01 | high |
| Skills open standard | Agent Skills standard | Like MCP, cross-platform portability goal | 2026-01 | high |
| Public skills repository | `anthropics/skills` on GitHub | Anthropic-created reference skills | 2026-01 | high |
| Partner skills directory (examples) | Asana, Atlassian, Canva, Figma, Sentry, Zapier | Ecosystem partner skills | 2026-01 | high |

---

## Offene Fragen

- What exactly constitutes the "Code Execution Tool beta" required for Skills API — is this the same as Claude's built-in code execution, or a separate beta program?
- How does the open standard specification for Agent Skills differ from MCP technically? No schema or versioning details provided in this guide.
- The success metric of "90% trigger rate on relevant queries" is described as "aspirational" — what tooling is Anthropic developing to measure this more precisely?
- The `allowed-tools` field is listed as optional with example syntax but not fully documented here — what is the complete list of allowed tool identifiers?
- How does the three-level progressive disclosure system interact with skill conflicts when multiple skills are loaded simultaneously? No conflict resolution mechanism described.
- The guide states "15–30 minutes to build and test your first working skill" — this claim depends heavily on use-case complexity; no complexity classification is provided.

---

> Destilliert aus: `file_DAFB9A07-31C6-444A-9B70-F782B1249071.pdf` am 2026-03-16
> Quellzeitraum: null — Dokumentation (kein periodischer Bericht). Publikationsdatum: ~2026-01 (inferred from "January 2026" reference on p. 19 and org-level skills shipped "December 18, 2025")
> Destillationsmethode: Knowledge Distiller Skill v3.1
> Companion: `claude-skills-guide.knowledge.json`
