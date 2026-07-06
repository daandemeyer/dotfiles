# Sourced by every non-interactive bash via BASH_ENV (set in ~/.claude/settings.json)
# so Claude Code's Bash tool picks up the mise environment for its working directory.
# The guard breaks recursion: `mise env` may run shell commands (templates, _.source)
# in a child bash that would otherwise source this file and run `mise env` again.
if [ -z "${_CLAUDE_MISE_GUARD:-}" ] && command -v mise >/dev/null 2>&1; then
  export _CLAUDE_MISE_GUARD=1
  eval "$(mise env -s bash 2>/dev/null)"
  unset _CLAUDE_MISE_GUARD
fi
