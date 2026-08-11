"""Keeping one device's database fresh against its mirrors, and the schedule that
does it unattended.

This is the interim "keep each device fresh" layer, not multi-writer replication:
writes happen one machine at a time, so a mirror is copied whole with ``rsync
--delete`` — the same last-writer-wins handoff the ``justfile`` recipe documented,
ported here so a scheduler (which has no repo and no ``just``) can call it and the
mirror host lives in config rather than baked into a recipe.

The decision is pure and the I/O is a thin edge. ``choose_source`` picks a mirror
from mtimes the caller resolved, and the plist/cron builders are string functions —
those are unit-tested. The ``ssh``/``rsync``/``launchctl``/``crontab`` calls around
them are the untested edge. ``choose_source``/``pull`` stay the seam a per-row merge
strategy could slot into, but the tracker is expected to move to shared infra, so we
are not building toward one.
"""

import logging
import os
import shutil
import sqlite3
import subprocess
import sys
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from xml.sax.saxutils import escape

from tt.platform import db
from tt.platform.config import Mirror, SyncConfig

log = logging.getLogger(__name__)

# The SQLite fileset a mirror carries. In WAL mode committed data lives in the -wal
# until a checkpoint, so copying tt.db alone would ship stale rows and leaving a
# stale -wal on the far end would let it replay over fresh data; mirroring the exact
# set the source has avoids both.
_FILESET: tuple[str, ...] = ("tt.db", "tt.db-wal", "tt.db-shm")
# Recency is the newest mtime across the db and its WAL only: a WAL-mode write lands
# in the -wal and leaves tt.db untouched, so the db alone understates it. The -shm is
# a scratch file whose mtime says nothing about the data.
_RECENCY_FILES: tuple[str, ...] = ("tt.db", "tt.db-wal")

# Fail fast and never prompt: a scheduler runs this unattended, so a mirror that
# needs a password or a slow handshake must error out, not hang the cycle.
_SSH_OPTS: tuple[str, ...] = ("-o", "BatchMode=yes", "-o", "ConnectTimeout=5")
_SSH_TIMEOUT = 20

# Only the fileset crosses, and --delete prunes a member the source dropped without
# touching the excluded rest of the remote directory.
_RSYNC_FILESET: tuple[str, ...] = (
    "--include=tt.db",
    "--include=tt.db-wal",
    "--include=tt.db-shm",
    "--exclude=*",
)

_LAUNCH_AGENT_LABEL = "dev.tt.sync"
_CRON_BEGIN = "# >>> tt sync (managed) >>>"
_CRON_END = "# <<< tt sync (managed) <<<"


class SyncError(Exception):
    """A sync could not proceed for a reason the operator must fix — no ``tt`` on
    PATH to schedule, say. Distinct from a mirror being unreachable, which is
    absorbed as "nothing to pull" rather than raised."""


class Action(StrEnum):
    """What a ``pull``/``push`` did, for the CLI to render."""

    PULLED = "pulled"
    PUSHED = "pushed"
    CURRENT = "current"
    BUSY = "busy"
    NO_MIRRORS = "no-mirrors"
    REFUSED = "refused"


@dataclass(frozen=True)
class SyncResult:
    """The outcome of one ``pull``/``push``: what happened, the host it moved data
    to or from, any mirrors that did not answer, and a free-text detail (the hosts a
    push refused over, for instance)."""

    action: Action
    source: str | None = None
    unreachable: tuple[str, ...] = ()
    detail: str = ""


@dataclass(frozen=True)
class Probe:
    """One mirror resolved to its recency and whether it answered. An unreachable
    mirror carries ``0.0`` so it never wins ``choose_source``, and ``reachable`` is
    what lets a caller note it apart from a mirror that simply holds no database
    yet."""

    mirror: Mirror
    mtime: float
    reachable: bool


# --- resolving recency ----------------------------------------------------


def newest_mtime(directory: Path) -> float:
    """The newest mtime across the database and its WAL in ``directory``, or ``0.0``
    when neither exists — an absent local database is older than any mirror, so the
    first sync pulls."""
    times: list[float] = []
    for name in _RECENCY_FILES:
        try:
            times.append((directory / name).stat().st_mtime)
        except OSError:
            continue
    return max(times, default=0.0)


