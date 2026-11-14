"""Release helper: keeps the version in sync and validates/prepares a PyPI release.

    python scripts/release.py check [--tag v0.1.0]     # versions + changelog consistent?
    python scripts/release.py notes [0.1.0]            # print a version's changelog section
    python scripts/release.py bump patch|minor|major|X.Y.Z [--dry-run]
    python scripts/release.py build                    # clean build + `twine check`
    python scripts/release.py commit                   # commit ONLY the release files
    python scripts/release.py tag                      # annotated tag vX.Y.Z (needs a clean tree)
    python scripts/release.py push --confirm yes       # push branch + tag, starts PyPI workflow
    python scripts/release.py preflight                # read-only: is the git state release-ready?
    python scripts/release.py verify                   # lint, format, tests, docs, check, build
    python scripts/release.py publish --confirm yes    # verify -> commit -> tag -> push (all)

Standard library only (so CI can run it before installing anything). It never uploads to PyPI
itself: pushing the tag is what starts .github/workflows/release.yml, which does. The git steps
are guarded: ``commit`` stages only the release files, ``tag`` refuses a dirty tree or an existing
tag, ``push`` and ``publish`` refuse unless ``--confirm yes`` is passed, and ``publish`` runs its
steps in order inside one process, stopping at the first failure. See docs/publishing.md; the
VS Code tasks in .vscode/tasks.json wrap all of this.

The version lives in three places that must agree: ``src/onshape_jamcp/__init__.py``,
``pyproject.toml`` and the newest ``## [X.Y.Z]`` heading of ``CHANGELOG.md``.
"""

import argparse
import datetime as dt
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INIT_FILE = "src/onshape_jamcp/__init__.py"
PYPROJECT_FILE = "pyproject.toml"
CHANGELOG_FILE = "CHANGELOG.md"

SEMVER = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
INIT_RE = re.compile(r'^__version__ = "([^"]+)"', re.M)
PYPROJECT_RE = re.compile(r'^version = "([^"]+)"', re.M)
HEADING_RE = re.compile(r"^## \[([^\]]+)\](?: - (\d{4}-\d{2}-\d{2}))?[ \t]*$", re.M)
PLACEHOLDER = "TODO: describe the changes in this release."
#: Files a release commit may contain (uv.lock only if it changed). Nothing else is ever staged.
RELEASE_FILES = (INIT_FILE, PYPROJECT_FILE, CHANGELOG_FILE, "uv.lock")

PY = sys.executable
#: The quality gate, in order. Each entry: (name, argv). Run from the repository root.
VERIFY_STEPS: list[tuple[str, list[str]]] = [
    ("lint", [PY, "-m", "ruff", "check", "src", "tests", "scripts"]),
    ("format", [PY, "-m", "ruff", "format", "--check", "src", "tests", "scripts"]),
    ("tests", [PY, "-m", "pytest", "-q"]),
    ("docs in sync", [PY, "scripts/gen_tools_doc.py", "--check"]),
    ("version + changelog", [PY, "scripts/release.py", "check"]),
    ("build + twine check", [PY, "scripts/release.py", "build"]),
]


class ReleaseError(Exception):
    """A problem the maintainer must fix before releasing."""


def parse_semver(text: str) -> tuple[int, int, int]:
    match = SEMVER.match(text)
    if not match:
        raise ReleaseError(f"{text!r} is not a plain MAJOR.MINOR.PATCH version.")
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def next_version(current: str, target: str) -> str:
    """Resolve ``patch``/``minor``/``major`` or an explicit ``X.Y.Z`` against ``current``."""
    major, minor, patch = parse_semver(current)
    if target == "patch":
        return f"{major}.{minor}.{patch + 1}"
    if target == "minor":
        return f"{major}.{minor + 1}.0"
    if target == "major":
        return f"{major + 1}.0.0"
    parse_semver(target)
    return target


def _find(pattern: re.Pattern[str], text: str, what: str) -> str:
    match = pattern.search(text)
    if not match:
        raise ReleaseError(f"Could not find the version in {what}.")
    return match.group(1)


