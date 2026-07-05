#!/bin/sh
set -eu

script_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)

export PATH="$HOME/.local/bin:$PATH"

if command -v chezmoi >/dev/null 2>&1; then
    exec chezmoi apply --source "$script_dir"
fi

version=$(sed -n 's/^version = "\(.*\)"$/\1/p' "$script_dir/dot_local/bin/executable_chezmoi")

tmp_dir=$(mktemp -d)
trap 'rm -rf "$tmp_dir"' EXIT

sh -c "$(curl -fsLS get.chezmoi.io)" -- -b "$tmp_dir" -t "v$version"

"$tmp_dir/chezmoi" apply --source "$script_dir"
