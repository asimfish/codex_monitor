Development uses the Forge Git policy in `.forge/git-policy.json`.

Enable the local commit and push guards after cloning:

```sh
git config core.hooksPath .githooks
```

Set your Git author and committer identity before committing. A local
`forge.expectedIdentity` setting can require an exact `Name <email>` identity
without imposing one developer's identity on other contributors.

Commit subjects use `[scope/op]: concise imperative title`. Subjects and body
lines must use printable ASCII and stay within 72 characters. Separate the
subject from the body with a blank line. Keep changes independently reviewable.
Run the relevant tests before committing; do not bypass hooks or force-push.
Pull with `--ff-only` and integrate into `main` through a reviewed squash PR.

Branch deletion requires explicit approval, a verified history backup and a
review of the current remote tip. For that operation only, add a local
`forge.reviewedDelete` entry in the form `refs/heads/feat/name=<reviewed-sha>`.
The push guard accepts only the exact reviewed tip and still rejects protected
branches and tags. Remove the entry immediately after the operation; never
commit this local authorization or use it without the branch owner's approval.

Run the portable checks before proposing a merge:

```sh
python tests/test_git_policy.py
python tests/test_merge_compat.py
python scripts/check_git.py --base origin/main --head HEAD
```

The guards reject credential files, runtime account directories and logs.
Tests use fabricated credentials in disposable storage; never add personal
accounts, tokens, session logs or local service configuration. Path checks do
not replace reviewing the staged diff and scanning for secrets. CI verifies
every incoming commit, so a legacy branch with noncompliant messages needs a
new squash candidate based on `origin/main` while retaining its published history.
