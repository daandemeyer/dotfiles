---
name: version-control
description: "Use before changing files in a repository and before running any version-control command: committing, amending, rebasing, writing commit messages or pull request descriptions, landing edits into existing commits, or working in a jj workspace."
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

### Branches

Do every task on its own branch, a series of commits on top of `trunk()`.
Before the first change, run `jj log -r '@-'`. If the parent of the
working-copy commit is the megamerge (`jj log -r 'megamerge()'`), move off it
with `jj new 'trunk()'`. When the task continues an existing branch, start on
top of its head with `jj new <head>` instead.

The megamerge merges the branches of the user, so the user can build and test
them together. Never start work on top of the megamerge, and never run
`jj restack` or `jj push-megamerge`.

Only rewrite the commits of your own branch. Never rewrite a commit of another
branch, even when it shows up in `jj log`: the user or another agent works on
it.

### Landing edits in existing commits

Make the edits in the working-copy commit on top of your branch, then move
them into the commits they belong to:

1. `jj absorb` moves each hunk into the commit that last touched those lines.
   When your branch starts on another branch, run
   `jj absorb --into '<head of the other branch>..@'` instead. Without
   `--into`, absorb also moves hunks into the commits of the other branch.
2. For hunks absorb leaves behind, use `jj squash --into <rev> [paths...]`.
   Squash moves whole files. Never use `--interactive`, it needs a terminal.

Check `jj status` afterwards: the working-copy commit should end up empty.

When a file's remaining hunks belong in different commits, split them first:
`jj split` into several commits on top of the branch, then squash each one
into its target.

### New features

Implement a new feature one commit per step. After each step,
`jj commit -m <message>` describes the working-copy commit and starts a new,
empty one on top of it. Land fixes to earlier steps with absorb or squash as
described above.

When the feature is complete, put a bookmark on the head of the branch:
`jj bookmark create <name> -r @-`. Name it after the topic in a few lowercase
words, for example `journal-prefetch`. If your branch already has a bookmark,
move it to the new head with `jj bookmark move <name> --to @-` instead. Never
move the bookmark of a branch that yours starts on. Tell the user the name of
the bookmark.

Then write the pull request description, as described under "Pull request
descriptions" below, and store it on the bookmark:

    git --git-dir="$(jj git root)" config set branch.<name>.description "$(cat /var/tmp/<name>.md)"

`jj pr` uses the first line as the title and the rest as the body when the
user creates or updates the pull request. Store a new description the same
way when later work on the branch makes the old one wrong.

### Workspaces

Zed creates a jj workspace for an agent thread that runs in its own worktree,
so that several agents can work at the same time. Zed also removes the
workspace when the user archives the thread. Never remove a workspace
yourself, and only create one when the `review` skill asks for it.

You are in a workspace other than the default one when `jj workspace root`
prints a different path than `jj workspace root --name default`. In that case,
run every command from the workspace. Never edit files or run builds in the
default workspace or in another workspace: the user and other agents work
there. Commits are shared through the repository. They show up in `jj log` in
every workspace without pushing.

The workspace is a btrfs snapshot of the default workspace. It contains every
ignored file of the default workspace: `buck-out`, build directories and the
untracked tine files. Builds in it reuse those outputs and only rebuild what
changed, so never delete them or set up new build directories next to them.
The user updates the build directories of the default workspace with
`jj seed`. Never run it yourself.

The meson build directories in the workspace have no `build.ninja`. The one
from the default workspace contains the paths of the default workspace, so it
was removed when the workspace was created. Before the first build in a meson
build directory, run `meson setup --reconfigure <dir>` (through `tine box --`
in a tine project). It takes a few seconds and keeps every build output.

### Rebasing onto the latest trunk

Rebase your branch only when the user asks:

    jj git fetch
    jj rebase --branch @ --onto 'trunk()' --skip-emptied

If your branch starts on another branch instead of on `trunk()`, ask the user
first: the rebase would rewrite the other branch too.

`jj log -r 'conflicts()'` lists the commits with conflicts. Resolve each one in
order, oldest first: `jj new <rev>`, fix the files, then `jj squash` to move
the resolution into the conflicted commit. Afterwards return to the head of
your branch with `jj new <head>`, then build and test it.

## Commit messages

Before writing, learn the project's style. Read its contribution guidelines,
run `jj log -r '::@' -n 20 -T builtin_log_oneline <path>` on the files you
touch, and read the full messages of a few commits by other authors on those
files. Match their subject prefix, terminology and tone. Never invent a new
prefix when one is established.

- The subject is `area: what changed`, imperative, under 60 characters. The
  area is the component the commit touches, for example `journal:` or `nspawn:`.
  The imperative is for the subject only.
- In the body, describe the fix in first person plural: "Let's ..." or
  "we ...". Never write a bare imperative sentence in the body. Use
  contractions (can't, don't, isn't) the way you would say them.

  Bad: Probe whether a file can be chowned to another ID, like the existing
  mknod probe, and skip these tests when it cannot.
  Good: Let's probe whether a file can be chowned to another ID, like the
  existing mknod probe, and skip these tests when we can't do so.
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
that no longer matches. Let's compare the digest on startup and kill the
daemon when it differs.
```

## Pull request descriptions

Briefly explain what the change does and why, following the same rules as
commit messages. The first line is the title, like the subject of a commit.
No headings or bullet lists for a simple change, no file-by-file summaries
that restate the diff, and no detail about unimportant things.

Store the description on the bookmark of the branch, as described under "New
features". Never post it to GitHub yourself.
