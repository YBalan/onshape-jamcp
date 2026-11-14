"""The git half of scripts/release.py, exercised for real in a throw-away repository.

Each test gets a work repo plus a local *bare* repo standing in for GitHub, so commit, tag and push
actually happen - and the tests can inspect exactly what reached the "remote".
"""

import datetime as dt
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from release_helpers import load_release, write_repo

release = load_release()

needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
OK_STEP = [("ok", [sys.executable, "-c", "pass"])]


def git(cwd: Path, *args: str) -> str:
    done = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)
    return done.stdout.strip()


class Sandbox:
    def __init__(self, work: Path, remote: Path) -> None:
        self.work, self.remote = work, remote

    def remote_tags(self) -> list[str]:
        lines = git(self.work, "ls-remote", "--tags", str(self.remote)).splitlines()
        return [line.split("refs/tags/")[1] for line in lines]

    def remote_head(self) -> str:
        # --git-dir is explicit on purpose: `safe.bareRepository=explicit` (a common hardening
        # setting) forbids running git inside a bare repo by cwd.
        return git(self.work, "--git-dir", str(self.remote), "rev-parse", "main")

    def prepare_release(self) -> None:
        """What a maintainer does before publishing: bump, then write real changelog notes."""
        release.bump(self.work, "patch", dt.date(2026, 3, 1))  # 0.2.0 -> 0.2.1
        path = self.work / "CHANGELOG.md"
        path.write_text(path.read_text().replace(release.PLACEHOLDER, "Fixed a thing."))


@pytest.fixture
def sandbox(tmp_path):
    remote = tmp_path / "remote.git"
    git(tmp_path, "init", "--bare", "-b", "main", str(remote))
    work = write_repo(tmp_path / "work")
    git(work, "init", "-b", "main")
    git(work, "config", "user.name", "Release Test")
    git(work, "config", "user.email", "release@test.invalid")
    git(work, "add", ".")
    git(work, "commit", "-m", "initial")
    git(work, "remote", "add", "origin", str(remote))
    git(work, "push", "-u", "origin", "main")
    return Sandbox(work, remote)


@needs_git
class TestGitSteps:
    def test_not_a_git_repository(self, tmp_path):
        write_repo(tmp_path)
        with pytest.raises(release.ReleaseError, match="not a git repository"):
            release.commit_release(tmp_path)

    def test_commit_refuses_an_unfinished_changelog(self, sandbox):
        release.bump(sandbox.work, "patch", dt.date(2026, 3, 1))  # TODO still in place
        with pytest.raises(release.ReleaseError, match="Not release-ready"):
            release.commit_release(sandbox.work)

    def test_commit_touches_only_the_release_files(self, sandbox):
        sandbox.prepare_release()
        (sandbox.work / "README.md").write_text("unrelated edit\n")  # must NOT be swept in
        git(sandbox.work, "add", "README.md")  # even if the user already staged it
        assert release.commit_release(sandbox.work) == "Release 0.2.1"
        files = set(git(sandbox.work, "show", "--name-only", "--format=", "HEAD").splitlines())
        assert files == {release.INIT_FILE, "pyproject.toml", "CHANGELOG.md"}
        assert git(sandbox.work, "status", "--porcelain").startswith("M  README.md")

    def test_commit_is_a_no_op_when_already_committed(self, sandbox):
        sandbox.prepare_release()
        release.commit_release(sandbox.work)
        assert release.commit_release(sandbox.work) is None

    def test_tag_needs_a_clean_tree(self, sandbox):
        sandbox.prepare_release()
        release.commit_release(sandbox.work)
        (sandbox.work / "README.md").write_text("dirty\n")
        with pytest.raises(release.ReleaseError, match="uncommitted"):
            release.tag_release(sandbox.work)

    def test_tag_is_annotated_and_never_duplicated(self, sandbox):
        sandbox.prepare_release()
        release.commit_release(sandbox.work)
        assert release.tag_release(sandbox.work) == "v0.2.1"
        assert git(sandbox.work, "cat-file", "-t", "v0.2.1") == "tag"  # annotated, not lightweight
        with pytest.raises(release.ReleaseError, match="already exists"):
            release.tag_release(sandbox.work)

    @pytest.mark.parametrize("answer", ["no", "", "y", "YES", "true"])
    def test_push_needs_exactly_yes(self, sandbox, answer):
        sandbox.prepare_release()
        release.commit_release(sandbox.work)
        release.tag_release(sandbox.work)
        with pytest.raises(release.ReleaseError, match="Nothing was pushed"):
            release.push_release(sandbox.work, answer)
        assert sandbox.remote_tags() == []

    def test_push_sends_the_branch_and_the_tag(self, sandbox):
        sandbox.prepare_release()
        release.commit_release(sandbox.work)
        release.tag_release(sandbox.work)
        assert release.push_release(sandbox.work, "yes") == "v0.2.1"
        assert sandbox.remote_tags()[0].startswith("v0.2.1")
        assert sandbox.remote_head() == git(sandbox.work, "rev-parse", "HEAD")

    def test_push_refuses_a_tag_that_is_not_at_head(self, sandbox):
        sandbox.prepare_release()
        release.commit_release(sandbox.work)
        release.tag_release(sandbox.work)
        (sandbox.work / "README.md").write_text("later commit\n")
        git(sandbox.work, "commit", "-am", "after tagging")
        with pytest.raises(release.ReleaseError, match="does not point at HEAD"):
            release.push_release(sandbox.work, "yes")
        assert sandbox.remote_tags() == []