def read_versions(root: Path) -> dict[str, str]:
    """Return the version as seen by each of the three places that carry it."""
    changelog = (root / CHANGELOG_FILE).read_text(encoding="utf-8")
    headings = [h for h in HEADING_RE.findall(changelog) if h[0].lower() != "unreleased"]
    if not headings:
        raise ReleaseError(f"{CHANGELOG_FILE} has no '## [X.Y.Z]' heading.")
    return {
        INIT_FILE: _find(INIT_RE, (root / INIT_FILE).read_text(encoding="utf-8"), INIT_FILE),
        PYPROJECT_FILE: _find(
            PYPROJECT_RE, (root / PYPROJECT_FILE).read_text(encoding="utf-8"), PYPROJECT_FILE
        ),
        CHANGELOG_FILE: headings[0][0],
    }


def changelog_section(text: str, version: str) -> str | None:
    """The body of ``## [version]`` up to the next version heading, or None if absent."""
    matches = list(HEADING_RE.finditer(text))
    for index, match in enumerate(matches):
        if match.group(1) == version:
            end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
            return text[match.end() : end].strip("\n")
    return None


def check(root: Path, tag: str | None = None) -> list[str]:
    """Return a list of problems (empty = ready to release)."""
    problems: list[str] = []
    try:
        versions = read_versions(root)
    except ReleaseError as exc:
        return [str(exc)]
    if len(set(versions.values())) != 1:
        detail = ", ".join(f"{name}={value}" for name, value in versions.items())
        return [f"Version mismatch: {detail}. Run `release.py bump` or fix by hand."]
    version = versions[PYPROJECT_FILE]
    try:
        parse_semver(version)
    except ReleaseError as exc:
        problems.append(str(exc))

    changelog = (root / CHANGELOG_FILE).read_text(encoding="utf-8")
    heading = next((m for m in HEADING_RE.finditer(changelog) if m.group(1) == version), None)
    if heading is None or not heading.group(2):
        problems.append(
            f"{CHANGELOG_FILE}: '## [{version}]' needs a release date (` - YYYY-MM-DD`)."
        )
    body = changelog_section(changelog, version) or ""
    if not any(line.lstrip().startswith("- ") for line in body.splitlines()):
        problems.append(f"{CHANGELOG_FILE}: the [{version}] section has no bullet points.")
    if PLACEHOLDER in body:
        problems.append(f"{CHANGELOG_FILE}: replace the TODO placeholder in [{version}].")
    if tag is not None and tag != f"v{version}":
        problems.append(f"Tag {tag!r} does not match the package version (expected v{version}).")
    return problems


def bump(root: Path, target: str, today: dt.date, dry_run: bool = False) -> tuple[str, str]:
    """Update the version everywhere and add a changelog stub. Returns ``(old, new)``."""
    versions = read_versions(root)
    if len(set(versions.values())) != 1:
        raise ReleaseError(f"Versions already disagree ({versions}); fix that first.")
    old = versions[PYPROJECT_FILE]
    new = next_version(old, target)
    if parse_semver(new) <= parse_semver(old):
        raise ReleaseError(f"New version {new} must be greater than the current {old}.")
    changelog_path = root / CHANGELOG_FILE
    changelog = changelog_path.read_text(encoding="utf-8")
    if changelog_section(changelog, new) is not None:
        raise ReleaseError(f"{CHANGELOG_FILE} already has a [{new}] section.")

    first = HEADING_RE.search(changelog)
    assert first is not None
    stub = f"## [{new}] - {today.isoformat()}\n\n### Changed\n- {PLACEHOLDER}\n\n"
    new_changelog = changelog[: first.start()] + stub + changelog[first.start() :]

    edits = {
        INIT_FILE: INIT_RE.sub(f'__version__ = "{new}"', (root / INIT_FILE).read_text("utf-8"), 1),
        PYPROJECT_FILE: PYPROJECT_RE.sub(
            f'version = "{new}"', (root / PYPROJECT_FILE).read_text("utf-8"), 1
        ),
        CHANGELOG_FILE: new_changelog,
    }
    if not dry_run:
        for name, content in edits.items():
            (root / name).write_text(content, encoding="utf-8", newline="\n")
    return old, new


