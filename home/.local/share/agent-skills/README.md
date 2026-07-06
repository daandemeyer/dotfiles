# Shared agent skills

Skills here are the single source of truth for both Claude Code and Codex. Each
agent gets a symlink per skill rather than a copy:

    ~/.local/share/agent-skills/<name>/SKILL.md   <- the real file
    ~/.claude/skills/<name>                       -> symlink
    ~/.codex/skills/<name>                        -> symlink

Everything is a mise dotfile symlinked out of this repo, so the copy to edit is
the one in the repo and edits are live the moment they are saved. `skill sync`
only exists to publish a skill that is new or was renamed. `skill show` prints
the repo path.

Never symlink an agent's `skills` directory itself: Codex owns
`~/.codex/skills/.system` and rewrites it on upgrade, so only individual skills
may be linked. The `[dotfiles]` entries use `symlink-each` for exactly this
reason, and the per-skill link lives in the repo at `home/.<agent>/skills/`.

`skill remove` deletes the skill and both of its links. Plain `rm` of the source
leaves the agent-side links dangling, because `mise dotfiles apply` only writes
the entries that are still declared and never removes the ones that went away.
