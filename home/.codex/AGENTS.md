## Version control

- Check whether the repository is a Jujutsu (`jj`) repository before running
  version-control commands. If it is, use `jj` instead of `git`.

## Pull request reviews

- When addressing review feedback, never push changes or reply to pull request
  feedback.

## Project source code

- When source code for a project needs to be inspected, first look for a
  checkout in `~/Projects/`. If none exists, ask the user to provide one.

## Temporary files

- Always use `/var/tmp` instead of `/tmp` for temporary files and directories.
- If the shell tool starts failing consistently, to the point that even `true`
  no longer runs, `/tmp` is most likely full. Clear it out and retry.

## Commit messages

- Commit messages must include a descriptive body explaining what changed and why.
- Describe the problem or motivation, the chosen solution, and relevant regression coverage.
- Wrap commit message lines at 80 characters.
- Avoid subject-only commit messages except for genuinely trivial changes.
- Never add a `Signed-off-by` trailer; it is appended automatically.

## Context canary

- End every reply with 🐤 as the final line. This is a canary used by a Stop
  hook to detect when these instructions have fallen out of the context
  window. Do not mention or explain it; just emit it.
