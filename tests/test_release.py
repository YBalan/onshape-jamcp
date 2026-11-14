"""Tests for scripts/release.py (version sync, changelog notes, release validation)."""

import datetime as dt

import pytest

from release_helpers import CHANGELOG, ROOT, load_release, write_repo

release = load_release()


@pytest.fixture
def repo(tmp_path):
    return write_repo(tmp_path)


class TestVersions:
    @pytest.mark.parametrize(
        ("current", "target", "expected"),
        [
            ("0.1.0", "patch", "0.1.1"),
            ("0.1.9", "minor", "0.2.0"),
            ("1.2.3", "major", "2.0.0"),
            ("0.1.0", "0.4.2", "0.4.2"),
        ],
    )
    def test_next_version(self, current, target, expected):
        assert release.next_version(current, target) == expected

    @pytest.mark.parametrize("bad", ["1.0", "v1.0.0", "1.0.0rc1", "latest"])
    def test_rejects_non_semver_targets(self, bad):
        with pytest.raises(release.ReleaseError):
            release.next_version("0.1.0", bad)

    def test_read_versions_ignores_other_version_like_lines(self, repo):
        # `target-version = "py310"` in pyproject must not be mistaken for the package version.
        assert release.read_versions(repo) == {
            release.INIT_FILE: "0.2.0",
            release.PYPROJECT_FILE: "0.2.0",
            release.CHANGELOG_FILE: "0.2.0",
        }

    def test_unreleased_heading_is_skipped(self, repo):
        text = CHANGELOG.replace("## [0.2.0]", "## [Unreleased]\n\n- wip\n\n## [0.2.0]", 1)
        (repo / "CHANGELOG.md").write_text(text)
        assert release.read_versions(repo)[release.CHANGELOG_FILE] == "0.2.0"


class TestCheck:
    def test_clean_repo_passes(self, repo):
        assert release.check(repo) == []
        assert release.check(repo, tag="v0.2.0") == []

    def test_tag_mismatch(self, repo):
        problems = release.check(repo, tag="v0.3.0")
        assert any("does not match" in p and "v0.2.0" in p for p in problems)

    def test_version_mismatch_between_files(self, repo):
        (repo / "pyproject.toml").write_text('[project]\nversion = "0.9.9"\n')
        (problem,) = release.check(repo)
        assert "mismatch" in problem and "0.9.9" in problem

    def test_changelog_needs_a_date_and_bullets(self, repo):
        (repo / "CHANGELOG.md").write_text("## [0.2.0]\n\nnothing here\n")
        problems = release.check(repo)
        assert any("release date" in p for p in problems)
        assert any("no bullet" in p for p in problems)

    def test_todo_placeholder_blocks_release(self, repo):
        release.bump(repo, "patch", dt.date(2026, 3, 1))
        problems = release.check(repo)
        assert any("TODO" in p for p in problems)


class TestBump:
    def test_bump_updates_all_three_places_and_adds_stub(self, repo):
        old, new = release.bump(repo, "minor", dt.date(2026, 3, 1))
        assert (old, new) == ("0.2.0", "0.3.0")
        assert '__version__ = "0.3.0"' in (repo / release.INIT_FILE).read_text()
        pyproject = (repo / "pyproject.toml").read_text()
        assert 'version = "0.3.0"' in pyproject and 'target-version = "py310"' in pyproject
        changelog = (repo / "CHANGELOG.md").read_text()
        assert changelog.index("## [0.3.0] - 2026-03-01") < changelog.index("## [0.2.0]")
        assert release.read_versions(repo)[release.CHANGELOG_FILE] == "0.3.0"

    def test_dry_run_changes_nothing(self, repo):
        before = {p: p.read_text() for p in repo.rglob("*") if p.is_file()}
        assert release.bump(repo, "patch", dt.date(2026, 3, 1), dry_run=True) == ("0.2.0", "0.2.1")
        assert {p: p.read_text() for p in repo.rglob("*") if p.is_file()} == before

    def test_refuses_to_go_backwards_or_stay(self, repo):
        for target in ("0.1.0", "0.2.0"):
            with pytest.raises(release.ReleaseError, match="greater"):
                release.bump(repo, target, dt.date(2026, 3, 1))

    def test_refuses_when_versions_already_disagree(self, repo):
        (repo / "pyproject.toml").write_text('[project]\nversion = "0.1.5"\n')
        with pytest.raises(release.ReleaseError, match="disagree"):
            release.bump(repo, "patch", dt.date(2026, 3, 1))

    def test_refuses_when_the_new_version_already_has_a_changelog_section(self, repo):
        # An (odd) older-looking section for the version we are about to create.
        (repo / "CHANGELOG.md").write_text(CHANGELOG + "\n## [0.2.1] - 2025-01-01\n\n- stray\n")
        with pytest.raises(release.ReleaseError, match="already has"):
            release.bump(repo, "patch", dt.date(2026, 3, 1))


