# Sourced by every non-interactive bash via BASH_ENV (set in ~/.codex/config.toml)
# so Codex shell commands pick up the mise environment for their working directory.
# The guard breaks recursion: `mise env` may run shell commands (templates, _.source)
# in a child bash that would otherwise source this file and run `mise env` again.
if [ -z "${_CODEX_MISE_GUARD:-}" ] && command -v mise >/dev/null 2>&1; then
  export _CODEX_MISE_GUARD=1
  eval "$(mise env -s bash 2>/dev/null)"
  unset _CODEX_MISE_GUARD
fi
