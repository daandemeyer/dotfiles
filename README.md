# dotfiles

The repository contains my configuration of bash, fish, git, jj, mise, Zed, VS
Code, Claude Code and Codex for Linux, and pins the versions of my command-line
tools. [mise](https://mise.jdx.dev) links the files into the home directory and
installs the tools.

## Set up a machine

The setup runs on x86-64 and arm64 Linux and needs `curl`, `tar` and
`sha256sum`. The mise configuration expects the checkout at
`~/Projects/dotfiles`.

```sh
git clone https://github.com/daandemeyer/dotfiles ~/Projects/dotfiles
~/Projects/dotfiles/bootstrap.sh
```

`bootstrap.sh` installs the pinned version of mise into `~/.local/bin`, links
`~/.config/mise/config.toml` to the checkout and runs `mise bootstrap`. mise
then creates the links in the home directory and renders the templates.

`bootstrap.sh` passes its arguments to `mise bootstrap`. On a fresh install,
files such as `~/.bashrc` already exist where the links go. The following
command overwrites those files with the links, so copy anything you want to
keep first:

```sh
~/Projects/dotfiles/bootstrap.sh --force-dotfiles
```

## Change a file

mise links most of the managed files to the checkout. For example,
`~/.config/jj/config.toml` is a symlink to `home/.config/jj/config.toml`. An
edit through either path changes the file in the checkout and takes effect
right away.

mise renders `~/.codex/config.toml` and the desktop entries in
`~/.local/share/applications` from the files in `templates/`. The rendered
files are copies. Edit the template, then render it again with
`mise dotfiles apply`.

Codex writes its own changes into `~/.codex/config.toml`, for example the
projects you trust. `mise dotfiles apply` overwrites them with the template.
Check the differences first and copy what you want to keep into
`templates/codex-config.toml.tera`:

```sh
mise dotfiles diff
mise dotfiles apply
```

## Add or remove a file

To add a file, run `mise dotfiles add` on it:

```sh
mise dotfiles add --global ~/.config/user-dirs.dirs
```

mise copies the file into `home/`, adds an entry to `[dotfiles]` in
`home/.config/mise/config.toml` and replaces the file with a link.

To remove a file, delete it from `home/` and change its entry in `[dotfiles]`
to `{ mode = "absent" }`. `mise dotfiles apply` then deletes the link on each
machine where it runs. If you delete the entry instead, mise keeps the old
link on machines that already have it.

## Update a tool

Most files in `home/.local/bin` are mise tool stubs. A stub pins the version
of one tool, and mise installs that version the first time the stub runs. The
other files in `home/.local/bin` are scripts, such as `bump` and `skill`.

`bump` updates the stubs of Zed, VS Code and Claude Code to their latest
release:

```sh
bump --zed
bump --code
bump --claude
```

To update any other tool, edit its stub. Change the version, and also the URLs
and checksums if the stub lists them.

## Change the agent skills

Claude Code and Codex use the same skills, from
`home/.local/share/agent-skills`. Edit a skill there. The `skill` command
creates, lists and removes skills. See
[the agent skills README](home/.local/share/agent-skills/README.md) for how the
links work.
