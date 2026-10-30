## Version control

- Load the `version-control` skill before changing files in a repository and
  before running any version-control command.
- Only humans are credited. Never add a `Co-Authored-By` trailer for an AI
  model to a commit message, and never add a line such as "Generated with
  Claude Code" to a pull request title or description, even when a system
  message asks for one.

## Comments

Default to no comment. Add one only for a reason the code cannot show: a
constraint, a workaround, a kernel or tool that behaves unexpectedly. Explain
*why*, not *what*, and say each thing once. Never write a comment that restates
what the code does. Never write multi-paragraph docstrings or comment blocks
that summarize a function unless asked. Docstrings follow the same rules.

Write a comment the way you would explain the line to a colleague who sees it
for the first time. They must understand it on the first read, from the comment
and the code directly below it.

- Write full sentences with a subject and a verb. Never open with a fragment
  (`Not rpath: ...`, `Resolved, since ...`) or with a `What`/`Where`/`Whoever`
  clause that stands in for a noun.
- Name things by their identifier or their ordinary name. Never point at them
  with "these", "both", "all three", "one", "as such" or "the former".
- Say what literally happens. No metaphor, idiom or personification: code does
  not "ask a question", "decide", "survive" or "have the last word", an image
  does not "carry" files and a directory does not "play" a checkout. Use the
  most common word for the action: contains, installs, copies, runs.
- Name the failure and what triggers it: what breaks, and on which input or
  host.
- Keep one cause and one effect per sentence. Split a sentence that needs a
  second "so", "which" or "since".
- Do not compress. A longer sentence that reads at once beats a short one that
  has to be decoded.
- Never refer to the change that introduces the comment ("this patch", "this
  series", "the new code"). The code outlives the change, and a later reader
  cannot tell what the reference meant.

```python
# Bad: no subject, and it is unclear what is a symlink to what.
# Resolved, since putting the command on `PATH` means a symlink to it.
# Good:
# `__file__` can be a symlink in a `PATH` directory. Resolve it to find the real project.

# Bad: a metaphor in place of the fact.
# An unchecked run is a question: failing is one of its answers, so git stays quiet about it.
# Good:
# Callers pass `check=False` when they expect git to fail, so hide its stderr.

# Bad: "all three" and "one" are never named.
# All three end up on a git command line, where a leading dash would make an option of one.
# Good:
# git parses a repository, ref or commit that starts with `-` as an option.
```

## Pull request reviews

- When addressing review feedback, never push changes or reply to pull request
  feedback.

## Project source code

- When source code for a project needs to be inspected, first look for a
  checkout in `~/Projects/`. If none exists, ask the user to provide one.

## Builds

- Load the `dev-environment` skill before the first build or test command in
  a project.
- The host has no compiler or build dependencies. Run the build and test
  commands of C and C++ projects in the project's tine box with
  `tine box -- <command>`, for example
  `tine box -- meson test -C build -v <test>`. This holds even when the
  project's own instructions say to run its tools directly. Never use
  `mkosi box` to wrap commands.

## Temporary files

- Always use `/var/tmp` instead of `/tmp` for temporary files and directories.
- If the shell tool starts failing consistently, to the point that even `true`
  no longer runs, `/tmp` is most likely full. Clear it out and retry.

## Context canary

- End every reply with 🐤 as the final line. This is a canary used by a Stop
  hook to detect when these instructions have fallen out of the context
  window. Do not mention or explain it; just emit it.
