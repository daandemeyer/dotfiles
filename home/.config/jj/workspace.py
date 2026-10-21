#!/usr/bin/python3 -SIB
"""Clone jj repositories into btrfs subvolumes and create workspaces as snapshots of the default workspace."""

import argparse
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
# New workspaces start on the megamerge, so the build directories of the default workspace are
# built from the megamerge. A repository without a megamerge builds trunk().
PROGRESS_WIDTH = 30
CLONE_USAGE = "jj clone <source> [<destination>] [<jj git clone option>...]"
SEED_REVSET = "coalesce(megamerge(), trunk())"
SYNC_STATE = "sync.json"
# `jj duplicate` prints one line for every commit: the original commit ID, then the change ID and
# the commit ID of the copy.
DUPLICATED = re.compile(r"^Duplicated ([0-9a-f]+) as ([k-z]+) ([0-9a-f]+)")
COMMIT_LINE = '"  " ++ change_id.short() ++ " " ++ description.first_line() ++ "\\n"'
# btrfs gives the root directory of every subvolume inode number 256.
SUBVOLUME_ROOT_INODE = 256
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


def is_subvolume(path: Path) -> bool:
    filesystem = run("stat", "--file-system", "--format=%T", path).strip()
    return filesystem == "btrfs" and path.stat().st_ino == SUBVOLUME_ROOT_INODE


def workspace_root(name: str | None = None, repository: Path | None = None) -> Path:
    command = ["jj", "--ignore-working-copy", *(["-R", repository] if repository else []), "workspace", "root"]
    return Path(run(*command, *(["--name", name] if name else [])).strip()).resolve()


def workspace_name(path: Path) -> str:
    for line in run("jj", "--ignore-working-copy", "-R", path, "workspace", "list", "-T", WORKSPACE_ROOTS).splitlines():
        name, _, root = line.partition("\t")
        if root and Path(root).resolve() == path:
            return name
    fail(f"{path} is not the root of a jj workspace")


def worktrees_directory(root: Path) -> Path:
    return root.parent / "worktrees"


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
            continue
        if not cwd.is_relative_to(path) and not (open_files and has_open_file_in(entry, path)):
            continue
        processes.append(f"{comm} ({entry.name})")
    return processes


def lock_megamerge(root: Path, operation: int) -> IO[str]:
    # `jj ws-add` copies the build directories of the default workspace into the new workspace.
    # While `jj seed` runs its build commands, the build directories of the default workspace
    # contain partially written outputs. While `jj land` runs, the megamerge and the default
    # workspace move to the synced stack in several jj operations.
    lock = (root / ".jj" / "megamerge.lock").open("a")
    try:
        fcntl.flock(lock, operation | fcntl.LOCK_NB)
    except BlockingIOError:
        warn("waiting for `jj seed` or `jj land` to finish")
        fcntl.flock(lock, operation)
    return lock


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
        fail(f"{root} is not a btrfs subvolume. Run `jj convert {root}` to convert it.")
    lock = lock_megamerge(root, fcntl.LOCK_SH)
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
    lock.close()

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
    refuse_mounts(path)
    root = workspace_root(DEFAULT, path)

    # A rewrite in another workspace, like `jj land`, leaves the workspace stale, and `jj util snapshot`
    # refuses to run in a stale workspace. `jj workspace update-stale` first records the changes in
    # the stale working-copy commit and then checks out its rewritten version.
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

    if path.is_relative_to(worktrees_directory(root)):
        parent = path.parent
        while parent.is_relative_to(worktrees_directory(root)):
            try:
                parent.rmdir()
            except OSError:
                break
            parent = parent.parent


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
    # `jj new` moves the default workspace onto the megamerge. The changes of the old working-copy
    # commit stay in that commit and aren't part of the seed build.
    if run("jj", "log", "--no-graph", "-r", "@", "-T", 'if(empty && !description, "clean")') != "clean":
        fail("the working-copy commit of the default workspace isn't empty; move its changes first")

    conflicts = run("jj", "log", "--no-graph", "-r", f"conflicts() & ::({SEED_REVSET})", "-T", COMMIT_LINE)
    if conflicts:
        fail(f"the megamerge has conflicts:\n{conflicts}")

    with lock_megamerge(root, fcntl.LOCK_EX):
        subprocess.run(["jj", "new", SEED_REVSET], check=True)
        for command in commands:
            print(f"jj-workspace: running {' '.join(command)}", file=sys.stderr)
            result = subprocess.run(command, cwd=root)
            if result.returncode != 0:
                sys.exit(result.returncode)


def resolve(revset: str, template: str = "commit_id") -> list[str]:
    return run("jj", "log", "--no-graph", "-r", revset, "-T", f'{template} ++ "\\n"').split()


