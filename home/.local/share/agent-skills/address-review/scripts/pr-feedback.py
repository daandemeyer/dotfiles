#!/usr/bin/python3 -SIB
"""Print the review feedback of a pull request: reviews, review threads and comments."""

import argparse
import json
import subprocess
import sys

PR_FIELDS = (
    "id,number,title,url,state,isDraft,author,headRefName,headRefOid,headRepositoryOwner,"
    "baseRefName,reviewDecision,reviews,comments"
)

THREADS_QUERY = """
query($id: ID!, $endCursor: String) {
  node(id: $id) {
    ... on PullRequest {
      reviewThreads(first: 100, after: $endCursor) {
        nodes {
          isResolved
          isOutdated
          path
          line
          originalLine
          comments(first: 100) {
            nodes { author { login } body createdAt diffHunk originalCommit { oid } }
          }
        }
        pageInfo { hasNextPage endCursor }
      }
    }
  }
}
"""

HUNK_LINES = 8


def run(*args: str) -> str:
    return subprocess.run(args, check=True, stdout=subprocess.PIPE, text=True).stdout


def login(node: dict) -> str:
    # GitHub returns a null author for deleted accounts.
    return (node.get("author") or {}).get("login", "ghost")


def indent(text: str) -> str:
    return "\n".join("    " + line for line in text.strip().splitlines())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("pr", help="pull request number or URL")
    parser.add_argument("--all", action="store_true", help="also show resolved threads whose last comment is from the pull request author")
    args = parser.parse_args()

    pr = json.loads(run("gh", "pr", "view", args.pr, "--json", PR_FIELDS))
    author = login(pr)
    pages = json.loads(run(
        "gh", "api", "graphql", "--paginate", "--slurp",
        "-f", f"id={pr['id']}", "-f", f"query={THREADS_QUERY}",
    ))
    threads = [t for page in pages for t in page["data"]["node"]["reviewThreads"]["nodes"]]

    state = "draft" if pr["isDraft"] else pr["state"].lower()
    print(f"# #{pr['number']} {pr['title']}")
    print(f"url: {pr['url']}")
    print(f"author: {author}")
    print(f"state: {state}, review decision: {(pr['reviewDecision'] or 'none').lower()}")
    print(f"head: {pr['headRepositoryOwner']['login']}:{pr['headRefName']} {pr['headRefOid']}")
    print(f"base: {pr['baseRefName']}")

    print("\n## Reviews")
    for review in pr["reviews"]:
        # GitHub creates a review with an empty body and the COMMENTED state for inline comments.
        # The review threads section prints those comments.
        if not review["body"].strip() and review["state"] == "COMMENTED":
            continue
        commit = (review.get("commit") or {}).get("oid", "?")[:12]
        print(f"\n### {login(review)}, {review['state'].lower()}, {review['submittedAt']}, on {commit}")
        if review["body"].strip():
            print(indent(review["body"]))

    print("\n## Review threads")
    # `jj pr` resolves every open thread when it pushes a branch again. A resolved thread in which
    # a reviewer wrote the last comment may never have been addressed.
    threads.sort(key=lambda t: t["isResolved"])
    for thread in threads:
        comments = thread["comments"]["nodes"]
        if not comments:
            continue
        if thread["isResolved"] and login(comments[-1]) == author and not args.all:
            continue
        flags = [f for f, on in (("resolved", thread["isResolved"]), ("outdated", thread["isOutdated"])) if on]
        line = thread["line"] or thread["originalLine"]
        commit = (comments[0].get("originalCommit") or {}).get("oid", "?")[:12]
        print(f"\n### {thread['path']}:{line} on {commit}" + (f" ({', '.join(flags)})" if flags else ""))
        hunk = comments[0]["diffHunk"].splitlines()[-HUNK_LINES:]
        print("\n".join("    " + h for h in hunk))
        for comment in comments:
            who = login(comment)
            print(f"\n  {who}{' (author)' if who == author else ''}, {comment['createdAt']}:")
            print(indent(comment["body"]))

    print("\n## Comments")
    for comment in pr["comments"]:
        who = login(comment)
        print(f"\n### {who}{' (author)' if who == author else ''}, {comment['createdAt']}")
        print(indent(comment["body"]))


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as e:
        sys.exit(e.returncode)
