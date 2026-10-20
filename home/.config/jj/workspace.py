#!/usr/bin/python3 -SIB
"""Create and remove jj workspaces as btrfs snapshots of the default workspace."""

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import NoReturn

DEFAULT = "default"
# btrfs gives the root directory of every subvolume inode number 256.
SUBVOLUME_ROOT_INODE = 256
METADATA = (".jj", ".git")
# Zed saves the files of a worktree in a commit under this namespace before it removes the
# worktree. Restoring the archived thread writes the files from that commit into a new worktree.
ZED_ARCHIVE_REFS = "refs/archived-worktrees/"
WORKSPACE_ROOTS = 'self.name() ++ "\\t" ++ self.root() ++ "\\n"'
# Zed doesn't save the description of a commit, so a described commit is never abandoned.
UNDESCRIBED_CHANGES = 'if(!empty && !description, commit_id)'


def run(*args: str | Path, cwd: Path | None = None) -> str:
    return subprocess.run(
        [str(arg) for arg in args], check=True, stdout=subprocess.PIPE, text=True, cwd=cwd
    ).stdout


def fail(message: str) -> NoReturn:
    sys.exit(f"jj-workspace: {message}")


def warn(message: str) -> None:
    print(f"jj-workspace: {message}", file=sys.stderr)


def is_subvolume(path: Path) -> bool:
    filesystem = run("stat", "--file-system", "--format=%T", path).strip()
    return filesystem == "btrfs" and path.stat().st_ino == SUBVOLUME_ROOT_INODE


def workspace_root(name: str | None = None, repository: Path | None = None) -> Path:
    command = ["jj", *(["-R", repository] if repository else []), "workspace", "root"]
    return Path(run(*command, *(["--name", name] if name else [])).strip()).resolve()


def workspace_name(path: Path) -> str:
    for line in run("jj", "-R", path, "workspace", "list", "-T", WORKSPACE_ROOTS).splitlines():
        name, _, root = line.partition("\t")
        if root and Path(root).resolve() == path:
            return name
    fail(f"{path} is not the root of a jj workspace")


def worktrees_directory(root: Path) -> Path:
    return root.parent / "worktrees"