def resolve_by_change(revset: str, template: str) -> dict[str, str]:
    lines = run("jj", "log", "--no-graph", "-r", revset, "-T", f'change_id ++ " " ++ {template} ++ "\\n"')
    return dict(line.split(" ", 1) for line in lines.splitlines())


def sync_state_path() -> Path:
    root = workspace_root()
    if root == workspace_root(DEFAULT):
        fail("run `jj sync` and `jj land` from a workspace other than the default one")
    return root / ".jj" / SYNC_STATE


def abort_sync(path: Path) -> None:
    if not path.exists():
        fail("no `jj sync` is in progress in this workspace")
    state = json.loads(path.read_text())
    copies = [copy for copy in state["copies"].values() if resolve(f"present({copy})")]
    if copies:
        run("jj", "abandon", *copies)
    path.unlink()
    subprocess.run(["jj", "new", "megamerge()"], check=True)
    print("jj-workspace: abandoned the synced copies of the stack", file=sys.stderr)


def sync(args: argparse.Namespace) -> None:
    path = sync_state_path()
    if args.abort:
        abort_sync(path)
        return
    if path.exists():
        fail("a `jj sync` is already in progress in this workspace; run `jj land` or `jj sync --abort`")

    subprocess.run(["jj", "git", "fetch"], check=True)
    if not resolve("parents(roots(trunk()..megamerge())) ~ trunk()"):
        print("jj-workspace: the megamerge is already on trunk()", file=sys.stderr)
        return
    trunk = resolve("trunk()")[0]
    originals = resolve_by_change("trunk()..megamerge()", "commit_id")
    megamerge = resolve("megamerge()", "change_id")[0]

    # The copies get new change IDs. The other workspaces keep working on the original stack until
    # `jj land` moves their commits onto the copies.
    result = subprocess.run(
        ["jj", "duplicate", *originals.values(), "--onto", trunk],
        check=True,
        stderr=subprocess.PIPE,
        text=True,
    )
    by_commit = {commit: change for change, commit in originals.items()}
    copies = {}
    for line in result.stderr.splitlines():
        match = DUPLICATED.match(line)
        if not match:
            continue
        original = next(change for commit, change in by_commit.items() if commit.startswith(match[1]))
        copies[original] = resolve(match[3], "change_id")[0]
    if sorted(copies) != sorted(originals):
        fail(f"could not map every commit to its copy in the output of `jj duplicate`:\n{result.stderr}")

    # `jj restack` drops commits that became empty because trunk() already contains them. A merge
    # commit like the megamerge is empty on its own, so a copy only counts as emptied when its
    # original wasn't empty.
    original_empty = resolve_by_change(" | ".join(originals.values()), "empty")
    copy_empty = resolve_by_change(" | ".join(copies.values()), "empty")
    emptied = [
        copy
        for original, copy in copies.items()
        if original_empty[original] == "false" and copy_empty[copy] == "true"
    ]
    if emptied:
        run("jj", "abandon", *emptied)
        copies = {original: copy for original, copy in copies.items() if copy not in emptied}
    run("jj", "simplify-parents", "-r", " | ".join(copies.values()))
    subprocess.run(["jj", "new", copies[megamerge]], check=True)

    path.write_text(
        json.dumps({"trunk": trunk, "megamerge": megamerge, "originals": originals, "copies": copies})
    )
    conflicts = run("jj", "log", "--no-graph", "-r", f"conflicts() & ({' | '.join(copies.values())})", "-T", COMMIT_LINE)
    if conflicts:
        print(f"jj-workspace: resolve the conflicts in the copies, then run `jj land`:\n{conflicts}", file=sys.stderr)
    else:
        print("jj-workspace: the copies have no conflicts; build them, then run `jj land`", file=sys.stderr)


