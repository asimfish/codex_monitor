#!/usr/bin/env python3
"""Portable Forge commit and repository-data checks; never print secret contents."""
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parent.parent
POLICY = json.loads((ROOT / ".forge/git-policy.json").read_text(encoding="utf-8"))


def git(*arguments):
    return subprocess.check_output(["git", *arguments], text=True).strip()


def message_errors(message):
    rules = POLICY["commit"]
    lines = message.splitlines()
    if not lines:
        return ["empty commit message"]
    errors = []
    operations = "|".join(re.escape(value) for value in rules["allowed_operations"])
    pattern = r"^\[[a-z0-9_]+(?:[/-][a-z0-9_]+)*/(?:%s)!?\]: .+$" % operations
    if not re.fullmatch(pattern, lines[0]):
        errors.append("subject must use [scope/op]: title")
    if len(lines[0]) > rules["subject_max_length"] or lines[0].endswith("."):
        errors.append("subject exceeds 72 characters or ends with a period")
    if len(lines) > 1 and any(lines[1:]) and lines[1] != "":
        errors.append("subject and body need a blank line")
    for number, line in enumerate(lines, 1):
        if any(ord(character) < 32 or ord(character) > 126 for character in line):
            errors.append("line %d must contain printable ASCII" % number)
        if number > 1 and len(line) > rules["body_line_max_length"]:
            errors.append("body line %d exceeds 72 characters" % number)
    return errors


def sensitive_path(name):
    path = PurePosixPath(name.lower())
    internal = {".codex", ".codex-accounts", "_auth_locks", "_reset_requests", "_api_accounts", "sessions", "__pycache__"}
    leaf = path.name
    return bool(internal.intersection(path.parts)
                or leaf in {"auth.json", "dashboard.token", "credentials.json", "secrets.json", ".env"}
                or leaf.startswith("auth-") and leaf.endswith(".json")
                or leaf.startswith(".env.") and leaf != ".env.example"
                or leaf.endswith((".log", ".pyc")))


def check_paths(arguments):
    raw = subprocess.check_output(["git", "diff", "--name-only", "--diff-filter=ACMR", "-z", *arguments])
    return ["runtime or credential data must not be committed: " + name
            for name in raw.decode("utf-8").split("\0") if name and sensitive_path(name)]


def check_branch(branch, writing=False):
    if subprocess.call(["git", "check-ref-format", "--branch", branch],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) != 0:
        return ["branch must be a valid Git ref"]
    if branch in POLICY["branch"]["protected"]:
        return ["use a reviewed PR for protected branches"] if writing else []
    prefix, separator, suffix = branch.partition("/")
    if not separator or not suffix or prefix not in POLICY["branch"]["allowed_prefixes"]:
        return ["branch must use a Forge prefix"]
    return []


def check_range(base, head, identity=None):
    errors = check_paths([base, head])
    commits = git("rev-list", base + ".." + head).splitlines()
    for commit in commits:
        errors.extend(check_paths([commit + "^", commit]))
        message = subprocess.check_output(["git", "show", "-s", "--format=%B", commit], text=True)
        errors.extend(commit[:7] + ": " + error for error in message_errors(message))
        if identity:
            people = git("show", "-s", "--format=%an <%ae>%n%cn <%ce>", commit).splitlines()
            if people != [identity, identity]:
                errors.append(commit[:7] + ": author and committer must match the configured identity")
    return errors


def reviewed_deletions():
    result = subprocess.run(["git", "config", "--local", "--get-all", "forge.reviewedDelete"],
                            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode not in (0, 1):
        raise ValueError("cannot read reviewed deletions")
    return dict(line.split("=", 1) for line in result.stdout.splitlines())


def check_deletion(remote_ref, remote_sha, reviewed):
    prefix = "refs/heads/"
    if not remote_ref.startswith(prefix):
        return ["only reviewed feature branches may be deleted"]
    errors = check_branch(remote_ref[len(prefix):], writing=True)
    if reviewed.get(remote_ref) != remote_sha or set(remote_sha) == {"0"}:
        errors.append("branch deletion requires review of the exact remote tip")
    return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--message-file", type=Path)
    parser.add_argument("--base", default="origin/main")
    parser.add_argument("--head", default="HEAD")
    parser.add_argument("--branch")
    parser.add_argument("--identity")
    parser.add_argument("--pre-push", action="store_true")
    args = parser.parse_args()
    if args.pre_push:
        errors = []
        reviewed = reviewed_deletions()
        for line in sys.stdin:
            local_ref, local_sha, remote_ref, remote_sha = line.split()
            if set(local_sha) == {"0"}:
                errors.extend(check_deletion(remote_ref, remote_sha, reviewed))
                continue
            prefix = "refs/heads/"
            branch = remote_ref[len(prefix):] if remote_ref.startswith(prefix) else remote_ref
            errors.extend(check_branch(branch, writing=True))
            if set(remote_sha) != {"0"} and subprocess.call(
                    ["git", "merge-base", "--is-ancestor", remote_sha, local_sha]) != 0:
                errors.append("push must preserve published history")
            errors.extend(check_range(args.base, local_sha, args.identity))
    elif args.message_file:
        branch = git("branch", "--show-current")
        errors = check_branch(branch, writing=True)
        errors.extend(message_errors(args.message_file.read_text(encoding="utf-8")))
        errors.extend(check_paths(["--cached"]))
        if args.identity:
            for role in ("GIT_AUTHOR_IDENT", "GIT_COMMITTER_IDENT"):
                if git("var", role).rsplit(">", 1)[0] + ">" != args.identity:
                    errors.append("author and committer must match the configured identity")
    else:
        errors = check_range(args.base, args.head, args.identity)
        if args.branch:
            errors.extend(check_branch(args.branch))
    for error in errors:
        print("FAIL: " + error, file=sys.stderr)
    if not errors:
        print("Git policy and runtime-data path checks passed")
    return bool(errors)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        print("FAIL: cannot verify Git policy (%s)" % type(error).__name__, file=sys.stderr)
        sys.exit(1)