def delete_subvolume(path: Path) -> None:
    # An unprivileged user can only run `btrfs subvolume delete` on a filesystem mounted with
    # user_subvol_rm_allowed. On other mounts, the command fails with EPERM. shutil.rmtree()
    # still works on those mounts, because rmdir() can remove an empty subvolume.
    result = subprocess.run(
        ["btrfs", "subvolume", "delete", str(path)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if result.returncode != 0:
        shutil.rmtree(path)


def archived_by_zed(root: Path, commit: str) -> bool:
    if not (root / ".git").is_dir():
        return False
    tree = run("git", "-C", root, "rev-parse", f"{commit}^{{tree}}").strip()
    archived = run("git", "-C", root, "for-each-ref", "--format=%(tree)", ZED_ARCHIVE_REFS)
    return tree in archived.split()


def remove_entry(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    elif path.exists() or path.is_symlink():
        path.unlink()


def graft(root: Path, dest: Path, metadata: Path) -> None:
    """`jj workspace add` and `git worktree add` refuse a directory that is not empty.

    The workspace is added first, and its .jj and .git are moved into the snapshot afterwards.
    """
    for entry in METADATA:
        if (dest / entry).exists():
            shutil.move(dest / entry, metadata / entry)
    shutil.rmtree(dest)
    run("btrfs", "subvolume", "snapshot", root, dest)
    for entry in METADATA:
        remove_entry(dest / entry)
        if (metadata / entry).exists():
            # The snapshot is a separate btrfs subvolume. rename() fails with EXDEV across
            # subvolumes.
            shutil.move(metadata / entry, dest / entry)


def meson_source_dir(build_ninja: Path) -> str | None:
    with build_ninja.open() as f:
        for line in f:
            words = line.split()
            if words[:1] == ["command"] and "regenerate" in words:
                index = words.index("regenerate")
                if words[index - 1] == "--internal" and index + 1 < len(words):
                    return words[index + 1]
    return None


def relocate_meson_build_dirs(root: Path, dest: Path) -> None:
    """meson writes absolute paths into a build directory.

    Without a reconfigure, ninja in `dest` runs the code generators from `root`. `meson test` in
    `dest` runs the test binaries from `root`.
    """
    for build_ninja in sorted([*dest.glob("*/build.ninja"), *dest.glob("*/*/build.ninja")]):
        build_dir = build_ninja.parent
        if not (build_dir / "meson-private" / "coredata.dat").exists():
            continue
        if meson_source_dir(build_ninja) != str(root):
            continue
        relative = build_dir.relative_to(dest)
        command = ["meson", "setup", "--reconfigure", str(relative)]
        if (dest / "BUCK").exists() and shutil.which("tine"):
            command = ["tine", "box", "--", *command]
        print(f"jj-workspace: reconfiguring {relative}", file=sys.stderr)
        if shutil.which(command[0]):
            result = subprocess.run(
                command, cwd=dest, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
            )
            if result.returncode == 0:
                continue
            print(result.stdout, end="", file=sys.stderr)
        build_ninja.unlink()
        warn(
            f"could not reconfigure {relative}, so its build.ninja was removed to keep ninja from "
            f"building with the paths of {root}. Run `meson setup --reconfigure {relative}` in {dest}."
        )


def add(args: argparse.Namespace) -> None:
    root = workspace_root(DEFAULT)
    if not is_subvolume(root):
        fail(
            f"{root} is not a btrfs subvolume. Convert it once, with no build or editor using it:\n\n"
            f"    btrfs subvolume create {root}.new\n"
            f"    cp -a --reflink=always {root}/. {root}.new/\n"
            f"    mv {root} {root}.old && mv {root}.new {root}\n"
            f"    rm -rf {root}.old"
        )
    dest = args.path.absolute()
    if dest.exists() or dest.is_symlink():
        fail(f"{dest} already exists")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest = dest.resolve()
    # Zed creates worktrees at ../worktrees/<project>/<name>/<project>, where the last path component
    # is always the project name. jj workspace names must be unique, so the workspace is named after
    # the <name> component.
    name = dest.parent.name if dest.name == root.name else dest.name

    revisions = [word for revision in args.revision for word in ("--revision", revision)]
    colocate = ["--colocate"] if (root / ".git").is_dir() else []
    run("jj", "-R", root, "workspace", "add", "--name", name, *colocate, *revisions, dest)
    metadata = Path(tempfile.mkdtemp(dir=dest.parent, prefix=f".{dest.name}."))
    try:
        graft(root, dest, metadata)
        # The snapshot contains the uncommitted changes of the default workspace. `jj restore`
        # removes them, so the new working-copy commit starts out empty.
        run("jj", "-R", dest, "restore")
    except BaseException:
        if dest.exists() and is_subvolume(dest):
            delete_subvolume(dest)
        elif dest.exists():
            shutil.rmtree(dest)
        run("jj", "-R", root, "workspace", "forget", name)
        if colocate:
            # `jj workspace forget` only unregisters the Git worktree while the .git file of the
            # workspace exists. The workspace directory is gone at this point.
            run("git", "-C", root, "worktree", "prune")
        raise
    finally:
        shutil.rmtree(metadata, ignore_errors=True)

    relocate_meson_build_dirs(root, dest)
    print(dest)


def remove(args: argparse.Namespace) -> None:
    path = args.path.resolve()
    name = workspace_name(path)
    if name == DEFAULT:
        fail("refusing to remove the default workspace")
    if Path.cwd().resolve().is_relative_to(path):
        fail("cannot remove the current workspace; run this from another workspace")
    if not is_subvolume(path):
        fail(f"{path} is not a btrfs subvolume; remove it with `jj workspace remove {name}`")
    root = workspace_root(DEFAULT, path)

    # `jj util snapshot` records uncommitted changes in the working-copy commit. The changes stay
    # in the repository after the files are deleted.
    run("jj", "-R", path, "util", "snapshot")
    commit = run("jj", "-R", path, "log", "--no-graph", "-r", "@", "-T", UNDESCRIBED_CHANGES).strip()
    # `jj workspace forget` only unregisters the Git worktree while the .git file of the workspace
    # exists, so it has to run before the subvolume is deleted.
    run("jj", "-R", root, "workspace", "forget", name)
    delete_subvolume(path)
    # A Zed archive commit with the same tree contains every change in the working-copy commit.
    # Restoring the thread recreates the changes in a new working-copy commit. Keeping the old
    # commit as well would leave a second copy of the changes in `jj log`.
    if commit and archived_by_zed(root, commit):
        run("jj", "-R", root, "abandon", commit)

    if path.is_relative_to(worktrees_directory(root)):
        parent = path.parent
        while parent.is_relative_to(worktrees_directory(root)):
            try:
                parent.rmdir()
            except OSError:
                break
            parent = parent.parent


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    add_parser = commands.add_parser("add", help="create a workspace from a snapshot of the default workspace")
    add_parser.add_argument("path", type=Path)
    add_parser.add_argument("revision", nargs="*", help="parents of the new working-copy commit")
    add_parser.set_defaults(func=add)
    remove_parser = commands.add_parser("remove", help="delete a snapshot workspace and forget it")
    remove_parser.add_argument("path", type=Path)
    remove_parser.set_defaults(func=remove)
    args = parser.parse_args()
    try:
        args.func(args)
    except subprocess.CalledProcessError as error:
        sys.exit(error.returncode)


if __name__ == "__main__":
    main()
