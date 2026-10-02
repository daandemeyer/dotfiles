---
name: version-control
description: "Use before changing files in a repository and before running any version-control command: committing, amending, rebasing, writing commit messages or pull request descriptions, or landing edits into existing commits."
---

# Version control

Check whether the repository is a Jujutsu (`jj`) repository before running
version-control commands. If it is, use `jj` instead of `git`.

## Committing as you go

Create commits while you work, not at the end. Splitting finished work into
commits afterwards is slow and error-prone, and the result often contains
commits that do not build on their own.

Before starting, plan the work as a sequence of logical steps: for example a
preparatory refactor, a new helper, the behavior change, and the tests and
documentation that belong to each. Then for each step:

1. Make the change.
2. Build it and run the relevant tests.
3. Commit it with its final message, then start the next step on top of it.

Every commit must build and pass the tests on its own. When a later step needs
a fix in an earlier commit, land the fix in that commit instead of folding it
into the current step. If the plan changes along the way, adjust the remaining
steps. Do not rewrite the finished commits into a different split.

## Jujutsu

### Megamerge

Check for a megamerge commit with `jj log -r 'megamerge()'`. If there is one,
always work in a new commit on top of it (`jj new 'megamerge()'`), never in the
commits below it.

Run builds, tests and other commands on top of the megamerge too. The only
exception is checking whether an individual commit builds and passes all tests.
For that, run `jj new <rev>`, run the checks, then return with
`jj new 'megamerge()'`.

### Landing edits in existing commits

Make the edits in the working-copy commit on top of the megamerge, then move
them into the commits they belong to:

1. `jj absorb` moves each hunk into the commit that last touched those lines.
2. For hunks absorb leaves behind, use `jj squash --into <rev> [paths...]`.
   Squash moves whole files. Never use `--interactive`, it needs a terminal.

Check `jj status` afterwards: the working-copy commit should end up empty.

Only edit a commit directly (`jj edit <rev>`) when absorb and squash cannot do
the job, for example when a file's remaining hunks belong in different commits.

### New features

Implement a new feature on top of the megamerge, one commit per step. After
each step, `jj commit -m <message>` describes the working-copy commit and
starts a new, empty one on top of it. Land fixes to earlier steps with absorb
or squash as described above.

When the feature is complete, run `jj stack 'megamerge()..@-'` to move the
whole series below the megamerge as a new parent. `jj stack` starts a fresh
working-copy commit on top of the megamerge afterwards.

## Commit messages

Before writing, learn the project's style. Read its contribution guidelines,
run `jj log -r '::@' -n 20 -T builtin_log_oneline <path>` on the files you
touch, and read the full messages of a few commits by other authors on those
files. Match their subject prefix, terminology and tone. Never invent a new
prefix when one is established.

- The subject is `area: what changed`, imperative, under 60 characters. The
  area is the component the commit touches, for example `journal:` or `nspawn:`.
- Add a body except for genuinely trivial changes, wrapped at 80 columns.
  Match its length to the change: one short paragraph for a simple change,
  more for a subtle bug. Each paragraph holds one idea.
- Open with the problem in present tense: what the code does today and why
  that is wrong. For a bug fix, lead with who hits it and what breaks, then the
  cause. Never open with "This patch" or "This commit".
- Describe the fix by the invariant it restores, not the implementation
  details. The reader has the diff; what they lack is the reason. Cut every
  sentence that is not the problem or the fix.
- If other code paths already handle the case, say so, so the reviewer sees a
  missed case rather than a new pattern. If the fix could plausibly go in a more
  obvious place, say why it does not.
- Paste errors, assertion failures, crash traces and reproducers verbatim as
  an indented block, then explain them. Never paraphrase them.
- Never invent facts. Every number, benchmark result, error message and commit
  hash must come from this session. Check a `Fixes:` hash with `jj show`. If
  you do not know something, leave it out or ask.
- Write only what an upstream maintainer can act on. No local paths, internal
  hostnames, private CI jobs, internal ticket IDs or links that are not public.
- An explanation that helps someone reading the code belongs in a code comment,
  not in the commit message.
- Never add a `Signed-off-by` trailer. It is appended automatically.
- Only humans are credited. Never mention an AI or agent, and never add a
  `Co-Authored-By`, `Co-Developed-By`, `Assisted-by` or similar trailer for an
  AI model.
- Write it like you would say it to a colleague at their desk: short words,
  plain verbs, no bullet lists, no headings. The list of rules under "Comments"
  in AGENTS.md applies to the sentences of a commit message too.
- No marketing adjectives (robust, powerful, seamless, comprehensive, elegant),
  no hedging ("Note that", "Importantly", "It's worth noting"), no recap
  paragraph at the end and no double negatives.

A whole commit body, in full:

```
The daemon keeps a stale mount digest, so a second build reuses a namespace
that no longer matches. Compare the digest on startup and kill the daemon
when it differs.
```

## Pull request descriptions

Briefly explain what the change does and why, following the same rules as
commit messages. No headings or bullet lists for a simple change, no
file-by-file summaries that restate the diff, and no detail about unimportant
things.
