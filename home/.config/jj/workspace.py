#!/usr/bin/python3 -SIB
"""Keep jj checkouts on btrfs subvolumes and manage workspaces that are snapshots of them."""

import argparse
import ctypes
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import NoReturn

DEFAULT = "default"
# btrfs gives the root directory of every subvolume inode number 256.
SUBVOLUME_ROOT_INODE = 256
BTRFS_SUPER_MAGIC = 0x9123683E
BTRFS_IOC_SNAP_CREATE_V2 = 0x50009417
BTRFS_IOC_SUBVOL_CREATE_V2 = 0x50009418
BTRFS_IOC_SNAP_DESTROY_V2 = 0x5000943F
BTRFS_SUBVOL_NAME_MAX = 4039
METADATA = (".jj", ".git")
# Zed's worktree picker offers to force delete a worktree when the error of the remove command
# contains the error that `git worktree remove` prints for a worktree with changes.
DIRTY_WORKTREE = "contains modified or untracked files, use --force to delete it"
# `jj util snapshot` only warns about new files that are larger than snapshot.max-new-file-size or
# that snapshot.auto-track doesn't match, and leaves them out of the snapshot. ws-remove deletes
# every file of the workspace afterwards, so its snapshot has to include those files too.
# `jj util snapshot` also refuses to run in a stale workspace unless snapshot.auto-update-stale is
# set.
SNAPSHOT_EVERY_FILE = (
    "--config",
    "snapshot.max-new-file-size=1TiB",
    "--config",
    'snapshot.auto-track="all()"',
    "--config",
    "snapshot.auto-update-stale=true",
)


def run(*args: str | Path) -> str:
    return subprocess.run([str(arg) for arg in args], check=True, stdout=subprocess.PIPE, text=True).stdout


def fail(message: str) -> NoReturn:
    sys.exit(f"jj-workspace: {message}")


def warn(message: str) -> None:
    print(f"jj-workspace: {message}", file=sys.stderr)


class btrfs_ioctl_vol_args_v2(ctypes.Structure):
    _fields_ = [
        ("fd", ctypes.c_int64),
        ("transid", ctypes.c_uint64),
        ("flags", ctypes.c_uint64),
        ("unused", ctypes.c_uint64 * 4),
        ("name", ctypes.c_char * (BTRFS_SUBVOL_NAME_MAX + 1)),
    ]


libc = ctypes.CDLL(None, use_errno=True)
libc.statfs.argtypes = (ctypes.c_char_p, ctypes.c_void_p)
libc.ioctl.argtypes = (ctypes.c_int, ctypes.c_ulong, ctypes.c_void_p)


def is_btrfs(path: Path) -> bool:
    # struct statfs is 120 bytes on x86-64, which is 15 longs. f_type is its first field and has
    # type long, so an array of 15 longs stands in for the struct.
    buffer = (ctypes.c_long * 15)()
    if libc.statfs(os.fsencode(path), ctypes.byref(buffer)) < 0:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code), str(path))
    return buffer[0] == BTRFS_SUPER_MAGIC


def is_subvolume(path: Path) -> bool:
    return is_btrfs(path) and path.stat().st_ino == SUBVOLUME_ROOT_INODE


def subvolume_ioctl(path: Path, request: int, source: int = 0) -> None:
    # The btrfs subvolume ioctls don't take a path. They take a file descriptor of the parent
    # directory and the name of the subvolume inside that directory.
    name = os.fsencode(path.name)
    if not name or len(name) > BTRFS_SUBVOL_NAME_MAX:
        raise ValueError(f"{path} isn't a valid subvolume path")
    parent = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        args = btrfs_ioctl_vol_args_v2(fd=source, name=name)
        if libc.ioctl(parent, request, ctypes.byref(args)) < 0:
            code = ctypes.get_errno()
            raise OSError(code, os.strerror(code), str(path))
    finally:
        os.close(parent)


def create_subvolume(path: Path) -> None:
    subvolume_ioctl(path, BTRFS_IOC_SUBVOL_CREATE_V2)