@needs_git
class TestPublish:
    def test_full_flow(self, sandbox):
        sandbox.prepare_release()
        assert release.publish(sandbox.work, "yes", OK_STEP) == "v0.2.1"
        assert any(t.startswith("v0.2.1") for t in sandbox.remote_tags())
        assert git(sandbox.work, "log", "-1", "--format=%s") == "Release 0.2.1"
        assert git(sandbox.work, "status", "--porcelain") == ""

    @pytest.mark.parametrize("answer", ["no", "", "maybe"])
    def test_does_nothing_without_confirmation(self, sandbox, answer):
        sandbox.prepare_release()
        before = git(sandbox.work, "rev-parse", "HEAD")
        with pytest.raises(release.ReleaseError, match="Cancelled"):
            release.publish(sandbox.work, answer, OK_STEP)
        assert git(sandbox.work, "rev-parse", "HEAD") == before
        assert git(sandbox.work, "tag") == "" and sandbox.remote_tags() == []

    def test_unrelated_changes_are_caught_before_anything_runs(self, sandbox):
        sandbox.prepare_release()
        (sandbox.work / "README.md").write_text("dirty\n")
        marker = sandbox.work / "verify-ran"
        steps = [("probe", [sys.executable, "-c", f"open({str(marker)!r}, 'w').close()"])]
        with pytest.raises(release.ReleaseError, match="outside the release files"):
            release.publish(sandbox.work, "yes", steps)
        assert not marker.exists()  # verification never even started
        assert git(sandbox.work, "tag") == ""

    def test_failed_verification_leaves_git_untouched(self, sandbox):
        sandbox.prepare_release()
        before = git(sandbox.work, "rev-parse", "HEAD")
        steps = [
            ("good", [sys.executable, "-c", "pass"]),
            ("bad", [sys.executable, "-c", "raise SystemExit(3)"]),
        ]
        with pytest.raises(release.ReleaseError, match="step 'bad'"):
            release.publish(sandbox.work, "yes", steps)
        assert git(sandbox.work, "rev-parse", "HEAD") == before  # nothing was committed
        assert git(sandbox.work, "tag") == "" and sandbox.remote_tags() == []

    def test_existing_tag_is_caught_up_front(self, sandbox):
        sandbox.prepare_release()
        git(sandbox.work, "tag", "v0.2.1")
        with pytest.raises(release.ReleaseError, match="already exists"):
            release.publish(sandbox.work, "yes", OK_STEP)

    def test_missing_remote_is_caught_up_front(self, sandbox):
        sandbox.prepare_release()
        git(sandbox.work, "remote", "remove", "origin")
        with pytest.raises(release.ReleaseError, match="no git remote"):
            release.publish(sandbox.work, "yes", OK_STEP)
        assert git(sandbox.work, "tag") == ""

    def test_a_single_remote_with_another_name_is_used(self, sandbox):
        """Real-world case: the only remote is called e.g. 'onshape-jamcp', not 'origin'."""
        git(sandbox.work, "remote", "rename", "origin", "onshape-jamcp")
        sandbox.prepare_release()
        assert release.publish(sandbox.work, "yes", OK_STEP) == "v0.2.1"
        assert any(t.startswith("v0.2.1") for t in sandbox.remote_tags())

    def test_ambiguous_remotes_are_refused(self, sandbox):
        git(sandbox.work, "remote", "rename", "origin", "first")
        git(sandbox.work, "remote", "add", "second", str(sandbox.remote))
        sandbox.prepare_release()
        with pytest.raises(release.ReleaseError, match="Several remotes"):
            release.publish(sandbox.work, "yes", OK_STEP)
        assert git(sandbox.work, "tag") == ""

    def test_origin_wins_when_there_are_several_remotes(self, sandbox):
        git(sandbox.work, "remote", "add", "backup", str(sandbox.remote))
        assert release.resolve_remote(sandbox.work) == "origin"

    def test_untracked_files_block_the_release(self, sandbox):
        """A new file that was never `git add`ed (like the workflow itself) must not slip by."""
        sandbox.prepare_release()
        (sandbox.work / "new-workflow.yml").write_text("on: push\n")
        marker = sandbox.work / "verify-ran"
        steps = [("probe", [sys.executable, "-c", f"open({str(marker)!r}, 'w').close()"])]
        with pytest.raises(release.ReleaseError, match="Untracked files"):
            release.publish(sandbox.work, "yes", steps)
        assert not marker.exists() and git(sandbox.work, "tag") == ""

    def test_ignored_files_do_not_block(self, sandbox):
        (sandbox.work / ".gitignore").write_text("dist/\n")
        git(sandbox.work, "add", ".gitignore")
        git(sandbox.work, "commit", "-m", "ignore dist")
        sandbox.prepare_release()
        (sandbox.work / "dist").mkdir()
        (sandbox.work / "dist" / "artifact.whl").write_text("x")
        assert release.publish(sandbox.work, "yes", OK_STEP) == "v0.2.1"

    def test_push_failure_explains_the_local_state(self, sandbox, tmp_path):
        """Someone else pushed first: the push is rejected, and the message says what to do."""
        other = tmp_path / "other"
        git(tmp_path, "clone", "-q", str(sandbox.remote), str(other))
        git(other, "config", "user.name", "Other")
        git(other, "config", "user.email", "other@test.invalid")
        (other / "other.txt").write_text("x\n")
        git(other, "add", ".")
        git(other, "commit", "-m", "someone else")
        git(other, "push", "origin", "main")
        sandbox.prepare_release()
        with pytest.raises(release.ReleaseError, match="exist locally but were not pushed"):
            release.publish(sandbox.work, "yes", OK_STEP)
        assert sandbox.remote_tags() == []
        assert git(sandbox.work, "tag") == "v0.2.1"  # still there, as the message says


