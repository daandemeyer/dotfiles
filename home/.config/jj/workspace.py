#!/usr/bin/python3 -SIB
"""Keep jj checkouts on btrfs subvolumes and manage workspaces that are snapshots of them."""

import argparse
import ctypes
import fcntl
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import IO, NoReturn

DEFAULT = "default"
PROGRESS_WIDTH = 30
# Zed runs the create command as a job of the repository's git job queue, and every later git job
# of the repository waits for it. ws-add doesn't wait for a whole `jj seed` build.
ADD_LOCK_TIMEOUT = 60
# btrfs gives the root directory of every subvolume inode number 256.
SUBVOLUME_ROOT_INODE = 256
BTRFS_SUPER_MAGIC = 0x9123683E
BTRFS_IOC_SNAP_CREATE_V2 = 0x50009417
BTRFS_IOC_SUBVOL_CREATE_V2 = 0x50009418
BTRFS_IOC_SNAP_DESTROY_V2 = 0x5000943F
BTRFS_SUBVOL_NAME_MAX = 4039
METADATA = (".jj", ".git")
# Zed saves the files of a worktree in a commit under this namespace before it removes the
# worktree. Restoring the archived thread writes the files from that commit into a new worktree.
ZED_ARCHIVE_REFS = "refs/archived-worktrees/"
# Zed's worktree picker offers to force delete a worktree when the error of the remove command
# contains the error that `git worktree remove` prints for a worktree with changes.
DIRTY_WORKTREE = "contains modified or untracked files, use --force to delete it"
# `jj util snapshot` only warns about new files that are larger than snapshot.max-new-file-size or
# that snapshot.auto-track doesn't match, and leaves them out of the snapshot. ws-remove deletes
# every file of the workspace afterwards, so its snapshot has to include those files too.
SNAPSHOT_EVERY_FILE = ("--config", "snapshot.max-new-file-size=1TiB", "--config", 'snapshot.auto-track="all()"')
WORKSPACE_ROOTS = 'self.name() ++ "\\t" ++ self.root() ++ "\\n"'
WORKING_COPY = 'commit_id ++ " " ++ empty ++ " " ++ if(description, "described", "undescribed")'


def run(*args: str | Path, cwd: Path | None = None) -> str:
    return subprocess.run(
        [str(arg) for arg in args], check=True, stdout=subprocess.PIPE, text=True, cwd=cwd
    ).stdout


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


def workspace_root(name: str | None = None, repository: Path | None = None) -> Path:
    command = ["jj", "--ignore-working-copy", *(["-R", repository] if repository else []), "workspace", "root"]
    return Path(run(*command, *(["--name", name] if name else [])).strip()).resolve()