UNRELEASED = "## [Unreleased]\n\n### Fixed\n- Fixed the thing.\n- Fixed the other thing.\n\n"


class TestBumpChangelogSources:
    """Where the new version's changelog text comes from: [Unreleased], a typed note, or a TODO."""

    def with_unreleased(self, repo, body=UNRELEASED):
        path = repo / "CHANGELOG.md"
        path.write_text(path.read_text().replace("## [0.2.0]", body + "## [0.2.0]", 1))

    def test_unreleased_bullets_are_promoted_and_an_empty_unreleased_stays_on_top(self, repo):
        self.with_unreleased(repo)
        release.bump(repo, "patch", dt.date(2026, 3, 1))
        changelog = (repo / "CHANGELOG.md").read_text()
        assert (
            "## [Unreleased]\n\n## [0.2.1] - 2026-03-01\n\n### Fixed\n- Fixed the thing."
            in changelog
        )
        assert changelog.index("## [0.2.1]") < changelog.index("## [0.2.0]")
        assert release.PLACEHOLDER not in changelog
        assert release.check(repo) == []  # dated, has bullets, no TODO: ready as it stands
        assert release.read_versions(repo)[release.CHANGELOG_FILE] == "0.2.1"

    def test_a_typed_note_becomes_the_bullet(self, repo):
        release.bump(repo, "minor", dt.date(2026, 3, 1), notes="  Added the shiny feature.  ")
        changelog = (repo / "CHANGELOG.md").read_text()
        assert "## [0.3.0] - 2026-03-01\n\n### Changed\n- Added the shiny feature.\n" in changelog
        assert release.check(repo) == []

    def test_without_any_notes_a_todo_stub_blocks_the_release(self, repo):
        release.bump(repo, "patch", dt.date(2026, 3, 1))
        assert any("TODO" in p for p in release.check(repo))

    def test_an_empty_unreleased_heading_is_skipped_over(self, repo):
        self.with_unreleased(repo, "## [Unreleased]\n\n")
        release.bump(repo, "patch", dt.date(2026, 3, 1), notes="A note.")
        changelog = (repo / "CHANGELOG.md").read_text()
        assert changelog.index("## [Unreleased]") < changelog.index("## [0.2.1]")
        assert changelog.index("## [0.2.1]") < changelog.index("## [0.2.0]")

    def test_two_sources_are_refused_rather_than_merged(self, repo):
        self.with_unreleased(repo)
        before = (repo / "CHANGELOG.md").read_text()
        with pytest.raises(release.ReleaseError, match="one source"):
            release.bump(repo, "patch", dt.date(2026, 3, 1), notes="Also this.")
        assert (repo / "CHANGELOG.md").read_text() == before

    def test_release_notes_source(self, repo):
        text = (repo / "CHANGELOG.md").read_text()
        with pytest.raises(release.ReleaseError, match="Nothing to release"):
            release.release_notes_source(text, None)
        assert release.release_notes_source(text, "typed") == "the note you typed"
        self.with_unreleased(repo)
        text = (repo / "CHANGELOG.md").read_text()
        assert "[Unreleased]" in release.release_notes_source(text, None)

    def test_unreleased_without_bullets_does_not_count(self):
        assert not release.unreleased_notes(
            "## [Unreleased]\n\nnothing yet\n\n## [0.1.0] - 2026-01-01\n- x\n"
        )
        assert release.unreleased_notes("## [Unreleased]\n- x\n\n## [0.1.0] - 2026-01-01\n- y\n")


class TestActionsUrl:
    @pytest.mark.parametrize(
        "remote",
        [
            "https://github.com/YBalan/onshape-jamcp.git",
            "https://github.com/YBalan/onshape-jamcp",
            "git@github.com:YBalan/onshape-jamcp.git",
            "ssh://git@github.com/YBalan/onshape-jamcp.git",
        ],
    )
    def test_github_remotes(self, remote):
        assert release.actions_url(remote) == "https://github.com/YBalan/onshape-jamcp/actions"

    @pytest.mark.parametrize("remote", ["/tmp/remote.git", "https://gitlab.com/a/b.git", ""])
    def test_other_remotes_give_no_link(self, remote):
        assert release.actions_url(remote) is None


class TestNotes:
    def test_section_extraction(self):
        section = release.changelog_section(CHANGELOG, "0.1.0")
        assert section is not None
        assert "- First thing." in section and "- New thing." not in section
        assert release.changelog_section(CHANGELOG, "9.9.9") is None

    def test_newest_section_stops_at_the_next_heading(self):
        section = release.changelog_section(CHANGELOG, "0.2.0")
        assert "New thing." in section and "First thing." not in section


def test_the_real_repository_is_release_consistent():
    """The checked-in version, changelog and package metadata must always agree."""
    assert release.check(ROOT) == []