@needs_git
class TestPreflightCommand:
    def test_reports_the_plan_without_changing_anything(self, sandbox, capsys, monkeypatch):
        sandbox.prepare_release()
        head = git(sandbox.work, "rev-parse", "HEAD")
        # Only the three release files are modified, so a release could start.
        assert release.preflight(sandbox.work) == "v0.2.1"
        assert git(sandbox.work, "rev-parse", "HEAD") == head and git(sandbox.work, "tag") == ""

    def test_blocks_on_unrelated_changes(self, sandbox):
        sandbox.prepare_release()
        (sandbox.work / "README.md").write_text("dirty\n")
        with pytest.raises(release.ReleaseError, match="outside the release files"):
            release.preflight(sandbox.work)


class TestVerify:
    def test_stops_at_the_first_failing_step(self, tmp_path):
        marker = tmp_path / "third-ran"
        steps = [
            ("first", [sys.executable, "-c", "pass"]),
            ("second", [sys.executable, "-c", "raise SystemExit(1)"]),
            ("third", [sys.executable, "-c", f"open({str(marker)!r}, 'w').close()"]),
        ]
        with pytest.raises(release.ReleaseError, match="step 'second'"):
            release.verify(tmp_path, steps)
        assert not marker.exists()

    def test_the_real_gate_covers_what_ci_covers(self):
        assert [name for name, _ in release.VERIFY_STEPS] == [
            "lint",
            "format",
            "tests",
            "docs in sync",
            "version + changelog",
            "build + twine check",
        ]


class TestCommandLineSafety:
    """The default invocation of the dangerous subcommands must be a harmless refusal."""

    @pytest.mark.parametrize("argv", [["publish"], ["push"], ["publish", "--confirm", "no"]])
    def test_defaults_never_act(self, argv, capsys):
        assert release.main(argv) == 1
        assert "error:" in capsys.readouterr().err
