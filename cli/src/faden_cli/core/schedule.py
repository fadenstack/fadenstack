"""Daily jobs -- the backup, the certificate renewal -- for the user who runs Fadenstack; no root.

Two ways, whichever the machine has:

* a **systemd user timer** (``faden-backup.timer``), preferred: every
  systemd distribution has one, and ``Persistent=true`` runs a job missed
  while the server was off. It runs without anyone logged in only with
  *lingering* on, which is switched on for the user (as the node agent's
  installer does).
* a **crontab line** marked with the job's name, where there is cron but no
  usable systemd user manager.

Ubuntu 26.04 server ships no cron, which is why cron is not the only way.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

#: Marks the crontab line of the backup.
MARK = "# faden-backup"
#: The systemd user units of the backup.
UNIT = "faden-backup"
#: The systemd user units (and crontab mark) of the daily ``faden tls renew``.
TLS_UNIT = "faden-tls-renew"

#: Scheduled jobs run with a bare PATH; docker is usually in one of these.
_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"


def _run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, capture_output=True, text=True, check=False, **kwargs)  # noqa: S603


# ── which way ─────────────────────────────────────────────────────


def _systemd_user() -> bool:
    if sys.platform != "linux" or shutil.which("systemctl") is None:
        return False
    state = _run(["systemctl", "--user", "is-system-running"]).stdout.strip()
    # "degraded" still runs timers; "offline"/empty means no user manager.
    return state in {"running", "degraded", "starting", "initializing"}


def _cron() -> bool:
    return sys.platform != "win32" and shutil.which("crontab") is not None


def available() -> bool:
    """Whether jobs can be scheduled on this machine at all."""
    return _systemd_user() or _cron()


# ── what runs ─────────────────────────────────────────────────────


def _cli() -> list[str]:
    """How a scheduled job should run this CLI: by absolute path, not a PATH lookup."""
    found = shutil.which("faden")
    if found:
        return [str(Path(found).resolve())]
    return [sys.executable, "-m", "faden"]


def _config() -> Path | None:
    value = os.environ.get("FADEN_CONFIG")
    return Path(value).expanduser().resolve() if value else None


@dataclass(frozen=True)
class Job:
    """A command of this CLI run every day at *hour*:*minute*, appending to *log*."""

    unit: str
    description: str
    args: tuple[str, ...]
    hour: int
    minute: int
    log: Path

    @property
    def mark(self) -> str:
        """What ends its crontab line."""
        return f"# {self.unit}"


def _command(job: Job) -> str:
    return " ".join(shlex.quote(part) for part in [*_cli(), *job.args])


# ── cron ──────────────────────────────────────────────────────────


def _read() -> list[str]:
    out = _run(["crontab", "-l"])
    # "no crontab for <user>" is exit 1 with nothing to keep.
    return out.stdout.splitlines() if out.returncode == 0 else []


def _write(lines: list[str]) -> None:
    text = "\n".join(lines).rstrip("\n") + "\n" if lines else ""
    subprocess.run(["crontab", "-"], input=text, text=True, check=True)  # noqa: S607


def job_entry(job: Job, config: Path | None) -> str:
    """The crontab line for *job*."""
    env = f"PATH={_PATH}"
    if config is not None:
        env += f" FADEN_CONFIG={shlex.quote(str(config))}"
    log = shlex.quote(str(job.log))
    return f"{job.minute} {job.hour} * * * {env} {_command(job)} >> {log} 2>&1 {job.mark}"


# ── systemd ───────────────────────────────────────────────────────


def _unit_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "systemd" / "user"


def job_units(job: Job, config: Path | None) -> dict[str, str]:
    """The service and timer unit files for *job*."""
    env = [f"Environment=PATH={_PATH}"]
    if config is not None:
        env.append(f'Environment="FADEN_CONFIG={config}"')
    service = "\n".join([
        "[Unit]",
        f"Description={job.description}",
        "",
        "[Service]",
        "Type=oneshot",
        *env,
        f"ExecStart={_command(job)}",
        f"StandardOutput=append:{job.log}",
        f"StandardError=append:{job.log}",
        "",
    ])
    timer = "\n".join([
        "[Unit]",
        f"Description={job.description}",
        "",
        "[Timer]",
        f"OnCalendar=*-*-* {job.hour:02d}:{job.minute:02d}:00",
        # A day the server was off is caught up when it next starts.
        "Persistent=true",
        "",
        "[Install]",
        "WantedBy=timers.target",
        "",
    ])
    return {f"{job.unit}.service": service, f"{job.unit}.timer": timer}


def _linger() -> bool:
    """Lingering on for this user (so the timer runs with nobody logged in); whether it is."""
    user = os.environ.get("USER") or ""
    shown = _run(["loginctl", "show-user", user, "-p", "Linger"]).stdout.strip()
    if shown == "Linger=yes":
        return True
    _run(["loginctl", "enable-linger", user])
    return _run(["loginctl", "show-user", user, "-p", "Linger"]).stdout.strip() == "Linger=yes"


# ── any job ───────────────────────────────────────────────────────


def job_current(unit: str) -> str | None:
    """How *unit* is scheduled, if it is: the timer's time or the crontab line."""
    enabled = ""
    if _systemd_user():
        enabled = _run(["systemctl", "--user", "is-enabled", f"{unit}.timer"]).stdout.strip()
    if enabled == "enabled":
        lines = (_unit_dir() / f"{unit}.timer").read_text().splitlines()
        calendar = next((line for line in lines if line.startswith("OnCalendar=")), "")
        return f"systemd timer {unit}.timer ({calendar.removeprefix('OnCalendar=')})"
    if _cron():
        return next((line for line in _read() if line.endswith(f"# {unit}")), None)
    return None


