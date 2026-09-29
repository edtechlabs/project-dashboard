# Git Project Dashboard

A cross-platform PySide6 desktop dashboard for a folder containing many Git projects.

## What it does

- Choose any development root folder.
- Discover Git repositories recursively to a configurable depth.
- Shows, per repository:
  - current branch
  - clean / dirty / conflicts
  - staged, modified and untracked counts
  - ahead / behind upstream
  - upstream branch
  - `origin` remote URL
  - latest commit/date
  - stash count
  - local path
- Optional `git fetch --all --prune` during refresh for accurate ahead/behind state.
- Select repositories with checkboxes.
- **Safe pull** only pulls repositories that are:
  - clean
  - conflict-free
  - tracking an upstream
  - actually behind upstream
- Optional **FF-only pull** (`git pull --ff-only`).
- Dirty repositories are never auto-reset, auto-cleaned, or auto-stashed.
- Context menu:
  - open folder
  - open terminal
  - copy/open remote URL
  - refresh/fetch one repo
  - detailed `git status`
  - show stash list
- Remembers your selected development folder and scan settings.

## Why pulls are conservative

A tool that automatically “cleans” dirty repositories can delete work. This app instead makes dirty/conflicted repositories obvious and skips them during bulk pulls. You decide how to resolve, commit, or stash those changes.

## Run on macOS / Linux

```bash
./run-macos-linux.sh
```

Or manually:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python git_dashboard.py
```

## Run on Windows

Double-click `run-windows.bat` to start the dashboard. It runs the PowerShell
script with a process-only execution-policy bypass and creates `.venv` when it
does not exist.

PowerShell:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\run-windows.ps1
```

## Requirements

- Python 3.10+
- Git available in `PATH`
- PySide6

## Suggested next features

The current architecture is ready for additions such as:

- repository groups / favorites
- clone a repository into the selected dev folder
- per-repo notes/tags
- detect GitHub/GitLab/Bitbucket provider
- buttons for Issues / Actions / CI / Pull Requests
- local service endpoint detection (`package.json`, Docker Compose, `.env`, Vite/Next/Flask/Django defaults)
- dependency freshness checks
- default branch / remote branch overview
- submodule status
- worktree overview
- batch `git gc`
- scheduled health checks
- export repository inventory to CSV/JSON
- system tray summary
- native packaging with PyInstaller