def _remote_stat_command(mirror: Mirror) -> str:
    """The one remote shell line printing each recency file's mtime, a number per
    line. Both ``stat`` spellings are tried — BSD/macOS ``-f %m`` then GNU ``-c %Y`` —
    so the mirror's OS need not match this one, and a missing file prints nothing."""
    files = " ".join(f'"{mirror.path}/{name}"' for name in _RECENCY_FILES)
    stat = 'stat -f %m "$f" 2>/dev/null || stat -c %Y "$f" 2>/dev/null || true'
    return f"for f in {files}; do {stat}; done"


def _floats(text: str) -> list[float]:
    values: list[float] = []
    for token in text.split():
        try:
            values.append(float(token))
        except ValueError:
            continue
    return values


def probe(mirror: Mirror) -> Probe:
    """Resolve a mirror's recency over ``ssh``. A connection failure or a non-zero
    exit is unreachable (``0.0``, logged); a clean connection with no files is
    reachable at ``0.0``. Never raises — a mirror down is not a failed sync."""
    try:
        completed = subprocess.run(
            ["ssh", *_SSH_OPTS, mirror.host, _remote_stat_command(mirror)],
            capture_output=True,
            text=True,
            timeout=_SSH_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError):
        log.warning("sync mirror %s unreachable", mirror.host)
        return Probe(mirror=mirror, mtime=0.0, reachable=False)
    if completed.returncode != 0:
        log.warning("sync mirror %s unreachable (ssh exit %d)", mirror.host, completed.returncode)
        return Probe(mirror=mirror, mtime=0.0, reachable=False)
    return Probe(mirror=mirror, mtime=max(_floats(completed.stdout), default=0.0), reachable=True)


def remote_mtime(mirror: Mirror) -> float:
    """The newest mtime across the mirror's database and WAL, or ``0.0`` when it is
    unreachable or holds no fileset."""
    return probe(mirror).mtime


# --- the decision (pure) --------------------------------------------------


def choose_source(local_mtime: float, candidates: list[tuple[Mirror, float]]) -> Mirror | None:
    """The mirror to pull from: the newest one strictly newer than local, or ``None``
    when none beats local — nothing to do, or local is ahead.

    Pure: the mtimes are resolved by the caller, so this is the unit-tested heart with
    no I/O. A mirror tied with local does not pull (equal is already current), and
    among mirrors the newest wins, the first listed taking a tie between them."""
    best: Mirror | None = None
    best_mtime = local_mtime
    for mirror, mtime in candidates:
        if mtime > best_mtime:
            best, best_mtime = mirror, mtime
    return best


# --- the I/O edge ---------------------------------------------------------


def _writer_idle(data_dir: Path) -> bool:
    """Best-effort check that no other ``tt``/TUI holds the local database for
    writing. Opens it and attempts ``BEGIN IMMEDIATE`` — which takes the write lock —
    honoring the connection's 5s ``busy_timeout``; if the lock cannot be had another
    writer has it and the caller skips this cycle. The lock is released at once.

    There is a small race between releasing here and the rsync swap the caller then
    does; for a single-user tracker that is acceptable, and far better than the manual
    recipe's "remember to close tt first"."""
    engine = db.connect(f"sqlite:///{data_dir / 'tt.db'}")
    raw = engine.raw_connection()
    try:
        cursor = raw.cursor()
        cursor.execute("BEGIN IMMEDIATE")
        cursor.execute("ROLLBACK")
        cursor.close()
        return True
    except sqlite3.OperationalError:
        return False
    finally:
        raw.close()
        engine.dispose()


def _rsync(source: str, destination: str) -> None:
    subprocess.run(
        [
            "rsync",
            "-a",
            "--delete",
            "-e",
            f"ssh {' '.join(_SSH_OPTS)}",
            *_RSYNC_FILESET,
            source,
            destination,
        ],
        check=True,
    )


def _pull_mirror(mirror: Mirror, data_dir: Path) -> None:
    _rsync(f"{mirror.host}:{mirror.path}/", f"{data_dir}/")


def _push_mirror(mirror: Mirror, data_dir: Path) -> None:
    subprocess.run(["ssh", *_SSH_OPTS, mirror.host, f"mkdir -p {mirror.path}"], check=True)
    _rsync(f"{data_dir}/", f"{mirror.host}:{mirror.path}/")