def land(args: argparse.Namespace) -> None:
    path = sync_state_path()
    if not path.exists():
        fail("no `jj sync` is in progress in this workspace")
    state = json.loads(path.read_text())
    originals, copies = state["originals"], state["copies"]
    root = workspace_root(DEFAULT)

    with lock_megamerge(root, fcntl.LOCK_EX):
        changed = [change for change, commit in originals.items() if resolve(f"present({change})") != [commit]]
        if changed or resolve("megamerge()", "change_id") != [state["megamerge"]]:
            fail(
                "the stack changed after `jj sync`; run `jj sync --abort` and `jj sync` again:\n"
                + "\n".join(f"  {change}" for change in changed)
            )
        missing = [copy for copy in copies.values() if len(resolve(f"present({copy})")) != 1]
        if missing:
            fail("some copies are missing or divergent:\n" + "\n".join(f"  {copy}" for copy in missing))
        conflicts = run("jj", "log", "--no-graph", "-r", f"conflicts() & ({' | '.join(copies.values())})", "-T", COMMIT_LINE)
        if conflicts:
            fail(f"the copies still have conflicts:\n{conflicts}")

        stack = " | ".join(originals.values())
        for change, commit in originals.items():
            # `jj sync` abandons a copy that became empty. The bookmarks of its original and the
            # commits on top of it move to the copy of the nearest ancestor that still has a copy,
            # or to trunk() when no ancestor has one. `jj log` lists the nearest ancestor first.
            ancestors = resolve(f"::{commit} & ({stack})", "change_id")
            target = next((copies[a] for a in ancestors if a in copies), state["trunk"])
            bookmarks = resolve(commit, 'local_bookmarks.map(|b| b.name()).join("\\n")')
            if bookmarks:
                run("jj", "bookmark", "set", *bookmarks, "-r", target, "--allow-backwards")
            descendants = f"children({commit}) ~ ({stack})"
            if resolve(descendants):
                run("jj", "rebase", "-s", descendants, "-o", target)
        run("jj", "abandon", *originals.values())
        path.unlink()

        subprocess.run(["jj", "-R", str(root), "workspace", "update-stale"], check=True)
        divergent = run("jj", "log", "--no-graph", "-r", "divergent()", "-T", COMMIT_LINE)
        if divergent:
            warn(f"some changes are divergent after `jj land`:\n{divergent}")
        conflicts = run("jj", "log", "--no-graph", "-r", "conflicts() & mutable()", "-T", COMMIT_LINE)
        if conflicts:
            warn(f"rebasing onto the synced stack created conflicts:\n{conflicts}")
    print("jj-workspace: landed the synced stack; run `jj seed` in the default workspace", file=sys.stderr)


def clone_destination(source: str) -> str:
    # `jj git clone` names the clone after the last component of the source. The source can be a
    # path, a URL or an scp-like address such as `git@github.com:systemd/systemd.git`.
    name = source.rstrip("/").replace(":", "/").rpartition("/")[2]
    return name.removesuffix(".git")


def clone(args: argparse.Namespace) -> None:
    if args.leading or not args.arguments:
        fail(f"usage: {CLONE_USAGE}")
    source, *rest = args.arguments
    if rest and not rest[0].startswith("-"):
        destination, *options = rest
    else:
        destination, options = clone_destination(source), rest
    dest = Path(destination).absolute()
    if dest.exists() or dest.is_symlink():
        fail(f"{dest} already exists")
    dest.parent.mkdir(parents=True, exist_ok=True)
    if run("stat", "--file-system", "--format=%T", dest.parent).strip() != "btrfs":
        fail(f"{dest.parent} is not on a btrfs filesystem")

    run("btrfs", "subvolume", "create", dest)
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
    if run("stat", "--file-system", "--format=%T", root).strip() != "btrfs":
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
    run("btrfs", "subvolume", "create", new)
    try:
        # `cp -a` keeps the modification times. Without them, the next build in the subvolume
        # rebuilds every target.
        run_with_progress(["cp", "-av", "--reflink=always", f"{root}/.", f"{new}/"], "copying", total)
        root.rename(old)
        try:
            new.rename(root)
        except BaseException:
            old.rename(root)
            raise
    except BaseException:
        delete_subvolume(new)
        raise
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
        "seed", help="move the default workspace onto the megamerge and build it for new workspaces"
    )
    seed_parser.set_defaults(func=seed)
    sync_parser = commands.add_parser("sync", help="fetch and rebase copies of the stack onto trunk()")
    sync_parser.add_argument("--abort", action="store_true", help="abandon the copies of an earlier sync")
    sync_parser.set_defaults(func=sync)
    land_parser = commands.add_parser("land", help="replace the stack with the copies from `jj sync`")
    land_parser.set_defaults(func=land)
    clone_parser = commands.add_parser(
        "clone",
        help="clone a repository into a new btrfs subvolume",
        usage=CLONE_USAGE,
    )
    clone_parser.add_argument("arguments", nargs=argparse.REMAINDER)
    clone_parser.set_defaults(func=clone)
    convert_parser = commands.add_parser("convert", help="convert a directory to a btrfs subvolume")
    convert_parser.add_argument("path", type=Path)
    convert_parser.set_defaults(func=convert)
    # argparse.REMAINDER doesn't collect options that come before the first positional argument.
    # parse_known_args() returns the leading options in a separate list. `jj clone` fails when the
    # list isn't empty.
    args, leading = parser.parse_known_args()
    args.leading = leading
    if args.leading and args.command != "clone":
        parser.error(f"unrecognized arguments: {' '.join(args.leading)}")
    try:
        args.func(args)
    except subprocess.CalledProcessError as error:
        sys.exit(error.returncode)
    except OSError as error:
        fail(str(error))


if __name__ == "__main__":
    main()
