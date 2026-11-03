"""Releases a new version: checks, tests, bumps the version, tags, pushes, and waits for the build.

    python scripts/release.py patch      # 1.0.0 -> 1.0.1
    python scripts/release.py minor      # 1.0.0 -> 1.1.0
    python scripts/release.py 2.0.0      # explicit version

Options: --quick (tests on the 2 newest rides only), --skip-tests, --yes (no confirmation),
--no-wait (don't wait for the GitHub build).

Pushing the tag starts .github/workflows/release.yml, which builds the programs for
Windows, macOS and Linux and attaches them to the GitHub release.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION_FILE = ROOT / "lib" / "__init__.py"
REPO = "leemmoox/zwift-garmin-sync"
API = f"https://api.github.com/repos/{REPO}"


def git(*args, capture=True):
    result = subprocess.run(["git", *args], cwd=ROOT, text=True, capture_output=capture)
    if result.returncode != 0:
        sys.exit(f"git {' '.join(args)} failed:\n{result.stderr or ''}")
    return result.stdout.strip() if capture else ""


def current_version():
    match = re.search(r'__version__ = "(\d+)\.(\d+)\.(\d+)"', VERSION_FILE.read_text())
    if not match:
        sys.exit(f"No __version__ found in {VERSION_FILE}")
    return tuple(int(part) for part in match.groups())


def next_version(current, requested):
    major, minor, patch = current
    if requested == "major":
        return major + 1, 0, 0
    if requested == "minor":
        return major, minor + 1, 0
    if requested == "patch":
        return major, minor, patch + 1
    match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", requested)
    if not match:
        sys.exit(f"Version must be major, minor, patch or X.Y.Z, not '{requested}'")
    version = tuple(int(part) for part in match.groups())
    if version <= current:
        sys.exit(f"New version {fmt(version)} must be higher than the current {fmt(current)}")
    return version


def fmt(version):
    return ".".join(str(part) for part in version)


def check_repository(tag):
    if git("rev-parse", "--abbrev-ref", "HEAD") != "main":
        sys.exit("Switch to the main branch first.")
    if git("status", "--porcelain"):
        sys.exit("Commit or stash your changes first (git status shows uncommitted changes).")
    git("fetch", "--quiet", "--tags", "origin")
    if git("rev-parse", "HEAD") != git("rev-parse", "origin/main"):
        sys.exit("main is not the same as on GitHub. Run git pull / git push first.")
    if git("tag", "--list", tag):
        sys.exit(f"Tag {tag} already exists.")


def run_tests(quick):
    env = dict(os.environ, **({"ZG_TEST_MAX_RIDES": "2"} if quick else {}))
    print(f"\nRunning tests{' (2 newest rides)' if quick else ''}...")
    if subprocess.run([sys.executable, "-m", "pytest", "-q"], cwd=ROOT, env=env).returncode != 0:
        sys.exit("Tests failed, nothing was released.")


def github(path):
    with urllib.request.urlopen(urllib.request.Request(f"{API}{path}", headers={"Accept": "application/vnd.github+json"})) as response:
        return json.load(response)


def wait_for_build(tag, timeout_min=30):
    """Waits for the release workflow of the tag and reports each platform's result."""
    print(f"\nWaiting for the build on GitHub: https://github.com/{REPO}/actions")
    deadline = time.time() + timeout_min * 60
    run = None
    while time.time() < deadline:
        time.sleep(30)
        runs = github(f"/actions/runs?event=push&branch={tag}&per_page=1").get("workflow_runs", [])
        if runs:
            run = runs[0]
            print(f"  {run['status']}...")
            if run["status"] == "completed":
                break
    if not run or run["status"] != "completed":
        print("The build is still running; check the Actions page.")
        return False

    for job in github(f"/actions/runs/{run['id']}/jobs?per_page=100")["jobs"]:
        failed = [step["name"] for step in job["steps"] if step["conclusion"] not in ("success", "skipped")]
        print(f"  {'✅' if job['conclusion'] == 'success' else '❌'} {job['name']}" + (f" (failed: {', '.join(failed)})" if failed else ""))
    if run["conclusion"] != "success":
        print(f"\n❌ Build failed: {run['html_url']}")
        return False
    print(f"\n✅ Released: https://github.com/{REPO}/releases/tag/{tag}")
    return True


def main():
    parser = argparse.ArgumentParser(description="Release a new version of zwift-garmin-sync.")
    parser.add_argument("version", help="major, minor, patch, or an explicit X.Y.Z")
    parser.add_argument("--quick", action="store_true", help="run tests on the 2 newest rides only")
    parser.add_argument("--skip-tests", action="store_true")
    parser.add_argument("--yes", action="store_true", help="don't ask before pushing")
    parser.add_argument("--no-wait", action="store_true", help="don't wait for the GitHub build")
    args = parser.parse_args()

    current = current_version()
    version = next_version(current, args.version)
    tag = f"v{fmt(version)}"
    print(f"Releasing {fmt(current)} -> {fmt(version)} ({tag})")

    check_repository(tag)
    if not args.skip_tests:
        run_tests(args.quick)

    if not args.yes and input(f"\nCommit, tag and push {tag} to GitHub? [y/N] ").strip().lower() != "y":
        sys.exit("Cancelled, nothing was changed.")

    VERSION_FILE.write_text(re.sub(r'__version__ = "[^"]+"', f'__version__ = "{fmt(version)}"', VERSION_FILE.read_text()))
    git("commit", "--quiet", "-am", f"Release {tag}")
    git("tag", "-a", tag, "-m", f"zwift-garmin-sync {fmt(version)}")
    git("push", "--quiet", "origin", "main", tag)
    print(f"Pushed {tag}.")

    if not args.no_wait:
        sys.exit(0 if wait_for_build(tag) else 1)


if __name__ == "__main__":
    main()