# --- pull and push --------------------------------------------------------


def pull(cfg: SyncConfig, data_dir: Path) -> SyncResult:
    """One pull cycle — what the scheduler calls. Resolve every mirror's recency,
    choose the newest that beats local, and if there is one, guard against a live
    writer and rsync its fileset in. A tie or a local that is ahead is already
    current; a busy database skips the cycle. Pull-only: it never overwrites a mirror,
    so it can only ever move fresher work onto this device."""
    if not cfg.mirrors:
        return SyncResult(action=Action.NO_MIRRORS)
    probes = [probe(mirror) for mirror in cfg.mirrors]
    unreachable = tuple(p.mirror.host for p in probes if not p.reachable)
    local = newest_mtime(data_dir)
    source = choose_source(local, [(p.mirror, p.mtime) for p in probes])
    if source is None:
        return SyncResult(action=Action.CURRENT, unreachable=unreachable)
    if not _writer_idle(data_dir):
        return SyncResult(action=Action.BUSY, unreachable=unreachable)
    _pull_mirror(source, data_dir)
    return SyncResult(action=Action.PULLED, source=source.host, unreachable=unreachable)


def push(cfg: SyncConfig, data_dir: Path, force: bool = False) -> SyncResult:
    """Mirror local out to every reachable mirror — the manual counterpart of the old
    ``just push``, for a human, never on the cron path. Refuses when any mirror holds
    a newer database than local (pull first, or ``force`` to override); an unreachable
    mirror is noted and skipped rather than failing the push."""
    if not cfg.mirrors:
        return SyncResult(action=Action.NO_MIRRORS)
    probes = [probe(mirror) for mirror in cfg.mirrors]
    unreachable = tuple(p.mirror.host for p in probes if not p.reachable)
    local = newest_mtime(data_dir)
    if not force:
        newer = tuple(p.mirror.host for p in probes if p.reachable and p.mtime > local)
        if newer:
            return SyncResult(
                action=Action.REFUSED, unreachable=unreachable, detail=", ".join(newer)
            )
    pushed = [p.mirror for p in probes if p.reachable]
    for mirror in pushed:
        _push_mirror(mirror, data_dir)
    return SyncResult(
        action=Action.PUSHED,
        source=", ".join(mirror.host for mirror in pushed),
        unreachable=unreachable,
    )


# --- the schedule: pure builders ------------------------------------------


def plist(tt_path: str, interval: int) -> str:
    """The launchd agent that runs ``tt sync run`` every ``interval`` seconds. Pure —
    the XML is a string the installer writes and the tests assert on. ``RunAtLoad`` is
    false so installing does not fire a pull at once."""
    program = "\n".join(
        f"        <string>{escape(part)}</string>" for part in (tt_path, "sync", "run")
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
        '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        '<plist version="1.0">\n'
        "<dict>\n"
        "    <key>Label</key>\n"
        f"    <string>{_LAUNCH_AGENT_LABEL}</string>\n"
        "    <key>ProgramArguments</key>\n"
        "    <array>\n"
        f"{program}\n"
        "    </array>\n"
        "    <key>StartInterval</key>\n"
        f"    <integer>{interval}</integer>\n"
        "    <key>RunAtLoad</key>\n"
        "    <false/>\n"
        "</dict>\n"
        "</plist>\n"
    )