def build(root: Path) -> list[Path]:
    """Clean ``dist/``, build sdist + wheel, and run ``twine check`` on them."""
    dist = root / "dist"
    if dist.exists():
        shutil.rmtree(dist)
    subprocess.run([sys.executable, "-m", "build", "--outdir", str(dist)], cwd=root, check=True)
    artifacts = sorted(dist.iterdir())
    subprocess.run([sys.executable, "-m", "twine", "check", *map(str, artifacts)], check=True)
    return artifacts


def _git(root: Path, *args: str) -> str:
    try:
        result = subprocess.run(
            ["git", *args], cwd=root, capture_output=True, text=True, check=False
        )
    except FileNotFoundError:
        raise ReleaseError("git is not installed or not on PATH.") from None
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise ReleaseError(f"git {' '.join(args)} failed: {detail}")
    return result.stdout.strip()


def _require_git_repo(root: Path) -> None:
    try:
        inside = _git(root, "rev-parse", "--is-inside-work-tree")
    except ReleaseError as exc:
        if "not a git repository" in str(exc).lower():
            raise ReleaseError(
                "This folder is not a git repository. Create it and push it to GitHub first "
                "(docs/publishing.md, section 1)."
            ) from None
        raise
    if inside != "true":
        raise ReleaseError("Not inside a git work tree.")


def _require_release_ready(root: Path) -> str:
    """Refuse to touch git unless version/changelog are consistent; return the version."""
    problems = check(root)
    if problems:
        raise ReleaseError("Not release-ready:\n  - " + "\n  - ".join(problems))
    return read_versions(root)[PYPROJECT_FILE]


def resolve_remote(root: Path) -> str:
    """The remote to push to: ``origin`` if present, else the only remote there is."""
    remotes = _git(root, "remote").split()
    if not remotes:
        raise ReleaseError(
            "This repository has no git remote. Add your GitHub repository first: "
            "git remote add origin <url> (docs/publishing.md, section 1)."
        )
    if "origin" in remotes:
        return "origin"
    if len(remotes) == 1:
        return remotes[0]
    raise ReleaseError(
        f"Several remotes ({', '.join(remotes)}) and none is called 'origin'. Rename the one "
        f"to release to: git remote rename <name> origin"
    )


def commit_release(root: Path) -> str | None:
    """Commit only the release files that changed. Returns the commit message, or None."""
    _require_git_repo(root)
    version = _require_release_ready(root)
    changed = [
        name
        for name in RELEASE_FILES
        if (root / name).exists() and _git(root, "status", "--porcelain", "--", name)
    ]
    if not changed:
        return None
    message = f"Release {version}"
    _git(root, "add", "--", *changed)
    # The pathspec limits the commit to these files even if the user staged others.
    _git(root, "commit", "-m", message, "--", *changed)
    return message


def tag_release(root: Path) -> str:
    """Create the annotated tag ``vX.Y.Z`` for the current commit. Returns the tag name."""
    _require_git_repo(root)
    version = _require_release_ready(root)
    dirty = _git(root, "diff", "--name-only", "HEAD")
    if dirty:
        raise ReleaseError(
            "The working tree has uncommitted changes; commit or stash them first "
            "(`release.py commit` commits the release files only):\n" + dirty
        )
    if _git(root, "rev-parse", "--abbrev-ref", "HEAD") == "HEAD":
        raise ReleaseError("HEAD is detached; check out a branch before tagging.")
    tag = f"v{version}"
    try:
        _git(root, "rev-parse", "-q", "--verify", f"refs/tags/{tag}")
    except ReleaseError:
        pass
    else:
        raise ReleaseError(f"Tag {tag} already exists. Bump the version for a new release.")
    _git(root, "tag", "-a", tag, "-m", f"Release {version}")
    return tag


