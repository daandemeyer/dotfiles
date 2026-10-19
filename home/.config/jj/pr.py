#!/usr/bin/python3 -SIB
"""Push jj bookmarks to the push remote and open a pull request for each of them.

`jj pr` runs this script. If a bookmark already has an open pull request, the script resolves
the review threads of that pull request and does not create a new one.
"""

import argparse
import json
import subprocess
import sys

BOOKMARK_NAMES = 'bookmarks.map(|b| b.name()).join("\\n") ++ "\\n"'
LOCAL_BOOKMARK_NAMES = 'if(!self.remote() && self.present(), self.name() ++ "\\n")'
TRUNK_NAMES = 'remote_bookmarks.filter(|b| b.remote() != "git").map(|b| b.name()).join("\\n") ++ "\\n"'

THREADS_QUERY = """
query($id: ID!, $endCursor: String) {
  node(id: $id) {
    ... on PullRequest {
      reviewThreads(first: 100, after: $endCursor) {
        nodes { id isResolved }
        pageInfo { hasNextPage endCursor }
      }
    }
  }
}
"""

RESOLVE_MUTATION = """
mutation($id: ID!) {
  resolveReviewThread(input: {threadId: $id}) { thread { id } }
}
"""


def run(*args: str) -> str:
    return subprocess.run(args, check=True, stdout=subprocess.PIPE, text=True).stdout


def lines(*args: str) -> list[str]:
    return [line for line in run(*args).splitlines() if line]


def push_remote() -> str:
    result = subprocess.run(
        ["jj", "config", "get", "git.push"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True
    )
    return result.stdout.strip() if result.returncode == 0 else "origin"


def remote_owner(remote: str) -> str:
    for line in lines("jj", "git", "remote", "list"):
        name, url = line.split()[:2]
        if name == remote:
            return run("gh", "repo", "view", url, "--json", "owner", "--jq", ".owner.login").strip()
    sys.exit(f"jj pr: no such remote: {remote}")


def resolve_threads(pr: dict) -> None:
    pages = json.loads(run(
        "gh", "api", "graphql", "--paginate", "--slurp",
        "-f", f"id={pr['id']}", "-f", f"query={THREADS_QUERY}",
    ))
    threads = [
        thread["id"]
        for page in pages
        for thread in page["data"]["node"]["reviewThreads"]["nodes"]
        if not thread["isResolved"]
    ]
    for thread in threads:
        run("gh", "api", "graphql", "-f", f"id={thread}", "-f", f"query={RESOLVE_MUTATION}")
    print(f"{pr['url']}: resolved {len(threads)} review threads")


def main() -> None:
    parser = argparse.ArgumentParser(prog="jj pr", description=__doc__.splitlines()[0])
    parser.add_argument(
        "-b", "--bookmark", action="append", default=[], metavar="BOOKMARK",
        help="push and open a pull request for bookmarks matching this pattern",
    )
    parser.add_argument(
        "-c", "--change", action="append", default=[], metavar="REVSETS",
        help="push these commits by creating a bookmark and open a pull request for it",
    )
    parser.add_argument(
        "revsets", nargs="*", metavar="REVSETS",
        help="push the bookmarks of these commits, or create one for an unbookmarked commit",
    )
    args = parser.parse_intermixed_args()
    if not (args.bookmark or args.change or args.revsets):
        parser.error("specify -b, -c or REVSETS")

    owner = remote_owner(push_remote())
    base = next(iter(lines("jj", "log", "--no-graph", "-r", "trunk()", "-T", TRUNK_NAMES)), None)
    change_name = run("jj", "config", "get", "templates.git_push_bookmark").strip() + ' ++ "\\n"'

    push = []
    branches = []
    for pattern in args.bookmark:
        push += ["--bookmark", pattern]
        branches += lines("jj", "bookmark", "list", "-T", LOCAL_BOOKMARK_NAMES, "--", pattern)
    for revset in args.change:
        push += ["--change", revset]
        branches += lines("jj", "log", "-r", revset, "--no-graph", "-T", change_name)
    for revset in args.revsets:
        bookmarks = lines("jj", "log", "-r", revset, "--no-graph", "-T", BOOKMARK_NAMES)
        if bookmarks:
            push += [arg for b in bookmarks for arg in ("--bookmark", f"exact:{b}")]
        else:
            # `gh pr create` requires the head branch to exist on the remote. For a commit
            # without a bookmark, `jj git push --change` creates a push-* bookmark.
            push += ["--change", revset]
            bookmarks = lines("jj", "log", "-r", revset, "--no-graph", "-T", change_name)
        branches += bookmarks

    subprocess.run(["jj", "git", "push", *push], check=True)

    for branch in dict.fromkeys(branches):
        # `gh pr list --head` does not accept `owner:branch` and matches the branch name on every
        # fork. A branch with the same name on another fork belongs to a different pull request.
        # The owner check below drops pull requests from other forks.
        prs = [
            pr
            for pr in json.loads(run(
                "gh", "pr", "list", "--head", branch, "--state", "open",
                "--json", "id,url,headRepositoryOwner",
            ))
            if pr["headRepositoryOwner"]["login"] == owner
        ]
        if prs:
            resolve_threads(prs[0])
            continue
        create = ["gh", "pr", "create", "--fill", "--head", f"{owner}:{branch}"]
        if base:
            create += ["--base", base]
        result = subprocess.run(create, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if result.returncode != 0:
            sys.stderr.write(result.stderr)
            sys.exit(result.returncode)
        print(f"{result.stdout.strip()}: created pull request")


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as e:
        sys.exit(e.returncode)
