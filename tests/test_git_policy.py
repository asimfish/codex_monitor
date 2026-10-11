"""Exercise the portable commit guard in disposable Git repositories."""
import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts/check_git.py"
spec = importlib.util.spec_from_file_location("check_git", SCRIPT)
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


class GitPolicyTests(unittest.TestCase):
    def test_message_contract(self):
        self.assertEqual(guard.message_errors("[web/fix]: preserve compatibility\n\n" + "a" * 72), [])
        for message in (
            "[web/fix]: preserve compatibility\n\n" + "a" * 73,
            "[web/ux]: preserve compatibility",
            "[web/fix]: preserve compatibility.",
            "[web/fix]: preserve compatibility\nNo blank line",
            "[web/fix]: " + "a" * 72,
            "[web/fix]: non-ASCII \u4fee\u590d",
        ):
            with self.subTest(message=message):
                self.assertTrue(guard.message_errors(message))

    def test_data_paths_are_distinguished_from_software(self):
        for name in ("auth.json", "downloads/auth-test.json", ".env.production", "dashboard.token",
                     "data/.codex/auth.json", "_auth_locks/request.lock", "sessions/session.jsonl", "server.log"):
            self.assertTrue(guard.sensitive_path(name), name)
        for name in ("codex_monitor/oauth.py", "Sources/CodexMonitor/AccountManager.swift",
                     "tests/test_reset_credits.py", ".forge/git-policy.json", ".env.example"):
            self.assertFalse(guard.sensitive_path(name), name)

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="codex-git-policy-")
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.environment = dict(os.environ, GIT_AUTHOR_NAME="ChangWinde", GIT_COMMITTER_NAME="ChangWinde",
                                GIT_AUTHOR_EMAIL="ChangWinde@163.com", GIT_COMMITTER_EMAIL="ChangWinde@163.com")
        self.git("init", "-b", "feat/fixture")

    def git(self, *arguments):
        return subprocess.check_output(["git", *arguments], cwd=self.directory,
                                       env=self.environment, text=True, stderr=subprocess.STDOUT).strip()

    def run_guard(self, *arguments, input=None):
        return subprocess.run([sys.executable, str(SCRIPT), *arguments], cwd=self.directory,
                              env=self.environment, input=input, text=True,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

    def test_branch_deletion_requires_review_of_the_remote_tip(self):
        remote_ref = "refs/heads/feat/obsolete"
        remote_sha = "a" * 40
        deletion = "(delete) %s %s %s\n" % ("0" * 40, remote_ref, remote_sha)
        result = self.run_guard("--pre-push", input=deletion)
        self.assertNotEqual(result.returncode, 0)
        self.git("config", "forge.reviewedDelete", remote_ref + "=" + remote_sha)
        result = self.run_guard("--pre-push", input=deletion)
        self.assertEqual(result.returncode, 0, result.stdout)

    def test_branch_deletion_rejects_stale_review(self):
        remote_ref = "refs/heads/feat/obsolete"
        self.git("config", "forge.reviewedDelete", remote_ref + "=" + "b" * 40)
        deletion = "(delete) %s %s %s\n" % ("0" * 40, remote_ref, "a" * 40)
        result = self.run_guard("--pre-push", input=deletion)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("exact remote tip", result.stdout)

    def test_review_cannot_delete_protected_branches_or_tags(self):
        for remote_ref in ("refs/heads/main", "refs/heads/master", "refs/tags/feat/obsolete"):
            with self.subTest(remote_ref=remote_ref):
                remote_sha = "a" * 40
                self.git("config", "forge.reviewedDelete", remote_ref + "=" + remote_sha)
                deletion = "(delete) %s %s %s\n" % ("0" * 40, remote_ref, remote_sha)
                result = self.run_guard("--pre-push", input=deletion)
                self.assertNotEqual(result.returncode, 0)

    def test_staged_credentials_are_rejected_without_printing_contents(self):
        (self.directory / "auth.json").write_text('{"fixture": "private-content-must-not-print"}')
        self.git("add", "auth.json")
        message = self.directory / "message.txt"
        message.write_text("[web/fix]: preserve compatibility")
        result = self.run_guard("--message-file", str(message))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("auth.json", result.stdout)
        self.assertNotIn("private-content-must-not-print", result.stdout)

    def test_protected_branch_commit_is_rejected(self):
        self.git("checkout", "-b", "main")
        message = self.directory / "message.txt"
        message.write_text("[web/fix]: preserve compatibility")
        result = self.run_guard("--message-file", str(message))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("protected", result.stdout)

    def test_range_checks_committed_identity(self):
        source = self.directory / "source.py"
        source.write_text("# baseline\n")
        self.git("add", "source.py")
        self.git("commit", "-m", "[test/test]: establish fixture")
        base = self.git("rev-parse", "HEAD")
        source.write_text("# candidate\n")
        self.git("add", "source.py")
        self.git("commit", "-m", "[test/test]: update fixture")
        result = self.run_guard("--base", base, "--identity", "ChangWinde <ChangWinde@163.com>")
        self.assertEqual(result.returncode, 0, result.stdout)
        result = self.run_guard("--base", base, "--identity", "Mismatch <fixture@example.test>")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("identity", result.stdout)

    def test_range_rejects_runtime_data_removed_by_a_later_commit(self):
        (self.directory / "source.py").write_text("# baseline\n")
        self.git("add", "source.py")
        self.git("commit", "-m", "[test/test]: establish fixture")
        base = self.git("rev-parse", "HEAD")
        credential = self.directory / "auth.json"
        credential.write_text('{"fixture": "private-content-must-not-print"}')
        self.git("add", "auth.json")
        self.git("commit", "-m", "[test/test]: simulate accidental data")
        self.git("rm", "auth.json")
        self.git("commit", "-m", "[test/test]: remove simulated data")
        result = self.run_guard("--base", base)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("auth.json", result.stdout)
        self.assertNotIn("private-content-must-not-print", result.stdout)


if __name__ == "__main__":
    unittest.main()