def push_release(root: Path, confirm: str) -> str:
    """Push the current branch and the release tag. Starts the PyPI publish workflow."""
    if confirm != "yes":
        raise ReleaseError("Push cancelled: confirmation was not 'yes'. Nothing was pushed.")
    _require_git_repo(root)
    version = _require_release_ready(root)
    tag = f"v{version}"
    head = _git(root, "rev-parse", "HEAD")
    try:
        tagged = _git(root, "rev-parse", f"refs/tags/{tag}^{{commit}}")
    except ReleaseError:
        raise ReleaseError(f"Tag {tag} does not exist; run `release.py tag` first.") from None
    if tagged != head:
        raise ReleaseError(f"Tag {tag} does not point at HEAD; commits were added after tagging.")
    remote = resolve_remote(root)
    # Two pushes on purpose. One `git push remote HEAD refs/tags/vX` updates each ref
    # independently, so a rejected branch push would still publish the tag - and the tag is what
    # starts the PyPI workflow. The tag only goes out once the branch is safely on the remote.
    _git(root, "push", remote, "HEAD")
    _git(root, "push", remote, f"refs/tags/{tag}")
    return tag


def verify(root: Path, steps: list[tuple[str, list[str]]] | None = None) -> None:
    """Run the quality gate; raise on the first failing step (later steps do not run)."""
    steps = VERIFY_STEPS if steps is None else steps
    for number, (name, argv) in enumerate(steps, start=1):
        print(f"\n==> [{number}/{len(steps)}] {name}", flush=True)
        if subprocess.run(argv, cwd=root).returncode != 0:
            raise ReleaseError(f"Verification failed at step '{name}'. Nothing was released.")
    print(f"\nAll {len(steps)} verification steps passed.")


def preflight(root: Path) -> str:
    """Everything that can be checked *before* the release changes anything. Returns the tag."""
    _require_git_repo(root)
    version = _require_release_ready(root)
    tag = f"v{version}"
    # Names only (no status prefix to mis-parse). Tracked files changed vs HEAD, staged or not.
    stray = [
        name
        for name in _git(root, "diff", "--name-only", "HEAD").splitlines()
        if name not in RELEASE_FILES
    ]
    if stray:
        raise ReleaseError(
            "Uncommitted changes outside the release files would block the tag; commit or "
            "stash them first:\n" + "\n".join(stray)
        )
    # A new file that was never `git add`ed would be missing from the tagged commit - for example
    # the release workflow itself, in which case pushing the tag would publish nothing.
    untracked = _git(root, "ls-files", "--others", "--exclude-standard").splitlines()
    if untracked:
        raise ReleaseError(
            "Untracked files would be missing from the release; `git add` and commit them, or "
            "add them to .gitignore:\n" + "\n".join(untracked)
        )
    if _git(root, "rev-parse", "--abbrev-ref", "HEAD") == "HEAD":
        raise ReleaseError("HEAD is detached; check out a branch before releasing.")
    try:
        _git(root, "rev-parse", "-q", "--verify", f"refs/tags/{tag}")
    except ReleaseError:
        pass
    else:
        raise ReleaseError(f"Tag {tag} already exists. Bump the version for a new release.")
    resolve_remote(root)
    return tag