def install_job(job: Job) -> tuple[str, list[str]]:
    """Schedule *job*, replacing an earlier schedule of it. Returns how, and warnings for the operator."""
    config = _config()
    warnings: list[str] = []
    remove_job(job.unit)
    job.log.parent.mkdir(parents=True, exist_ok=True)
    if _systemd_user():
        directory = _unit_dir()
        directory.mkdir(parents=True, exist_ok=True)
        for name, text in job_units(job, config).items():
            (directory / name).write_text(text, encoding="utf-8")
        _run(["systemctl", "--user", "daemon-reload"])
        enabled = _run(["systemctl", "--user", "enable", "--now", f"{job.unit}.timer"])
        if enabled.returncode != 0:
            msg = f"systemctl --user enable {job.unit}.timer failed: {enabled.stderr.strip()}"
            raise RuntimeError(msg)
        if not _linger():
            warnings.append(
                f"Lingering is off for this user, so {job.unit} only runs while someone is logged in. "
                f"Turn it on once with: sudo loginctl enable-linger {os.environ.get('USER', '<user>')}"
            )
        return f"systemd timer {job.unit}.timer", warnings
    if _cron():
        _write([*_read(), job_entry(job, config)])
        return "crontab", warnings
    msg = "Neither a systemd user manager nor cron is available."
    raise RuntimeError(msg)


def remove_job(unit: str) -> bool:
    """Remove *unit*'s schedule, whichever way it was scheduled; whether there was one."""
    removed = False
    if _systemd_user():
        timer = _unit_dir() / f"{unit}.timer"
        if timer.exists():
            _run(["systemctl", "--user", "disable", "--now", f"{unit}.timer"])
            for name in (f"{unit}.timer", f"{unit}.service"):
                (_unit_dir() / name).unlink(missing_ok=True)
            _run(["systemctl", "--user", "daemon-reload"])
            removed = True
    if _cron():
        lines = _read()
        kept = [line for line in lines if not line.endswith(f"# {unit}")]
        if len(kept) != len(lines):
            _write(kept)
            removed = True
    return removed


# ── the backup ────────────────────────────────────────────────────


def _backup(*, hour: int, minute: int, retain: int, log: Path) -> Job:
    return Job(UNIT, "Fadenstack nightly backup", ("backup", "-y", "--retain", str(retain)), hour, minute, log)


def entry(*, hour: int, minute: int, retain: int, log: Path, config: Path | None) -> str:
    """The crontab line for a backup every day at *hour*:*minute*."""
    return job_entry(_backup(hour=hour, minute=minute, retain=retain, log=log), config)


def units(*, hour: int, minute: int, retain: int, log: Path, config: Path | None) -> dict[str, str]:
    """The service and timer unit files for a backup every day at *hour*:*minute*."""
    return job_units(_backup(hour=hour, minute=minute, retain=retain, log=log), config)


def current() -> str | None:
    """How the nightly backup is scheduled, if it is: the timer's time or the crontab line."""
    return job_current(UNIT)


def install(*, hour: int, minute: int, retain: int, log: Path) -> tuple[str, list[str]]:
    """Schedule the backup, replacing an earlier schedule.

    Returns how it is scheduled, and warnings for the operator.
    """
    return install_job(_backup(hour=hour, minute=minute, retain=retain, log=log))


def remove() -> bool:
    """Remove the scheduled backup, whichever way it was scheduled; whether there was one."""
    return remove_job(UNIT)


# ── the certificate renewal ───────────────────────────────────────


def tls_renewal(log: Path) -> Job:
    """``faden tls renew`` every morning; it changes nothing until something is due."""
    return Job(TLS_UNIT, "Fadenstack certificate renewal", ("tls", "renew"), 4, 40, log)
