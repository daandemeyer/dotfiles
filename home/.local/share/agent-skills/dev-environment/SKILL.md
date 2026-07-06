---
name: dev-environment
description: "Use when a project needs a toolchain to build, test or run: installing language runtimes, compilers, build tools or dependencies, or deciding how a project is built locally. Also use the moment a build, test or run command fails over a missing tool (linker `cc` not found, command not found, missing header or library), before working around it by hand."
---

# Development environments

Provision everything through mise, pinned, except C and C++.

## Missing tools

A command that fails because a tool is not installed is a toolchain gap, and
this file decides how to close it. That holds mid-task as much as during setup:
a failing `cargo test` is the same problem as a fresh checkout.

Never work around it in place. No hand-rolled compiler or linker wrapper, no
pointing the build at a linker directly, no stub libraries, no installing into a
temp directory, no reaching past the project's own configuration. Those are not
faster than the answer below and they test something other than what the project
builds.

## Pinning

mise records whatever version string it is given, so `mise use node@22` pins
nothing. Resolve the concrete version first:

    mise latest <tool>
    mise use <tool>@<resolved version>

Do the same for anything mise pulls in as part of the setup. Only fall back to a
range when the tool publishes no resolvable concrete version.

Run `mise use` from the project root. It writes into the current directory with
no repo-root awareness, so from a subdirectory it silently creates a second
config that shadows the project's. In the root it updates an existing
`mise.toml` or `.mise.toml` in place, keeping the entries already there.

## C and C++

Stop and ask the user how the project should be built. Do not install a system
toolchain, pick a package manager, or reach for the zig wrappers on your own.
Report what the project needs and let the user choose.

## Rust

Install the toolchain with mise, pinned, as above. Then decide how it links.

Check whether the project or any dependency links against system libraries. If
there is no `Cargo.lock`, run `cargo generate-lockfile` first so dependencies are
actually visible:

    grep -nE '^name = "(pkg-config|cmake)"' Cargo.lock
    grep -nE '^name = "[a-z0-9_-]+-sys"' Cargo.lock
    grep -rn 'rustc-link-lib\|rustc-link-search' --include=build.rs .

No hits: use the zig wrappers, below.

Any hits: stop and ask the user, exactly as for C and C++. A `-sys` crate that
vendors and builds its own source needs no system library, but confirm that
rather than assuming it.

### zig wrappers

They provide `cc` and `c++` backed by `zig cc`. Those are the drivers rustc and
the `cc` crate already reach for, so putting them on PATH is enough and no cargo
linker configuration is needed.

Ad hoc, without touching the project:

    MISE_ENV=zigcc cargo build

Or opt the project in through its `mise.toml`:

    [env]
    _.path = ["{{env.ZIG_CC_BIN}}"]

The wrappers shell out to `zig`, so it has to be on PATH. Pin it in the project
rather than relying on a global install:

    mise use zig@<resolved version>

## Everything else

mise, pinned. If mise has no backend for what the project needs, say so and ask
rather than installing it another way.
