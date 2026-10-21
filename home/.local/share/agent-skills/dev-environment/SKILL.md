---
name: dev-environment
description: "Use when a project needs a toolchain to build, test or run: installing language runtimes, compilers, build tools or dependencies, setting up a tine box, or deciding how a project is built locally. Also use the moment a build, test or run command fails over a missing tool (linker `cc` not found, command not found, missing header or library), before working around it by hand."
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

Build, test and run inside a tine box. Never install a system toolchain or
libraries on the host, and never reach for the zig wrappers.

If the project root has no `BUCK` file, set one up:

1. Run `tine init` in the project root.
2. Write a `BUCK` file in the project root that declares a box with the
   packages the project needs, and a `box` alias for it. `tine box` runs
   `//:box`. Package names are Arch Linux package names:

       """Arch Linux development environment for <project>."""

       load("@tine//box:defs.bzl", "box")

       box.new(
           name = "<project>.box",
           packages = [
               "base",
               "base-devel",
               "iptables",
               "meson",
               "openssl",
           ],
           release = "tine//catalog:arch.rolling.release",
       )

       command_alias(
           name = "box",
           exe = ":<project>.box",
       )

   Take the package list from the project's build files (`meson.build`,
   `CMakeLists.txt`, `configure.ac`) and its CI configuration.

Then run every build, test and run command through the box:

    tine box -- meson setup build
    tine box -- meson test -C build

A build directory that exists but has no `build.ninja` was copied from another
checkout. Run `tine box -- meson setup --reconfigure <dir>` before building in
it. That rewrites the paths and keeps every build output.

When a tool or library is missing, add its package to `packages` in `BUCK`.
Keep the `BUCK` file and the files `tine init` writes out of commits unless the
user asks otherwise.

## Rust

Install the toolchain with mise, pinned, as above. Then decide how it links.

Check whether the project or any dependency links against system libraries. If
there is no `Cargo.lock`, run `cargo generate-lockfile` first so dependencies are
actually visible:

    grep -nE '^name = "(pkg-config|cmake)"' Cargo.lock
    grep -nE '^name = "[a-z0-9_-]+-sys"' Cargo.lock
    grep -rn 'rustc-link-lib\|rustc-link-search' --include=build.rs .

No hits: use the zig wrappers, below.

Any hits: use a tine box, exactly as for C and C++. A `-sys` crate that vendors
and builds its own source needs no system library, but confirm that rather than
assuming it.

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
