---
name: documentation
description: "Use when writing, editing or reviewing documentation: READMEs, man pages, user guides, tutorials, reference pages, help text, release notes or anything under docs/. Not for code comments or commit messages."
---

# Documentation

Write for the person who uses the thing, not the person who built it. For a
library that is the developer who calls it. For a command it is the person who
runs it. Either way they are not implementing it.

Before writing, read a few existing pages of the same kind in the project and
match their structure, terms, tone and markup.

## What goes in

Include only what the reader needs. When in doubt, leave it out.

Leave out implementation details: internal data structures, function names,
algorithms, internal libraries, how the code is organized, the refactor that
shipped the feature. For each detail, ask whether the reader would be surprised
or make a wrong decision without it. Observable behavior passes that test even
when it sounds technical: limits, defaults, what happens on failure or
conflict, where files are written, what survives a restart, what needs root.
The machinery behind that behavior does not.

Describe what the thing is and does now, never how it came to be. No "was added
to replace", "now supports", "previously", "new", "recently", "currently" or
"coming soon". History belongs in changelogs, release notes and migration
guides.

Never describe the document itself. No "This section explains", "In this guide
you will learn" or "The table below shows", and no first sentence that repeats
the heading.

Say what something is, not what it isn't. Mention what it does not do only
where readers predictably assume that it does.

## Structure

- Organize guides by what the reader wants to do. Headings name tasks ("Boot an
  image in a virtual machine"), not components ("VM support"). Reference pages
  such as man pages and option lists keep the structure the project already
  uses.
- Lead with the outcome, then the steps.
- Put prerequisites and limits first: required versions, privileges, platforms,
  size limits.
- Each page answers one question and stands on its own. Readers arrive from a
  search or a link, so link to other pages instead of assuming they were read.
- Where several ways exist, document the recommended one. Mention alternatives
  briefly at the end, or not at all.
- Use lists for steps and for items that are really parallel. Explanations are
  prose.
- Put a warning before the step it applies to, never after.
- Use bold only for the one warning that must not be missed, never as
  decoration.

## Examples

- Readers copy examples without reading the text around them. Every procedure
  shows the correct invocation, ready to copy. Warnings back up the example and
  never replace it.
- Use realistic values, not `foo`, `bar` or `test123`.
- Never invent options, defaults, error messages or output. Check them against
  the code or by running the command, and show output exactly as it appears.
- Walk every procedure literally. If someone following it word for word cannot
  finish, the document is wrong.

## Words

- Use second person, active voice and present tense: "`mkosi` writes the image
  to `mkosi.output/`", not "the image will be written".
- Use one term per concept, matching the code, the command line and the UI
  exactly. A synonym reads as a new concept.
- Use plain verbs: "is", "has", "uses", not "serves as", "features", "boasts" or
  "leverages".
- Use the most common word for what literally happens: an image contains
  files, a command installs packages, a build copies binaries. Never use
  figurative verbs such as "carries", "holds", "ships", "houses", "lives in",
  "sits", "plays", "wires", "drives", "owns" or "lands".
- Say what something does with a verb, not with a colon followed by a list of
  nouns. Bad: "`examples/image-rust-project` is a complete example: a builder
  box, two projects checked out beside it, and an image that carries their
  binaries." Good: "`examples/image-rust-project` builds two Rust projects in a
  box and copies their binaries into an image."
- Cut "just", "simply", "easy" and "obviously", marketing adjectives
  (powerful, seamless, robust, intuitive) and words that models overuse
  (additionally, crucial, delve, enhance, key, showcase, underscore,
  highlight, comprehensive).
- No "not X, but Y" setups, no closing one-liners that repeat the point, no
  "-ing" tails that attach a vague claim ("..., ensuring reliability") and no
  summary paragraph at the end.
- The list of rules under "Comments" in AGENTS.md applies to the sentences of
  documentation too.

## Before finishing

Reread the draft once, looking only for these:

1. Anything the reader does not need to finish the task: cut it.
2. Anything about how it works internally: cut it, unless behavior depends on
   it.
3. Anything about history or about the document itself: cut it.
4. Any sentence you would not say out loud to a colleague: rewrite it.