def publish(root: Path, confirm: str, steps: list[tuple[str, list[str]]] | None = None) -> str:
    """verify -> commit -> tag -> push. Stops at the first problem; returns the pushed tag."""
    if confirm != "yes":
        raise ReleaseError("Cancelled: confirmation was not 'yes'. Nothing was changed.")
    tag = preflight(root)  # cheap checks first, before anything runs or changes
    print(f"Releasing {tag}.")
    verify(root, steps)
    print("\n==> commit")
    message = commit_release(root)
    print(f"Committed: {message}" if message else "Release files already committed.")
    print("\n==> tag")
    print(f"Created tag {tag_release(root)}")
    print("\n==> push")
    try:
        push_release(root, confirm)
    except ReleaseError as exc:
        raise ReleaseError(
            f"{exc}\nThe release commit and tag {tag} exist locally but were not pushed. Fix the "
            f"cause (for example `git pull --rebase`). If HEAD did not change, run "
            f"`release.py push --confirm yes`; otherwise `git tag -d {tag}` and publish again."
        ) from None
    return tag


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)
    p_check = sub.add_parser("check", help="verify versions, changelog and (optionally) the tag")
    p_check.add_argument("--tag", help="a git tag such as v0.1.0 that must match the version")
    p_notes = sub.add_parser("notes", help="print the changelog section for a version")
    p_notes.add_argument("version", nargs="?", help="default: the current package version")
    p_bump = sub.add_parser("bump", help="bump the version everywhere and add a changelog stub")
    p_bump.add_argument("target", help="patch | minor | major | X.Y.Z")
    p_bump.add_argument("--dry-run", action="store_true", help="show the result, change nothing")
    sub.add_parser("build", help="clean build + twine check")
    sub.add_parser("commit", help="commit only the release files (version, changelog, lock)")
    sub.add_parser("tag", help="create the annotated tag vX.Y.Z (clean tree required)")
    p_push = sub.add_parser("push", help="push branch + tag; this starts the PyPI workflow")
    p_push.add_argument("--confirm", default="no", help="must be exactly 'yes'")
    sub.add_parser("preflight", help="read-only: could a release start right now? (git state)")
    sub.add_parser("verify", help="lint, format, tests, docs, version check, build")
    p_pub = sub.add_parser("publish", help="verify -> commit -> tag -> push (starts PyPI publish)")
    p_pub.add_argument("--confirm", default="no", help="must be exactly 'yes'")
    args = parser.parse_args(argv)

    try:
        if args.command == "check":
            problems = check(ROOT, args.tag)
            for problem in problems:
                print(f"error: {problem}", file=sys.stderr)
            if not problems:
                print(f"OK: ready to release {read_versions(ROOT)[PYPROJECT_FILE]}")
            return 1 if problems else 0
        if args.command == "notes":
            version = args.version or read_versions(ROOT)[PYPROJECT_FILE]
            section = changelog_section((ROOT / CHANGELOG_FILE).read_text("utf-8"), version)
            if section is None:
                raise ReleaseError(f"No [{version}] section in {CHANGELOG_FILE}.")
            print(section.strip())
            return 0
        if args.command == "bump":
            old, new = bump(ROOT, args.target, dt.date.today(), args.dry_run)
            verb = "Would bump" if args.dry_run else "Bumped"
            print(f"{verb} {old} -> {new}")
            if not args.dry_run:
                print(
                    f"\nNext:\n  1. Edit {CHANGELOG_FILE}: replace the TODO under [{new}].\n"
                    f"  2. python scripts/release.py check\n"
                    f"  3. VS Code: Terminal > Run Task > 'Release 7: PUBLISH', or\n"
                    f"     python scripts/release.py publish --confirm yes"
                )
            return 0
        if args.command == "build":
            for artifact in build(ROOT):
                print(f"built {artifact.relative_to(ROOT)}")
            return 0
        if args.command == "commit":
            message = commit_release(ROOT)
            print(
                f"Committed: {message}"
                if message
                else "Nothing to commit (release files unchanged)."
            )
            return 0
        if args.command == "tag":
            print(f"Created tag {tag_release(ROOT)}")
            return 0
        if args.command == "push":
            tag = push_release(ROOT, args.confirm)
            print(f"Pushed branch and {tag}. Watch the 'Release' workflow in GitHub Actions.")
            return 0
        if args.command == "preflight":
            tag = preflight(ROOT)
            remote = resolve_remote(ROOT)
            url = _git(ROOT, "remote", "get-url", remote)
            branch = _git(ROOT, "rev-parse", "--abbrev-ref", "HEAD")
            print(
                f"Preflight OK: {tag} would be released from branch '{branch}' to {remote} ({url})."
            )
            return 0
        if args.command == "verify":
            verify(ROOT)
            return 0
        if args.command == "publish":
            tag = publish(ROOT, args.confirm)
            print(
                f"\nPushed {tag}. GitHub Actions now builds and publishes to PyPI - watch the "
                f"'Release' workflow (approve the 'pypi' environment if you set reviewers)."
            )
            return 0
    except (ReleaseError, subprocess.CalledProcessError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 2


if __name__ == "__main__":
    sys.exit(main())
