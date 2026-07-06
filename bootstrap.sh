#!/bin/sh
# Installs the pinned mise, points the global config at this checkout, then hands
# the rest of the machine setup to `mise bootstrap`.
set -eu

mise_version=v2026.9.13
mise_sha256_x64=827c7997af2e0e5a418f266ead5df99ee4aae9b6fd435df8dbe79d90f335ff9a
mise_sha256_arm64=0a9b8c243e717df2706e6822855ac8cfd5089745400565a815a93803cfcd4042

script_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
export PATH="$HOME/.local/bin:$PATH"

case $(uname -m) in
    x86_64) mise_arch=x64; mise_sha256=$mise_sha256_x64 ;;
    aarch64 | arm64) mise_arch=arm64; mise_sha256=$mise_sha256_arm64 ;;
    *) printf '%s\n' "unsupported architecture: $(uname -m)" >&2; exit 1 ;;
esac

if ! mise --version 2>/dev/null | grep -qF "${mise_version#v}"; then
    tmp_dir=$(mktemp -d /var/tmp/mise-bootstrap.XXXXXX)
    trap 'rm -rf -- "$tmp_dir"' EXIT

    curl --fail --location --retry 3 --connect-timeout 15 --max-time 120 \
        --output "$tmp_dir/mise.tar.gz" \
        "https://github.com/jdx/mise/releases/download/$mise_version/mise-$mise_version-linux-$mise_arch.tar.gz"
    printf '%s  %s\n' "$mise_sha256" "$tmp_dir/mise.tar.gz" | sha256sum --check

    tar -xzf "$tmp_dir/mise.tar.gz" -C "$tmp_dir" mise/bin/mise
    # Stage in the target directory so the replace is a rename: the file being
    # overwritten may be the mise that is running.
    install -Dm755 "$tmp_dir/mise/bin/mise" "$HOME/.local/bin/mise.new"
    mv -f "$HOME/.local/bin/mise.new" "$HOME/.local/bin/mise"

    rm -rf -- "$tmp_dir"
    trap - EXIT
fi

# mise reads `[dotfiles]` from its global config, so that link has to exist
# before it can apply anything, including the link to itself.
mkdir -p "$HOME/.config/mise"
ln -sfn "$script_dir/home/.config/mise/config.toml" "$HOME/.config/mise/config.toml"

exec mise bootstrap "$@"
