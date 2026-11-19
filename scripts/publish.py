#!/usr/bin/env python
"""Publish the repository to GitHub and archive a release on Zenodo.

Credentials are read from the environment and never written to disk:
    GITHUB_TOKEN   a GitHub personal access token with 'repo' scope (or fine-grained
                   token with Contents=write and Metadata=read for this repository)
    ZENODO_TOKEN   a Zenodo personal access token with deposit:write scope

Optional environment:
    GITHUB_USER    your GitHub login (auto-detected from the token if omitted)
    REPO_NAME      repository name (default: cadcam)
    ZENODO_SANDBOX set to 1 to use sandbox.zenodo.org for a dry run

Usage:
    export GITHUB_TOKEN=ghp_...
    export ZENODO_TOKEN=...
    PYTHONPATH=src python scripts/publish.py            # GitHub + Zenodo
    PYTHONPATH=src python scripts/publish.py --github-only
    PYTHONPATH=src python scripts/publish.py --zenodo-only
    PYTHONPATH=src python scripts/publish.py --dry-run  # show plan, no network writes

The script is idempotent: an existing repository or an already-published Zenodo
deposition is detected and reported rather than duplicated.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
TITLE = (
    "CAD2CAM: A Verification-Grounded Agent Architecture for Spatial and "
    "Manufacturing Modalities"
)
DESCRIPTION = (
    "Verification-grounded agent architecture that connects foundation models to "
    "CAD/CAM through typed tool contracts, region-masked toolpaths and an "
    "independently auditable material-removal verifier."
)
CREATOR = {"name": "Chen, Weiqiang"}
LICENSE_ID = "apache-2.0"


def sh(cmd: list[str], *, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=ROOT, check=check, text=True, capture_output=True)


def github_user(token: str) -> str:
    override = os.getenv("GITHUB_USER")
    if override:
        return override
    r = requests.get(
        "https://api.github.com/user",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()["login"]


def ensure_repo(token: str, user: str, repo: str, dry: bool) -> str:
    if dry:
        print(f"GitHub: [dry-run] would ensure public repository {user}/{repo}")
        return f"https://github.com/{user}/{repo}"

    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
    api = f"https://api.github.com/repos/{user}/{repo}"
    r = requests.get(api, headers=headers, timeout=30)
    if r.status_code == 200:
        print(f"GitHub: repository {user}/{repo} already exists")
        return r.json()["html_url"]
    if r.status_code != 404:
        r.raise_for_status()
    payload = {
        "name": repo,
        "description": DESCRIPTION,
        "homepage": "",
        "private": False,
        "has_issues": True,
        "has_wiki": False,
    }
    r = requests.post("https://api.github.com/user/repos", headers=headers, json=payload, timeout=30)
    r.raise_for_status()
    print(f"GitHub: created public repository {user}/{repo}")
    return r.json()["html_url"]


def push_repo(token: str, user: str, repo: str, dry: bool) -> None:
    if dry:
        print("[dry-run] would push master and v0.1.0 to GitHub")
        return
    remote = f"https://x-access-token:{token}@github.com/{user}/{repo}.git"
    existing = sh(["git", "remote", "get-url", "origin"], check=False)
    if existing.returncode == 0:
        sh(["git", "remote", "set-url", "origin", remote])
    else:
        sh(["git", "remote", "add", "origin", remote])
    # Do not print the remote: it embeds the token.
    sh(["git", "push", "-u", "origin", "master"])
    sh(["git", "push", "origin", "v0.1.0"])
    # Strip the token from the stored remote URL afterwards.
    sh(["git", "remote", "set-url", "origin", f"https://github.com/{user}/{repo}.git"])
    print("GitHub: pushed master and tag v0.1.0")


def make_archive(path: Path) -> Path:
    """A clean source archive: tracked files only, no .git, no build artefacts."""
    out = ROOT / "build" / "cadcam-v0.1.0-source.zip"
    out.parent.mkdir(parents=True, exist_ok=True)
    tracked = sh(["git", "ls-files", "-z"]).stdout.split("\0")
    files = [ROOT / f for f in tracked if f]
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for f in files:
            if f.exists():
                z.write(f, Path("cadcam-v0.1.0") / f.relative_to(ROOT))
    return out


def zenodo_base() -> str:
    return "https://sandbox.zenodo.org" if os.getenv("ZENODO_SANDBOX") == "1" else "https://zenodo.org"


def zenodo_deposit(token: str, repo_url: str, dry: bool) -> tuple[str, str]:
    base = zenodo_base()
    params = {"access_token": token}
    meta = {
        "metadata": {
            "title": TITLE,
            "description": DESCRIPTION,
            "creators": [CREATOR],
            "upload_type": "software",
            "access_right": "open",
            "license": LICENSE_ID,
            "version": "v0.1.0",
            "keywords": [
                "foundation models", "CAD", "CAM", "tool use", "verification",
                "multimodal agents", "manufacturing",
            ],
            "related_identifiers": [
                {
                    "identifier": repo_url,
                    "relation": "isSupplementTo",
                    "scheme": "url",
                }
            ],
        }
    }
    if dry:
        print(f"Zenodo: [dry-run] would create + publish deposition at {base}")
        return "", ""

    # Look for an existing deposition with the same title before creating a new one.
    r = requests.get(f"{base}/api/deposit/depositions", params=params, timeout=30)
    r.raise_for_status()
    for dep in r.json():
        if dep.get("metadata", {}).get("title") == TITLE:
            print(f"Zenodo: deposition {dep['id']} already exists (doi: {dep.get('doi', 'pending')})")
            return dep.get("doi", ""), dep.get("links", {}).get("html", "")

    r = requests.post(f"{base}/api/deposit/depositions", params=params, json=meta, timeout=30)
    r.raise_for_status()
    dep = r.json()
    dep_id = dep["id"]
    bucket = dep["links"]["bucket"]

    archive = make_archive(archive_path := ROOT / "build" / "cadcam-v0.1.0-source.zip")
    with archive.open("rb") as fh:
        r = requests.put(
            f"{bucket}/{archive.name}",
            params=params,
            data=fh,
            headers={"Content-Type": "application/zip"},
            timeout=300,
        )
        r.raise_for_status()

    pdf = ROOT / "latex" / "main.pdf"
    if pdf.exists():
        with pdf.open("rb") as fh:
            r = requests.put(
                f"{bucket}/manuscript.pdf",
                params=params,
                data=fh,
                headers={"Content-Type": "application/pdf"},
                timeout=120,
            )
            r.raise_for_status()

    r = requests.post(f"{base}/api/deposit/depositions/{dep_id}/actions/publish", params=params, timeout=60)
    r.raise_for_status()
    published = r.json()
    doi = published.get("doi", "")
    html = published.get("links", {}).get("html", "")
    print(f"Zenodo: published deposition {dep_id}, DOI {doi}")
    return doi, html


def backfill(repo_url: str, doi: str, dry: bool) -> None:
    """Replace the two placeholders in the data-availability statement."""
    p = ROOT / "latex" / "sections" / "05-backmatter.tex"
    s = p.read_text(encoding="utf-8")
    s = s.replace("[REPOSITORY URL]", repo_url)
    if doi:
        s = s.replace("[ZENODO DOI]", f"https://doi.org/{doi}")
    if dry:
        print("[dry-run] would backfill repository URL and DOI into the manuscript")
        return
    p.write_text(s, encoding="utf-8")
    print("Manuscript: backfilled repository URL" + (" and DOI" if doi else ""))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--github-only", action="store_true")
    ap.add_argument("--zenodo-only", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    dry = args.dry_run
    do_gh = not args.zenodo_only
    do_zen = not args.github_only

    gh_token = os.getenv("GITHUB_TOKEN", "")
    zen_token = os.getenv("ZENODO_TOKEN", "")
    repo = os.getenv("REPO_NAME", "cadcam")

    repo_url = ""
    if do_gh:
        if not gh_token and not dry:
            print("ERROR: set GITHUB_TOKEN before running (see script docstring).", file=sys.stderr)
            return 2
        user = github_user(gh_token) if gh_token else "YOUR_USER"
        repo_url = ensure_repo(gh_token, user, repo, dry)
        if gh_token:
            push_repo(gh_token, user, repo, dry)
    else:
        user = os.getenv("GITHUB_USER", "YOUR_USER")
        repo_url = f"https://github.com/{user}/{repo}"

    doi = ""
    if do_zen:
        if not zen_token and not dry:
            print("ERROR: set ZENODO_TOKEN before running (see script docstring).", file=sys.stderr)
            return 2
        doi, _ = zenodo_deposit(zen_token, repo_url, dry)

    if not dry:
        backfill(repo_url, doi, False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
