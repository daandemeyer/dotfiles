---
name: shared-skills
description: "Use when creating, editing, or removing a skill that is shared between Claude Code and Codex, or when asked how the `skill` command works."
---

# Shared skills

Claude Code and Codex draw on the same set of skills. The `skill` command manages
them; use it rather than moving files around by hand.

## Listing

    skill list [--json]

Prints every shared skill with its description, as a table or, with `--json`, as
a list of `{"name", "description"}` objects. Check this before adding a skill, so
an existing one gets extended instead of duplicated.

## Creating

    skill new <name> [--description "..."]

`<name>` must be lowercase kebab-case. The new skill is live in both agents on
their next session.

## Editing

    skill show <name>

That prints the directory to edit. Change `SKILL.md` inside it, add any supporting
files alongside, then publish:

    skill sync

Edits have no effect on either agent until `skill sync` runs.

## Removing

    skill remove <name>

## Writing SKILL.md

Frontmatter must set `name` and `description`. Quote the description if it
contains `: `, otherwise the YAML fails to parse. Write the description so it says
when the skill applies, since that is all either agent sees until the skill is
invoked.

`allowed-tools`, `license` and `disable-model-invocation` work in both agents.
`user-invocable` is Claude-only and `metadata.short-description` is Codex-only;
each ignores the other's keys, so one file can carry both.

Supporting files such as `references/`, `scripts/` and `assets/` live beside
`SKILL.md` and are reachable by either agent once `SKILL.md` points at them.
