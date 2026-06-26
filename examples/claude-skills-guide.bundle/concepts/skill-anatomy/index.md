# Skill Anatomy

What a skill is and how it is structured technically

* [Skill](skill.md) — A skill is a folder-based instruction package that teaches Claude how to handle specific tasks or workflows. It consists of a required SKILL.md file (Markdown with YAML frontmatter) and optional subdirectories: scripts/ (executable code), references/ (documentation), and assets/ (templates, fonts, icons).
* [YAML Frontmatter](yaml-frontmatter.md) — The YAML frontmatter block (delimited by ---) at the top of SKILL.md is the metadata section that Claude reads to decide whether to load the skill. It is the first level of the progressive disclosure system, always loaded into the system prompt even before the full skill body.
