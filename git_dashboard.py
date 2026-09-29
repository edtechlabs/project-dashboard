from __future__ import annotations

import os
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QObject, QRunnable, QSettings, Qt, QThreadPool, Signal, Slot, QUrl
from PySide6.QtGui import QAction, QColor, QDesktopServices, QFont
from PySide6.QtWidgets import (
    QApplication,
    QAbstractItemView,
    QCheckBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

APP_NAME = "Git Project Dashboard"
ORG_NAME = "Focsd"


def run_git(repo: Path, args: list[str], timeout: int = 25) -> tuple[int, str, str]:
    try:
        p = subprocess.run(
            ["git", "-C", str(repo), *args],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        return p.returncode, p.stdout.strip(), p.stderr.strip()
    except FileNotFoundError:
        return 127, "", "git executable not found"
    except subprocess.TimeoutExpired:
        return 124, "", "git command timed out"
    except Exception as e:
        return 1, "", str(e)


@dataclass
class RepoInfo:
    path: Path
    name: str = ""
    branch: str = ""
    tracking: str = ""
    dirty: bool = False
    staged: int = 0
    modified: int = 0
    untracked: int = 0
    conflicts: int = 0
    ahead: int = 0
    behind: int = 0
    remote: str = ""
    last_commit: str = ""
    last_commit_date: str = ""
    stash_count: int = 0
    error: str = ""

    @property
    def status_text(self) -> str:
        if self.error:
            return "ERROR"
        if self.conflicts:
            return f"CONFLICT ({self.conflicts})"
        if self.dirty:
            bits = []
            if self.staged:
                bits.append(f"{self.staged} staged")
            if self.modified:
                bits.append(f"{self.modified} modified")
            if self.untracked:
                bits.append(f"{self.untracked} untracked")
            return "DIRTY · " + ", ".join(bits)
        return "CLEAN"

    @property
    def sync_text(self) -> str:
        if not self.tracking:
            return "no upstream"
        if self.ahead == 0 and self.behind == 0:
            return "up to date"
        bits = []
        if self.ahead:
            bits.append(f"↑{self.ahead}")
        if self.behind:
            bits.append(f"↓{self.behind}")
        return " ".join(bits)


def inspect_repo(repo: Path, fetch: bool = False) -> RepoInfo:
    info = RepoInfo(path=repo, name=repo.name)

    code, out, err = run_git(repo, ["rev-parse", "--is-inside-work-tree"])
    if code != 0 or out.lower() != "true":
        info.error = err or "Not a Git work tree"
        return info

    if fetch:
        # Fetch is deliberately non-destructive and lets ahead/behind be accurate.
        run_git(repo, ["fetch", "--all", "--prune"], timeout=90)

    _, branch, _ = run_git(repo, ["branch", "--show-current"])
    if not branch:
        _, branch, _ = run_git(repo, ["rev-parse", "--short", "HEAD"])
        branch = f"detached@{branch}" if branch else "detached"
    info.branch = branch

    c, tracking, _ = run_git(repo, ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"])
    if c == 0:
        info.tracking = tracking
        c2, counts, _ = run_git(repo, ["rev-list", "--left-right", "--count", "HEAD...@{u}"])
        if c2 == 0 and counts:
            try:
                a, b = counts.split()
                info.ahead, info.behind = int(a), int(b)
            except ValueError:
                pass

    _, remote, _ = run_git(repo, ["remote", "get-url", "origin"])
    if not remote:
        _, remotes, _ = run_git(repo, ["remote", "-v"])
        if remotes:
            remote = remotes.splitlines()[0].split()[1]
    info.remote = remote

    _, porcelain, _ = run_git(repo, ["status", "--porcelain=v1"])
    if porcelain:
        for line in porcelain.splitlines():
            if len(line) < 2:
                continue
            xy = line[:2]
            if xy == "??":
                info.untracked += 1
                continue
            if "U" in xy or xy in {"AA", "DD"}:
                info.conflicts += 1
            if xy[0] not in (" ", "?"):
                info.staged += 1
            if xy[1] not in (" ", "?"):
                info.modified += 1
        info.dirty = True

    _, logline, _ = run_git(repo, ["log", "-1", "--pretty=format:%h%x09%ad%x09%s", "--date=short"])
    if logline:
        parts = logline.split("\t", 2)
        if len(parts) == 3:
            h, d, s = parts
            info.last_commit = f"{h} · {s}"
            info.last_commit_date = d
        else:
            info.last_commit = logline

    _, stashes, _ = run_git(repo, ["stash", "list"])
    info.stash_count = len(stashes.splitlines()) if stashes else 0
    return info


def discover_repos(root: Path, max_depth: int) -> list[Path]:
    root = root.resolve()
    repos: list[Path] = []
    seen: set[Path] = set()

    if (root / ".git").exists():
        return [root]

    for current, dirs, _files in os.walk(root):
        current_path = Path(current)
        try:
            rel_depth = len(current_path.relative_to(root).parts)
        except ValueError:
            continue

        dirs[:] = [d for d in dirs if d not in {"node_modules", ".venv", "venv", "dist", "build", ".idea", ".gradle", "Pods"}]

        if ".git" in dirs or (current_path / ".git").is_file():
            p = current_path.resolve()
            if p not in seen:
                seen.add(p)
                repos.append(p)
            if ".git" in dirs:
                dirs.remove(".git")
            # Don't recurse into a repo looking for nested repos by default.
            dirs[:] = []
            continue

        if rel_depth >= max_depth:
            dirs[:] = []

    return sorted(repos, key=lambda p: p.name.lower())


class WorkerSignals(QObject):
    result = Signal(object)
    message = Signal(str)
    progress = Signal(int, int)
    finished = Signal()


class ScanWorker(QRunnable):
    def __init__(self, root: Path, depth: int, fetch: bool):
        super().__init__()
        self.root = root
        self.depth = depth
        self.fetch = fetch
        self.signals = WorkerSignals()

    @Slot()
    def run(self):
        repos = discover_repos(self.root, self.depth)
        total = len(repos)
        self.signals.message.emit(f"Found {total} repositories")
        for i, repo in enumerate(repos, 1):
            self.signals.message.emit(f"Inspecting {repo.name}…")
            self.signals.result.emit(inspect_repo(repo, fetch=self.fetch))
            self.signals.progress.emit(i, total)
        self.signals.finished.emit()


class CommandWorker(QRunnable):
    def __init__(self, repo: Path, args: list[str], label: str, timeout: int = 120):
        super().__init__()
        self.repo = repo
        self.args = args
        self.label = label
        self.timeout = timeout
        self.signals = WorkerSignals()

    @Slot()
    def run(self):
        self.signals.message.emit(f"{self.label}: {self.repo.name}")
        code, out, err = run_git(self.repo, self.args, timeout=self.timeout)
        self.signals.result.emit((self.repo, self.args, code, out, err))
        self.signals.finished.emit()


class GitDashboard(QMainWindow):
    COL_SELECT = 0
    COL_REPO = 1
    COL_BRANCH = 2
    COL_STATUS = 3
    COL_SYNC = 4
    COL_UPSTREAM = 5
    COL_REMOTE = 6
    COL_LAST = 7
    COL_STASH = 8
    COL_PATH = 9

    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(1500, 850)
        self.settings = QSettings(ORG_NAME, APP_NAME)
        self.pool = QThreadPool.globalInstance()
        self.repo_infos: dict[str, RepoInfo] = {}
        self.pending_commands = 0
        self._build_ui()
        self._restore()

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        outer = QVBoxLayout(central)

        title_row = QHBoxLayout()
        title = QLabel("Git Project Dashboard")
        f = QFont()
        f.setPointSize(18)
        f.setBold(True)
        title.setFont(f)
        title_row.addWidget(title)
        title_row.addStretch()
        self.summary = QLabel("No folder selected")
        title_row.addWidget(self.summary)
        outer.addLayout(title_row)

        folder_row = QHBoxLayout()
        self.folder_label = QLabel("Dev folder: —")
        self.folder_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        folder_row.addWidget(self.folder_label, 1)
        choose = QPushButton("Choose folder…")
        choose.clicked.connect(self.choose_folder)
        folder_row.addWidget(choose)
        self.depth = QSpinBox()
        self.depth.setRange(1, 8)
        self.depth.setValue(3)
        self.depth.setPrefix("Scan depth: ")
        folder_row.addWidget(self.depth)
        self.fetch_on_refresh = QCheckBox("Fetch before status")
        self.fetch_on_refresh.setToolTip("Runs git fetch --all --prune so ahead/behind is current. Slower on many repositories.")
        folder_row.addWidget(self.fetch_on_refresh)
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self.refresh)
        folder_row.addWidget(refresh)
        outer.addLayout(folder_row)

        actions = QHBoxLayout()
        self.select_all_btn = QPushButton("Select all")
        self.select_all_btn.clicked.connect(lambda: self.set_all_checks(True))
        actions.addWidget(self.select_all_btn)
        self.select_none_btn = QPushButton("Select none")
        self.select_none_btn.clicked.connect(lambda: self.set_all_checks(False))
        actions.addWidget(self.select_none_btn)
        actions.addSpacing(16)

        self.fetch_btn = QPushButton("Fetch selected")
        self.fetch_btn.clicked.connect(self.fetch_selected)
        actions.addWidget(self.fetch_btn)
        self.pull_btn = QPushButton("Safe pull selected")
        self.pull_btn.clicked.connect(self.pull_selected)
        self.pull_btn.setToolTip("Pulls only clean repositories with a configured upstream. Dirty/conflicted repositories are skipped.")
        actions.addWidget(self.pull_btn)
        self.ff_btn = QPushButton("FF-only pull")
        self.ff_btn.clicked.connect(lambda: self.pull_selected(ff_only=True))
        self.ff_btn.setToolTip("Runs git pull --ff-only on eligible clean repositories.")
        actions.addWidget(self.ff_btn)
        actions.addStretch()
        outer.addLayout(actions)

        splitter = QSplitter(Qt.Vertical)
        self.table = QTableWidget(0, 10)
        self.table.setHorizontalHeaderLabels([
            "Use", "Repository", "Branch", "Working tree", "Sync", "Upstream", "Origin / remote", "Last commit", "Stashes", "Path"
        ])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSortingEnabled(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.cellDoubleClicked.connect(self.double_click_row)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self.open_context_menu)

        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(self.COL_SELECT, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(self.COL_REPO, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(self.COL_BRANCH, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(self.COL_STATUS, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(self.COL_SYNC, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(self.COL_UPSTREAM, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(self.COL_REMOTE, QHeaderView.Stretch)
        hdr.setSectionResizeMode(self.COL_LAST, QHeaderView.Stretch)
        hdr.setSectionResizeMode(self.COL_STASH, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(self.COL_PATH, QHeaderView.Stretch)

        splitter.addWidget(self.table)

        log_box = QWidget()
        log_layout = QVBoxLayout(log_box)
        log_header = QHBoxLayout()
        log_header.addWidget(QLabel("Activity / Git output"))
        log_header.addStretch()
        clear = QPushButton("Clear log")
        clear.clicked.connect(lambda: self.log.clear())
        log_header.addWidget(clear)
        log_layout.addLayout(log_header)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(3000)
        log_layout.addWidget(self.log)
        splitter.addWidget(log_box)
        splitter.setSizes([600, 200])
        outer.addWidget(splitter, 1)

        status_row = QHBoxLayout()
        self.status = QLabel("Ready")
        status_row.addWidget(self.status, 1)
        self.progress = QProgressBar()
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.progress.setMaximumWidth(320)
        status_row.addWidget(self.progress)
        outer.addLayout(status_row)

    def _restore(self):
        folder = self.settings.value("dev_folder", "")
        depth = int(self.settings.value("depth", 3))
        fetch = str(self.settings.value("fetch_on_refresh", "false")).lower() == "true"
        self.depth.setValue(depth)
        self.fetch_on_refresh.setChecked(fetch)
        if folder and Path(folder).exists():
            self.dev_folder = Path(folder)
            self.folder_label.setText(f"Dev folder: {self.dev_folder}")
            self.refresh()
        else:
            self.dev_folder: Optional[Path] = None

    def closeEvent(self, event):
        if self.dev_folder:
            self.settings.setValue("dev_folder", str(self.dev_folder))
        self.settings.setValue("depth", self.depth.value())
        self.settings.setValue("fetch_on_refresh", self.fetch_on_refresh.isChecked())
        super().closeEvent(event)

    def append_log(self, text: str):
        if text:
            self.log.appendPlainText(text)

    def choose_folder(self):
        start = str(self.dev_folder) if self.dev_folder else str(Path.home())
        selected = QFileDialog.getExistingDirectory(self, "Choose development folder", start)
        if selected:
            self.dev_folder = Path(selected)
            self.folder_label.setText(f"Dev folder: {self.dev_folder}")
            self.settings.setValue("dev_folder", selected)
            self.refresh()

    def refresh(self):
        if not self.dev_folder or not self.dev_folder.exists():
            self.choose_folder()
            return
        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)
        self.repo_infos.clear()
        self.summary.setText("Scanning…")
        self.progress.setRange(0, 0)
        self.status.setText("Discovering Git repositories…")
        worker = ScanWorker(self.dev_folder, self.depth.value(), self.fetch_on_refresh.isChecked())
        worker.signals.result.connect(self.add_repo)
        worker.signals.message.connect(self.status.setText)
        worker.signals.progress.connect(self.scan_progress)
        worker.signals.finished.connect(self.scan_finished)
        self.pool.start(worker)

    @Slot(int, int)
    def scan_progress(self, current: int, total: int):
        self.progress.setRange(0, max(total, 1))
        self.progress.setValue(current)

    @Slot(object)
    def add_repo(self, info: RepoInfo):
        key = str(info.path)
        self.repo_infos[key] = info
        row = self.table.rowCount()
        self.table.insertRow(row)

        chk = QTableWidgetItem()
        chk.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
        chk.setCheckState(Qt.Checked)
        chk.setData(Qt.UserRole, key)
        self.table.setItem(row, self.COL_SELECT, chk)

        def put(col: int, text: str, tooltip: str = ""):
            item = QTableWidgetItem(text)
            item.setData(Qt.UserRole, key)
            if tooltip:
                item.setToolTip(tooltip)
            self.table.setItem(row, col, item)
            return item

        put(self.COL_REPO, info.name)
        put(self.COL_BRANCH, info.branch)
        status_item = put(self.COL_STATUS, info.status_text, info.error)
        put(self.COL_SYNC, info.sync_text)
        put(self.COL_UPSTREAM, info.tracking or "—")
        put(self.COL_REMOTE, info.remote or "—")
        last = info.last_commit
        if info.last_commit_date:
            last = f"{info.last_commit_date} · {last}"
        put(self.COL_LAST, last or "—")
        put(self.COL_STASH, str(info.stash_count))
        put(self.COL_PATH, str(info.path))

        if info.error:
            status_item.setForeground(QColor("#d32f2f"))
        elif info.conflicts:
            status_item.setForeground(QColor("#b71c1c"))
            status_item.setFont(self._bold_font(status_item.font()))
        elif info.dirty:
            status_item.setForeground(QColor("#d97706"))
            status_item.setFont(self._bold_font(status_item.font()))
        else:
            status_item.setForeground(QColor("#16803a"))

        sync_item = self.table.item(row, self.COL_SYNC)
        if info.behind:
            sync_item.setForeground(QColor("#1565c0"))
            sync_item.setFont(self._bold_font(sync_item.font()))
        elif info.ahead:
            sync_item.setForeground(QColor("#7b1fa2"))

    @staticmethod
    def _bold_font(font: QFont) -> QFont:
        f = QFont(font)
        f.setBold(True)
        return f

    @Slot()
    def scan_finished(self):
        self.table.setSortingEnabled(True)
        self.progress.setRange(0, 1)
        self.progress.setValue(1)
        infos = list(self.repo_infos.values())
        dirty = sum(1 for i in infos if i.dirty)
        conflicts = sum(1 for i in infos if i.conflicts)
        behind = sum(1 for i in infos if i.behind)
        no_upstream = sum(1 for i in infos if not i.tracking and not i.error)
        self.summary.setText(
            f"{len(infos)} repos · {dirty} dirty · {behind} behind · {conflicts} conflicts · {no_upstream} no upstream"
        )
        self.status.setText("Ready")

    def set_all_checks(self, checked: bool):
        state = Qt.Checked if checked else Qt.Unchecked
        for row in range(self.table.rowCount()):
            item = self.table.item(row, self.COL_SELECT)
            if item:
                item.setCheckState(state)

    def checked_infos(self) -> list[RepoInfo]:
        result = []
        for row in range(self.table.rowCount()):
            item = self.table.item(row, self.COL_SELECT)
            if item and item.checkState() == Qt.Checked:
                key = item.data(Qt.UserRole)
                info = self.repo_infos.get(key)
                if info:
                    result.append(info)
        return result

    def selected_row_info(self) -> Optional[RepoInfo]:
        row = self.table.currentRow()
        if row < 0:
            return None
        item = self.table.item(row, self.COL_REPO)
        if not item:
            return None
        return self.repo_infos.get(item.data(Qt.UserRole))

    def fetch_selected(self):
        infos = self.checked_infos()
        if not infos:
            QMessageBox.information(self, APP_NAME, "No repositories are checked.")
            return
        for info in infos:
            self.start_command(info.path, ["fetch", "--all", "--prune"], "Fetch")

    def pull_selected(self, ff_only: bool = False):
        infos = self.checked_infos()
        if not infos:
            QMessageBox.information(self, APP_NAME, "No repositories are checked.")
            return

        eligible = []
        skipped_dirty = []
        skipped_upstream = []
        skipped_conflict = []
        for info in infos:
            if info.conflicts:
                skipped_conflict.append(info.name)
            elif info.dirty:
                skipped_dirty.append(info.name)
            elif not info.tracking:
                skipped_upstream.append(info.name)
            elif info.behind == 0:
                # Safe to skip already current or locally ahead-only repos.
                continue
            else:
                eligible.append(info)

        lines = []
        if eligible:
            lines.append(f"Will pull {len(eligible)} clean repositories that are behind their upstream.")
        else:
            lines.append("No clean, behind repositories are eligible to pull.")
        if skipped_dirty:
            lines.append(f"Skipped dirty: {', '.join(skipped_dirty)}")
        if skipped_conflict:
            lines.append(f"Skipped conflicts: {', '.join(skipped_conflict)}")
        if skipped_upstream:
            lines.append(f"Skipped no upstream: {', '.join(skipped_upstream)}")

        self.append_log("\n".join(lines))
        if not eligible:
            QMessageBox.information(self, "Safe pull", "\n".join(lines))
            return

        reply = QMessageBox.question(
            self,
            "Safe pull",
            "\n".join(lines) + "\n\nProceed?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        if reply != QMessageBox.Yes:
            return

        args = ["pull"]
        if ff_only:
            args.append("--ff-only")
        for info in eligible:
            self.start_command(info.path, args, "Pull")

    def start_command(self, repo: Path, args: list[str], label: str):
        self.pending_commands += 1
        self.progress.setRange(0, 0)
        worker = CommandWorker(repo, args, label)
        worker.signals.message.connect(self.status.setText)
        worker.signals.result.connect(self.command_result)
        worker.signals.finished.connect(self.command_finished)
        self.pool.start(worker)

    @Slot(object)
    def command_result(self, payload):
        repo, args, code, out, err = payload
        cmd = "git " + " ".join(shlex.quote(x) for x in args)
        self.append_log(f"\n[{repo.name}] $ {cmd}\nexit={code}")
        if out:
            self.append_log(out)
        if err:
            self.append_log(err)

    @Slot()
    def command_finished(self):
        self.pending_commands = max(0, self.pending_commands - 1)
        if self.pending_commands == 0:
            self.status.setText("Git operations finished; refreshing status…")
            self.refresh()

    def double_click_row(self, row: int, _col: int):
        item = self.table.item(row, self.COL_REPO)
        if item:
            info = self.repo_infos.get(item.data(Qt.UserRole))
            if info:
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(info.path)))

    def open_context_menu(self, pos):
        info = self.selected_row_info()
        if not info:
            return
        menu = QMenu(self)

        open_folder = QAction("Open folder", self)
        open_folder.triggered.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(info.path))))
        menu.addAction(open_folder)

        terminal = QAction("Open terminal here", self)
        terminal.triggered.connect(lambda: self.open_terminal(info.path))
        menu.addAction(terminal)

        copy_remote = QAction("Copy remote URL", self)
        copy_remote.setEnabled(bool(info.remote))
        copy_remote.triggered.connect(lambda: QApplication.clipboard().setText(info.remote))
        menu.addAction(copy_remote)

        menu.addSeparator()
        refresh_one = QAction("Refresh this repository", self)
        refresh_one.triggered.connect(lambda: self.refresh_one(info.path))
        menu.addAction(refresh_one)

        fetch = QAction("Fetch", self)
        fetch.triggered.connect(lambda: self.start_command(info.path, ["fetch", "--all", "--prune"], "Fetch"))
        menu.addAction(fetch)

        if info.remote.startswith(("http://", "https://")):
            browse = QAction("Open remote in browser", self)
            browse.triggered.connect(lambda: QDesktopServices.openUrl(QUrl(info.remote.removesuffix(".git"))))
            menu.addAction(browse)

        menu.addSeparator()
        status = QAction("Show detailed git status", self)
        status.triggered.connect(lambda: self.start_command(info.path, ["status", "--short", "--branch"], "Status"))
        menu.addAction(status)

        stash_list = QAction("Show stashes", self)
        stash_list.triggered.connect(lambda: self.start_command(info.path, ["stash", "list"], "Stashes"))
        menu.addAction(stash_list)

        menu.exec(self.table.viewport().mapToGlobal(pos))

    def refresh_one(self, repo: Path):
        info = inspect_repo(repo, fetch=self.fetch_on_refresh.isChecked())
        self.repo_infos[str(repo)] = info
        # Simpler and safer than trying to update a sorted row in place.
        self.refresh()

    def open_terminal(self, path: Path):
        try:
            if sys.platform == "darwin":
                subprocess.Popen(["open", "-a", "Terminal", str(path)])
            elif os.name == "nt":
                subprocess.Popen(["cmd.exe", "/K", "cd", "/d", str(path)])
            else:
                terminals = [
                    ["x-terminal-emulator", "--working-directory", str(path)],
                    ["gnome-terminal", "--working-directory", str(path)],
                    ["konsole", "--workdir", str(path)],
                ]
                for cmd in terminals:
                    try:
                        subprocess.Popen(cmd)
                        return
                    except FileNotFoundError:
                        continue
                raise FileNotFoundError("No supported terminal launcher found")
        except Exception as e:
            QMessageBox.warning(self, APP_NAME, f"Could not open terminal:\n{e}")


def main():
    app = QApplication(sys.argv)
    app.setOrganizationName(ORG_NAME)
    app.setApplicationName(APP_NAME)
    w = GitDashboard()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