def default_workspace_root() -> Path | None:
    result = subprocess.run(
        ["jj", "--ignore-working-copy", "workspace", "root", "--name", DEFAULT],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    return Path(result.stdout.strip()).resolve() if result.returncode == 0 else None


def workspace_name(path: Path) -> str:
    for line in run("jj", "--ignore-working-copy", "-R", path, "workspace", "list", "-T", WORKSPACE_ROOTS).splitlines():
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
    # `cp -a`, `rm -r` and shutil.rmtree() descend into mounts. Deleting a directory with a mount
    # inside it deletes the files of the mounted filesystem too.
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


def saved_by_zed(root: Path, commit: str) -> bool:
    if not (root / ".git").is_dir():
        return False
    tree = run("git", "-C", root, "rev-parse", f"{commit}^{{tree}}").strip()
    archives = run("git", "-C", root, "for-each-ref", "--format=%(tree)", ZED_ARCHIVE_REFS)
    return tree in archives.split()


def parent_pid(pid: int) -> int:
    # The command name in /proc/<pid>/stat can contain spaces and parentheses. The fields after
    # the last ")" are the state and then the parent PID.
    stat = Path(f"/proc/{pid}/stat").read_text()
    return int(stat.rpartition(")")[2].split()[1])


def ancestors(pid: int) -> list[int]:
    pids = []
    while pid > 1:
        try:
            pid = parent_pid(pid)
        except (OSError, ValueError):
            break
        pids.append(pid)
    return pids


def has_open_file_in(process: Path, path: Path) -> bool:
    try:
        fds = list((process / "fd").iterdir())
    except OSError:
        return False
    for fd in fds:
        try:
            if Path(os.readlink(fd)).is_relative_to(path):
                return True
        except OSError:
            continue
    return False


def processes_in(path: Path, open_files: bool = False) -> list[str]:
    ignored = {os.getpid(), *ancestors(os.getpid())}
    processes = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit() or int(entry.name) in ignored:
            continue
        try:
            cwd = Path(os.readlink(entry / "cwd"))
            comm = (entry / "comm").read_text().strip()
        except OSError:
            # Only root can read the working directory of another user's process. processes_in()
            # doesn't check the processes of other users.
            continue
        if not cwd.is_relative_to(path) and not (open_files and has_open_file_in(entry, path)):
            continue
        processes.append(f"{comm} ({entry.name})")
    return processes


def lock_seed(root: Path, operation: int, timeout: float | None = None) -> IO[str]:
    # `jj ws-add` copies the build directories of the default workspace into the new workspace.
    # While `jj seed` runs its build commands, the build directories of the default workspace
    # contain partially written outputs. A workspace that ws-add creates during the build would
    # contain the partially written outputs too.
    lock = (root / ".jj" / "seed.lock").open("a")
    deadline = None if timeout is None else time.monotonic() + timeout
    waiting = False
    while True:
        try:
            fcntl.flock(lock, operation | fcntl.LOCK_NB)
            return lock
        except BlockingIOError:
            if deadline is not None and time.monotonic() > deadline:
                fail(f"`jj seed` still builds in {root}; try again when it's done")
            if not waiting:
                warn("waiting for `jj seed` to finish")
                waiting = True
            time.sleep(0.2)


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
    snapshot_subvolume(root, dest)
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


def detach_meson_build_dirs(root: Path, dest: Path) -> None:
    # meson writes absolute paths into build.ninja. With the build.ninja of the default workspace,
    # ninja in `dest` runs the code generators from `root` and writes outputs into the build
    # directory of `root`. A reconfigure takes about ten seconds per build directory in a tine
    # box. The agent that uses a build directory reconfigures it before its first build.
    for build_ninja in sorted([*dest.glob("*/build.ninja"), *dest.glob("*/*/build.ninja")]):
        build_dir = build_ninja.parent
        if not (build_dir / "meson-private" / "coredata.dat").exists():
            continue
        if meson_source_dir(build_ninja) != str(root):
            continue
        build_ninja.unlink()
        relative = build_dir.relative_to(dest)
        warn(f"removed {relative}/build.ninja; run `meson setup --reconfigure {relative}` before building")


def current_revision(root: Path, revision: str) -> str:
    # Zed resolves the commit of a new worktree before ws-add takes the lock. When a jj command in
    # another workspace rewrites the commit in between, the commit is hidden. Checking out the
    # hidden commit makes it and its ancestors visible again next to their rewritten versions.
    hidden = run("jj", "--ignore-working-copy", "-R", root, "log", "--no-graph", "-r", revision, "-T", 'if(self.hidden(), change_id ++ "\\n")').split()
    if len(hidden) == 1 and len(resolve(f"change_id({hidden[0]})")) == 1:
        return f"change_id({hidden[0]})"
    return revision


def add(args: argparse.Namespace) -> None:
    root = default_workspace_root()
    # Zed runs its create and remove commands in every repository, including git repositories
    # without jj. Those get the git worktree that Zed creates without the commands.
    if root is None:
        if len(args.revision) != 1:
            fail("a git worktree needs exactly one revision")
        run("git", "worktree", "add", "--detach", "--", args.path, args.revision[0])
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

    revisions = [word for revision in args.revision for word in ("--revision", revision)]
    colocate = ["--colocate"] if (root / ".git").is_dir() else []
    if not is_subvolume(root):
        warn(
            f"{root} is not a btrfs subvolume, so the workspace starts without its build directories. "
            f"Run `jj convert {root}` to convert it."
        )
        run("jj", "-R", root, "workspace", "add", "--name", name, *colocate, *revisions, dest)
        print(dest)
        return
    lock = lock_seed(root, fcntl.LOCK_SH, ADD_LOCK_TIMEOUT)
    revisions = [word for revision in args.revision for word in ("--revision", current_revision(root, revision))]
    run("jj", "-R", root, "workspace", "add", "--name", name, *colocate, *revisions, dest)
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
    lock.close()

    detach_meson_build_dirs(root, dest)
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

    # A rebase in another workspace of the branch below the working-copy commit leaves the workspace
    # stale. `jj util snapshot` refuses to run in a stale workspace. `jj workspace update-stale`
    # first records the changes in the stale working-copy commit and then checks out its rewritten
    # version.
    run("jj", "-R", path, *SNAPSHOT_EVERY_FILE, "workspace", "update-stale")
    # `jj util snapshot` records uncommitted changes in the working-copy commit. The changes stay
    # in the repository after the files are deleted.
    run("jj", "-R", path, *SNAPSHOT_EVERY_FILE, "util", "snapshot")
    commit, empty, description = run(
        "jj", "--ignore-working-copy", "-R", path, "log", "--no-graph", "-r", "@", "-T", WORKING_COPY
    ).split()
    # Zed passes --force when it archives a thread and when the user confirms a force delete in the
    # worktree picker. Before Zed archives a thread, it checks that no other thread or terminal uses
    # the worktree, but the agent of the archived thread can still run in it.
    if not args.force:
        processes = processes_in(path)
        if processes:
            fail(f"{path} is the working directory of {', '.join(processes)}")
        if empty == "false":
            fail(f"'{path}' {DIRTY_WORKTREE}")
    # `jj workspace forget` only unregisters the Git worktree while the .git file of the workspace
    # exists, so it has to run before the subvolume is deleted.
    run("jj", "-R", root, "workspace", "forget", name)
    delete_subvolume(path)
    # A Zed archive commit with the same tree contains every change in the working-copy commit.
    # Restoring the thread recreates the changes in a new working-copy commit. Keeping the old
    # commit as well would leave a second copy of the changes in `jj log`. Zed doesn't save the
    # description of a commit, so a described commit is never abandoned.
    if empty == "false" and description == "undescribed" and saved_by_zed(root, commit):
        run("jj", "-R", root, "abandon", commit)


def seed(args: argparse.Namespace) -> None:
    root = workspace_root(DEFAULT)
    if workspace_root() != root:
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
    if not isinstance(commands, list) or not all(
        isinstance(command, list) and command and all(isinstance(word, str) for word in command)
        for command in commands
    ):
        fail("seed.commands must be a list of commands, each a list of strings")
    # `jj new` moves the default workspace onto trunk(). The changes of the old working-copy commit
    # stay in that commit and aren't part of the seed build.
    if run("jj", "log", "--no-graph", "-r", "@", "-T", 'if(empty && !description, "clean")') != "clean":
        fail("the working-copy commit of the default workspace has changes or a description; move them first")

    with lock_seed(root, fcntl.LOCK_EX):
        subprocess.run(["jj", "new", "trunk()"], check=True)
        for command in commands:
            print(f"jj-workspace: running {' '.join(command)}", file=sys.stderr)
            result = subprocess.run(command, cwd=root)
            if result.returncode != 0:
                sys.exit(result.returncode)


def resolve(revset: str, template: str = "commit_id") -> list[str]:
    return run("jj", "--ignore-working-copy", "log", "--no-graph", "-r", revset, "-T", f'{template} ++ "\\n"').split()


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


def count_entries(path: Path) -> int:
    count = 0
    directories = [path]
    while directories:
        with os.scandir(directories.pop()) as entries:
            for entry in entries:
                count += 1
                if entry.is_dir(follow_symlinks=False):
                    directories.append(Path(entry.path))
    return count


def show_progress(label: str, done: int, total: int) -> None:
    done = min(done, total)
    fraction = done / total if total else 1
    filled = int(fraction * PROGRESS_WIDTH)
    bar = "#" * filled + "-" * (PROGRESS_WIDTH - filled)
    print(
        f"\r\x1b[K{label} [{bar}] {fraction:4.0%} {done:,}/{total:,}", end="", file=sys.stderr, flush=True
    )


def run_with_progress(command: list[str], label: str, total: int) -> None:
    # `cp -v` and `rm -v` print one line for every file and directory. The number of lines so far
    # is the number of entries that the command has processed.
    terminal = sys.stderr.isatty()
    if not terminal:
        print(f"jj-workspace: {label} {total:,} entries", file=sys.stderr)
    process = subprocess.Popen(command, stdout=subprocess.PIPE)
    assert process.stdout is not None
    done = 0
    shown = 0.0
    for _ in process.stdout:
        done += 1
        if terminal and time.monotonic() - shown >= 0.1:
            show_progress(label, done, total)
            shown = time.monotonic()
    if process.wait() != 0:
        if terminal:
            print(file=sys.stderr)
        raise subprocess.CalledProcessError(process.returncode, command)
    if terminal:
        show_progress(label, total, total)
        print(file=sys.stderr)


def convert(args: argparse.Namespace) -> None:
    root = args.path.resolve()
    if not root.is_dir():
        fail(f"{root} is not a directory")
    if is_subvolume(root):
        print(f"jj-workspace: {root} is already a btrfs subvolume", file=sys.stderr)
        return
    if not is_btrfs(root):
        fail(f"{root} is not on a btrfs filesystem")
    refuse_mounts(root)
    # A build or an editor that writes into the checkout during the copy writes into the old
    # directory. The old directory is deleted after the copy, and the writes are lost.
    processes = processes_in(root, open_files=True)
    if processes:
        fail(f"{root} is in use by {', '.join(processes)}")

    new = root.with_name(f"{root.name}.new")
    old = root.with_name(f"{root.name}.old")
    for path in (new, old):
        if path.exists() or path.is_symlink():
            fail(f"{path} already exists")
    print(f"jj-workspace: counting the files in {root}", file=sys.stderr)
    total = count_entries(root)
    create_subvolume(new)
    marker = Path(tempfile.mkstemp(dir=root.parent, prefix=f".{root.name}.convert.")[1])
    try:
        # `cp -a` keeps the modification times. Without them, the next build in the subvolume
        # rebuilds every target.
        run_with_progress(["cp", "-av", "--reflink=always", f"{root}/.", f"{new}/"], "copying", total)
        # The process check above only sees processes that run in the checkout. jj commands in other
        # workspaces of the repository and git commands in its worktrees write into .jj and .git
        # from outside. Their writes during the copy would be lost with the old directory.
        changed = run("find", root, "-xdev", "(", "-newer", marker, "-o", "-cnewer", marker, ")", "-print", "-quit")
        if changed:
            fail(f"{changed.strip()} changed during the copy; run `jj convert` again when nothing writes to {root}")
        root.rename(old)
        try:
            new.rename(root)
        except BaseException:
            old.rename(root)
            raise
    except BaseException:
        delete_subvolume(new)
        raise
    finally:
        marker.unlink()
    run_with_progress(["rm", "-rfv", str(old)], "deleting the old directory", total)
    print(f"jj-workspace: converted {root} to a btrfs subvolume", file=sys.stderr)
    if Path(os.environ.get("PWD", "/")).is_relative_to(root):
        # The shell that runs `jj convert` still has the deleted directory as its working directory.
        print("jj-workspace: run `cd $PWD` to enter the subvolume", file=sys.stderr)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    add_parser = commands.add_parser("add", help="create a workspace from a snapshot of the default workspace")
    add_parser.add_argument("path", type=Path)
    add_parser.add_argument("revision", nargs="*", help="parents of the new working-copy commit")
    add_parser.set_defaults(func=add)
    remove_parser = commands.add_parser("remove", help="delete a snapshot workspace and forget it")
    remove_parser.add_argument("path", type=Path)
    remove_parser.add_argument(
        "--force", action="store_true", help="remove the workspace even if it has changes or is in use"
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
    convert_parser = commands.add_parser("convert", help="convert a directory to a btrfs subvolume")
    convert_parser.add_argument("path", type=Path)
    convert_parser.set_defaults(func=convert)
    args = parser.parse_args()
    try:
        args.func(args)
    except subprocess.CalledProcessError as error:
        sys.exit(error.returncode)
    except OSError as error:
        fail(str(error))


if __name__ == "__main__":
    main()
