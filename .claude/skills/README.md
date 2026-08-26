# Project skills

Skills that travel with this repo. Each lives in its own folder as a `SKILL.md`
with YAML frontmatter:

    .claude/skills/<skill-name>/SKILL.md

    ---
    name: skill-name
    description: When this applies. This is what Claude matches on, so be specific.
    ---

    The steps to follow...

Invoke explicitly with `/skill-name`, or Claude picks one up when the description
fits what you asked for.

Personal skills, available across every project rather than just this one, go in
`~/.claude/skills/` instead.

Note: built-in skills (`design`, `code-review`, `security-review`, and others) are
already available and must NOT be redefined here — a stub of the same name shadows
the real one.