def cron_minutes(interval: int) -> int:
    """The cadence as whole minutes, cron's finest granularity. A sub-minute interval
    is raised to one — cron cannot do better — and the CLI warns when it does."""
    return max(1, interval // 60)


def cron_line(tt_path: str, interval: int) -> str:
    """The crontab line running ``tt sync run`` every N minutes."""
    return f"*/{cron_minutes(interval)} * * * * {tt_path} sync run"


def cron_block(tt_path: str, interval: int) -> str:
    """tt's managed crontab block: the marked line between its begin/end sentinels, so
    ``cron_without_block`` can find and remove exactly what tt wrote."""
    return f"{_CRON_BEGIN}\n{cron_line(tt_path, interval)}\n{_CRON_END}"


def cron_without_block(existing: str) -> str:
    """``existing`` crontab text with tt's managed block removed, normalized to a
    single trailing newline (empty stays empty). Idempotent — no block is a no-op."""
    kept: list[str] = []
    inside = False
    for line in existing.splitlines():
        marker = line.strip()
        if marker == _CRON_BEGIN:
            inside = True
        elif marker == _CRON_END:
            inside = False
        elif not inside:
            kept.append(line)
    body = "\n".join(kept).strip("\n")
    return f"{body}\n" if body else ""


def cron_with_block(existing: str, tt_path: str, interval: int) -> str:
    """``existing`` crontab text with tt's managed block installed, replacing any
    block already there. Idempotent — install rewrites rather than stacking blocks."""
    return f"{cron_without_block(existing)}{cron_block(tt_path, interval)}\n"


# --- the schedule: the edge -----------------------------------------------


def _is_macos() -> bool:
    return sys.platform == "darwin"


def launch_agent_path() -> Path:
    """Where the launchd agent lives — one per-user LaunchAgents plist."""
    return Path.home() / "Library" / "LaunchAgents" / f"{_LAUNCH_AGENT_LABEL}.plist"


def tt_executable() -> str:
    """The absolute ``tt`` on PATH the schedule should invoke, resolved so a cron's
    minimal PATH still finds it. Raises ``SyncError`` when tt is not installed."""
    found = shutil.which("tt")
    if found is None:
        raise SyncError("tt is not on PATH; run 'just install' first")
    return found


def _launchctl(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["launchctl", *args], capture_output=True, text=True)


def _launch_domain() -> str:
    return f"gui/{os.getuid()}"


def _bootstrap(path: Path) -> None:
    if _launchctl("bootstrap", _launch_domain(), str(path)).returncode != 0:
        _launchctl("load", str(path))


def _bootout(path: Path) -> None:
    if _launchctl("bootout", _launch_domain(), str(path)).returncode != 0:
        _launchctl("unload", str(path))


def _read_crontab() -> str:
    completed = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    return completed.stdout if completed.returncode == 0 else ""


def _write_crontab(text: str) -> None:
    subprocess.run(["crontab", "-"], input=text, text=True, check=True)


def install(interval: int, tt_path: str) -> str:
    """Install (or rewrite) the schedule that pulls every ``interval`` seconds, and
    return a line describing what was installed. launchd on macOS, the user crontab
    elsewhere. Idempotent."""
    if _is_macos():
        path = launch_agent_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(plist(tt_path, interval))
        _bootout(path)
        _bootstrap(path)
        return f"installed launchd agent at {path}, every {interval}s"
    _write_crontab(cron_with_block(_read_crontab(), tt_path, interval))
    minutes = cron_minutes(interval)
    warning = " (interval floored up to 1m; cron cannot do sub-minute)" if interval < 60 else ""
    return f"installed crontab entry, every {minutes}m{warning}"


def uninstall() -> str:
    """Remove the schedule if present, returning a line describing what was removed.
    Idempotent — removing an absent schedule is a no-op."""
    if _is_macos():
        path = launch_agent_path()
        if not path.exists():
            return "no launchd agent installed"
        _bootout(path)
        path.unlink()
        return f"removed launchd agent at {path}"
    _write_crontab(cron_without_block(_read_crontab()))
    return "removed crontab entry"


def schedule_installed() -> bool:
    """Whether the pull schedule is installed on this machine."""
    if _is_macos():
        return launch_agent_path().exists()
    return _CRON_BEGIN in _read_crontab()


# --- status ---------------------------------------------------------------


@dataclass(frozen=True)
class Status:
    """What ``tt sync status`` renders: the configured cadence, whether the schedule
    is installed, the local recency, and each mirror probed against it."""

    interval: int
    installed: bool
    local_mtime: float
    mirrors: tuple[Probe, ...] = field(default_factory=tuple)


def status(cfg: SyncConfig, data_dir: Path) -> Status:
    """Resolve everything ``tt sync status`` shows: cadence and schedule state from
    config and the OS, and each mirror's recency probed against local."""
    return Status(
        interval=cfg.interval,
        installed=schedule_installed(),
        local_mtime=newest_mtime(data_dir),
        mirrors=tuple(probe(mirror) for mirror in cfg.mirrors),
    )