def snapshot_subvolume(source: Path, dest: Path) -> None:
    fd = os.open(source, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        subvolume_ioctl(dest, BTRFS_IOC_SNAP_CREATE_V2, fd)
    finally:
        os.close(fd)


def workspace_root(name: str | None = None, repository: Path | None = None) -> Path | None:
    result = subprocess.run(
        [
            "jj",
            "--ignore-working-copy",
            *(["-R", str(repository)] if repository else []),
            "workspace",
            "root",
            *(["--name", name] if name else []),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    return Path(result.stdout.strip()).resolve() if result.returncode == 0 else None


def workspace_name(path: Path) -> str:
    workspaces = run("jj", "--ignore-working-copy", "-R", path, "workspace", "list", "-T", 'name ++ "\\t" ++ root ++ "\\n"')
    for line in workspaces.splitlines():
        name, _, root = line.partition("\t")
        if root and Path(root).resolve() == path:
            return name
    fail(f"{path} is not the root of a jj workspace")


def mounts_under(path: Path) -> list[Path]:
    mounts = []
    for line in Path("/proc/self/mountinfo").read_text().splitlines():
        # The mount point is the fifth field. The kernel escapes spaces and other special characters
        # in it as octal sequences like \040.
        point = Path(re.sub(r"\\([0-7]{3})", lambda m: chr(int(m[1], 8)), line.split()[4]))
        if point != path and point.is_relative_to(path):
            mounts.append(point)
    return mounts


def refuse_mounts(path: Path) -> None:
    # shutil.rmtree() descends into mounts. Deleting a directory with a mount inside it deletes the
    # files of the mounted filesystem too.
    mounts = mounts_under(path)
    if mounts:
        fail(f"{path} contains mounts, unmount them first: {', '.join(map(str, mounts))}")


def delete_subvolume(path: Path) -> None:
    # An unprivileged user can only delete a subvolume on a filesystem mounted with
    # user_subvol_rm_allowed. On other mounts, the ioctl fails with EPERM. shutil.rmtree() still
    # works on those mounts, because rmdir() can remove an empty subvolume. The ioctl also fails
    # with EINVAL on a plain directory. ws-add passes a plain directory when it fails before it
    # takes the snapshot.
    try:
        subvolume_ioctl(path, BTRFS_IOC_SNAP_DESTROY_V2)
    except OSError:
        shutil.rmtree(path)


def graft(root: Path, dest: Path, metadata: Path) -> None:
    """`jj workspace add` and `git worktree add` refuse a directory that is not empty.

    The workspace is added first, and its .jj and .git are moved into the snapshot afterwards.
    """
    for entry in METADATA:
        if (dest / entry).exists():
            shutil.move(dest / entry, metadata / entry)
    shutil.rmtree(dest)
    snapshot_subvolume(root, dest)
    for entry in METADATA:
        if (dest / entry).exists():
            shutil.rmtree(dest / entry)
        if (metadata / entry).exists():
            # The snapshot is a separate btrfs subvolume. rename() fails with EXDEV across
            # subvolumes.
            shutil.move(metadata / entry, dest / entry)


def detach_meson_build_dirs(dest: Path) -> None:
    # meson writes absolute paths into build.ninja. With the build.ninja of the default workspace,
    # ninja in `dest` runs the code generators from `root` and writes outputs into the build
    # directory of `root`. A reconfigure takes about ten seconds per build directory in a tine
    # box. The agent that uses a build directory reconfigures it before its first build.
    for build_ninja in sorted([*dest.glob("*/build.ninja"), *dest.glob("*/*/build.ninja")]):
        build_dir = build_ninja.parent
        if not (build_dir / "meson-private" / "coredata.dat").exists():
            continue
        build_ninja.unlink()
        relative = build_dir.relative_to(dest)
        warn(f"removed {relative}/build.ninja; run `meson setup --reconfigure {relative}` before building")


def add(args: argparse.Namespace) -> None:
    root = workspace_root(DEFAULT)
    # Zed runs its create and remove commands in every repository, including git repositories
    # without jj. Those get the git worktree that Zed creates without the commands.
    if root is None:
        run("git", "worktree", "add", "--detach", "--", args.path, args.revision)
        print(args.path.absolute())
        return
    dest = args.path.absolute()
    if dest.exists() or dest.is_symlink():
        fail(f"{dest} already exists")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest = dest.resolve()
    # Zed creates worktrees at ../worktrees/<project>/<name>/<project>, where the last path component
    # is always the project name and <name> can contain slashes. jj workspace names must be unique,
    # so the workspace is named after all of <name>.
    worktrees = root.parent / "worktrees" / root.name
    if dest.name == root.name and dest.parent.is_relative_to(worktrees) and dest.parent != worktrees:
        name = str(dest.parent.relative_to(worktrees))
    else:
        name = dest.parent.name if dest.name == root.name else dest.name

    colocate = ["--colocate"] if (root / ".git").is_dir() else []
    run("jj", "-R", root, "workspace", "add", "--name", name, *colocate, "--revision", args.revision, dest)
    if not is_subvolume(root):
        warn(f"{root} is not a btrfs subvolume, so the workspace starts without its build directories")
        print(dest)
        return
    metadata = Path(tempfile.mkdtemp(dir=dest.parent, prefix=f".{dest.name}."))
    try:
        graft(root, dest, metadata)
        # The snapshot contains the uncommitted changes of the default workspace. `jj restore`
        # removes them, so the new working-copy commit starts out empty. `jj restore` reads every
        # file of the snapshot to find them, which takes 0.2 seconds in systemd. When the default
        # workspace has no uncommitted changes, there is nothing to remove. The `jj log` call
        # snapshots the default workspace after the btrfs snapshot, so it records every change
        # that the btrfs snapshot contains.
        if run("jj", "-R", root, "log", "--no-graph", "-r", "@", "-T", "empty").strip() != "true":
            run("jj", "-R", dest, "restore")
    except BaseException:
        if dest.exists():
            delete_subvolume(dest)
        run("jj", "-R", root, "workspace", "forget", name)
        if colocate:
            # `jj workspace forget` only unregisters the Git worktree while the .git file of the
            # workspace exists. The workspace directory is gone at this point.
            run("git", "-C", root, "worktree", "prune")
        raise
    finally:
        shutil.rmtree(metadata, ignore_errors=True)

    detach_meson_build_dirs(dest)
    print(dest)


def remove(args: argparse.Namespace) -> None:
    path = args.path.resolve()
    if not (path / ".jj").is_dir():
        run("git", "worktree", "remove", *(["--force"] if args.force else []), "--", path)
        return
    name = workspace_name(path)
    if name == DEFAULT:
        fail("refusing to remove the default workspace")
    if Path.cwd().resolve().is_relative_to(path):
        fail("cannot remove the current workspace; run this from another workspace")
    refuse_mounts(path)
    root = workspace_root(DEFAULT, path)

    # `jj util snapshot` records uncommitted changes in the working-copy commit. The changes stay
    # in the repository after the files are deleted.
    run("jj", "-R", path, *SNAPSHOT_EVERY_FILE, "util", "snapshot")
    # A snapshot of a stale workspace with uncommitted changes records the changes in a second
    # commit of the working-copy change. The working-copy commit itself is empty afterwards.
    change = run("jj", "--ignore-working-copy", "-R", path, "log", "--no-graph", "-r", "@", "-T", "change_id")
    changed = run("jj", "--ignore-working-copy", "-R", path, "log", "--no-graph", "-r", f"change_id({change}) ~ empty()", "-T", "change_id")
    if not args.force and changed:
        fail(f"'{path}' {DIRTY_WORKTREE}")
    # `jj workspace forget` only unregisters the Git worktree while the .git file of the workspace
    # exists, so it has to run before the subvolume is deleted.
    run("jj", "-R", root, "workspace", "forget", name)
    delete_subvolume(path)


def seed(args: argparse.Namespace) -> None:
    root = workspace_root(DEFAULT)
    if root is None or workspace_root() != root:
        fail("run `jj seed` from the default workspace")
    result = subprocess.run(
        ["jj", "config", "list", "seed.commands", "-T", "json(value)"],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    commands = json.loads(result.stdout or "null")
    if not commands:
        fail("seed.commands is not set for this repository")
    # `jj new` moves the default workspace onto trunk(). The changes of the old working-copy commit
    # stay in that commit and aren't part of the seed build.
    if run("jj", "log", "--no-graph", "-r", "@", "-T", 'if(empty && !description, "clean")') != "clean":
        fail("the working-copy commit of the default workspace has changes or a description; move them first")

    subprocess.run(["jj", "new", "trunk()"], check=True)
    for command in commands:
        print(f"jj-workspace: running {' '.join(command)}", file=sys.stderr)
        result = subprocess.run(command, cwd=root)
        if result.returncode != 0:
            sys.exit(result.returncode)


def clone_destination(source: str) -> str:
    # `jj git clone` names the clone after the last component of the source. The source can be a
    # path, a URL or an scp-like address such as `git@github.com:systemd/systemd.git`.
    name = source.rstrip("/").replace(":", "/").rpartition("/")[2]
    return name.removesuffix(".git")


def clone(args: argparse.Namespace) -> None:
    source, rest = args.source, args.rest
    if rest and not rest[0].startswith("-"):
        destination, *options = rest
    else:
        destination, options = clone_destination(source), rest
    dest = Path(destination).absolute()
    if dest.exists() or dest.is_symlink():
        fail(f"{dest} already exists")
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not is_btrfs(dest.parent):
        fail(f"{dest.parent} is not on a btrfs filesystem")

    create_subvolume(dest)
    result = subprocess.run(["jj", "git", "clone", *options, source, str(dest)])
    if result.returncode != 0:
        delete_subvolume(dest)
        sys.exit(result.returncode)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    add_parser = commands.add_parser("add", help="create a workspace from a snapshot of the default workspace")
    add_parser.add_argument("path", type=Path)
    add_parser.add_argument("revision", help="parent of the new working-copy commit")
    add_parser.set_defaults(func=add)
    remove_parser = commands.add_parser("remove", help="delete a snapshot workspace and forget it")
    remove_parser.add_argument("path", type=Path)
    remove_parser.add_argument(
        "--force", action="store_true", help="remove the workspace even if it has changes"
    )
    remove_parser.set_defaults(func=remove)
    seed_parser = commands.add_parser(
        "seed", help="move the default workspace onto trunk() and build it for new workspaces"
    )
    seed_parser.set_defaults(func=seed)
    clone_parser = commands.add_parser(
        "clone",
        help="clone a repository into a new btrfs subvolume",
        usage="jj clone <source> [<destination>] [<jj git clone option>...]",
    )
    clone_parser.add_argument("source")
    clone_parser.add_argument("rest", nargs=argparse.REMAINDER)
    clone_parser.set_defaults(func=clone)
    args = parser.parse_args()
    try:
        args.func(args)
    except subprocess.CalledProcessError as error:
        sys.exit(error.returncode)
    except OSError as error:
        fail(str(error))


if __name__ == "__main__":
    main()
