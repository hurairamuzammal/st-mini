# Purpose: Provides CLI and scheduling orchestration for recording and replaying macro sessions.

"""
rich_scheduler.py
-----------------
Ties RichRecorder, RichScriptRunner, and APScheduler into one system.

Usage — CLI:

    # Record a new task
    python rich_scheduler.py record --name "send_report" --desc "Send weekly report email"

    # Schedule it to run once
    python rich_scheduler.py schedule --session sessions/send_report.json --at "2025-04-07 09:00:00"

    # Schedule it to repeat (every Monday at 09:00 UTC)
    python rich_scheduler.py schedule --session sessions/send_report.json \\
        --cron --hour 9 --minute 0 --day-of-week mon

    # Run immediately (no schedule)
    python rich_scheduler.py run --session sessions/send_report.json

    # List / cancel jobs
    python rich_scheduler.py list
    python rich_scheduler.py cancel --id send_report_mon_09

Usage — API:

    from rich_recorder       import RichRecorder
    from rich_script_runner  import RichScriptRunner
    from rich_scheduler      import RichScheduler

    runner    = RichScriptRunner(agent=my_agent)
    scheduler = RichScheduler(runner=runner)
    scheduler.start()

    # Record
    rec = RichRecorder()
    rec.start()
    input("Perform your task...")
    rec.save("sessions/send_report.json", name="send_report")

    # Schedule weekly
    scheduler.add_job_cron(
        session_path="sessions/send_report.json",
        job_id="weekly_report",
        hour=9, minute=0, day_of_week="mon",
    )

    scheduler.wait()   # blocks until Ctrl-C
"""

from __future__ import annotations

import argparse
import json
import logging
import signal
import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from rich_recorder      import RichRecorder
from rich_script_runner import RichScriptRunner

logging.basicConfig(
    level   = logging.INFO,
    format  = "%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt = "%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("rich_scheduler")

SESSIONS_DIR = Path("sessions")
DB_PATH      = "rich_scheduler.db"

# ── APScheduler (optional — graceful error if not installed) ─────────────────
try:
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.jobstores.sqlalchemy  import SQLAlchemyJobStore
    from apscheduler.executors.pool        import ThreadPoolExecutor as ApsThreadPool
    from apscheduler.triggers.date         import DateTrigger
    from apscheduler.triggers.cron         import CronTrigger
    _APS_OK = True
except ImportError:
    _APS_OK = False
    logger.warning("apscheduler not installed — pip install apscheduler")


# ─────────────────────────────────────────────────────────────────────────────
# RichScheduler
# ─────────────────────────────────────────────────────────────────────────────

class RichScheduler:
    """
    Schedules Rich Action Packet sessions as agent tasks using APScheduler.

    Jobs are persisted in SQLite so they survive process restarts.
    Only one job runs at a time (max_instances=1) — the agent cannot safely
    control two screens simultaneously.
    """

    def __init__(
        self,
        runner:      Any,
        db_path:     str = DB_PATH,
        sessions_dir: str = "sessions",
        max_workers: int = 1,
    ) -> None:
        if not _APS_OK:
            raise RuntimeError("apscheduler is required — pip install apscheduler")

        self.runner       = runner
        self.sessions_dir = Path(sessions_dir)
        self.sessions_dir.mkdir(parents=True, exist_ok=True)

        jobstores    = {"default": SQLAlchemyJobStore(url=f"sqlite:///{db_path}")}
        executors    = {"default": ApsThreadPool(max_workers)}
        job_defaults = {"coalesce": True, "max_instances": 1, "misfire_grace_time": 120}

        self._scheduler = BackgroundScheduler(
            jobstores    = jobstores,
            executors    = executors,
            job_defaults = job_defaults,
            timezone     = "UTC",
        )
        self._started = False

    # ── lifecycle ─────────────────────────────────────────────────────────────

    def start(self) -> None:
        if self._started:
            return
        self._scheduler.start()
        self._started = True
        logger.info("Scheduler started.")

    def stop(self) -> None:
        if self._started:
            self._scheduler.shutdown(wait=False)
            self._started = False
            logger.info("Scheduler stopped.")

    def wait(self) -> None:
        """Block until SIGINT / SIGTERM."""
        logger.info("Scheduler running — press Ctrl-C to exit.")
        ev = threading.Event()

        def _handler(sig, _frame):
            logger.info("Shutdown requested.")
            ev.set()

        signal.signal(signal.SIGINT,  _handler)
        signal.signal(signal.SIGTERM, _handler)
        ev.wait()
        self.stop()

    # ── add jobs ──────────────────────────────────────────────────────────────

    def add_job_once(
        self,
        session_path: str,
        run_at:       str,
        job_id:       Optional[str] = None,
        replace:      bool = True,
    ) -> str:
        """
        Run a session once at a specific UTC datetime.

        Parameters
        ----------
        session_path  Path to the Rich Action Packet session JSON.
        run_at        Datetime string, e.g. "2025-04-07 09:00:00" (UTC).
        job_id        Unique ID — auto-generated if omitted.
        """
        if not self._started:
            raise RuntimeError("Call scheduler.start() first.")

        run_dt = _parse_dt(run_at)
        job_id = job_id or f"once_{Path(session_path).stem}_{int(run_dt.timestamp())}"

        self._scheduler.add_job(
            func            = self._fire,
            trigger         = DateTrigger(run_date=run_dt),
            args            = [str(session_path)],
            id              = job_id,
            name            = f"once: {Path(session_path).stem}",
            replace_existing= replace,
        )
        logger.info("Scheduled (once)  id=%s  at=%s  file=%s", job_id, run_dt, session_path)
        return job_id

    def add_job_cron(
        self,
        session_path: str,
        job_id:       Optional[str] = None,
        replace:      bool = True,
        **cron_kwargs,
    ) -> str:
        """
        Run a session on a repeating cron schedule (UTC).

        Parameters
        ----------
        session_path  Path to the session JSON.
        job_id        Unique ID.
        **cron_kwargs Passed to APScheduler CronTrigger:
                      hour, minute, second, day_of_week, day, month
                      Examples:
                        hour=9, minute=0                        → daily 09:00
                        hour=9, minute=0, day_of_week="mon-fri" → weekdays 09:00
                        minute="*/30"                           → every 30 min
        """
        if not self._started:
            raise RuntimeError("Call scheduler.start() first.")

        job_id = job_id or f"cron_{Path(session_path).stem}"

        self._scheduler.add_job(
            func             = self._fire,
            trigger          = CronTrigger(**cron_kwargs, timezone="UTC"),
            args             = [str(session_path)],
            id               = job_id,
            name             = f"cron: {Path(session_path).stem}",
            replace_existing = replace,
        )
        logger.info("Scheduled (cron)  id=%s  cron=%s  file=%s", job_id, cron_kwargs, session_path)
        return job_id

    # ── manage ────────────────────────────────────────────────────────────────

    def cancel_job(self, job_id: str) -> bool:
        try:
            self._scheduler.remove_job(job_id)
            logger.info("Cancelled: %s", job_id)
            return True
        except Exception:
            logger.warning("Job not found: %s", job_id)
            return False

    def list_jobs(self) -> List[Dict[str, Any]]:
        jobs = []
        for job in self._scheduler.get_jobs():
            jobs.append({
                "id":       job.id,
                "name":     job.name,
                "next_run": str(job.next_run_time),
                "trigger":  str(job.trigger),
            })
        if not jobs:
            logger.info("No jobs scheduled.")
        return jobs

    # ── execution ─────────────────────────────────────────────────────────────

    def _fire(self, session_path: str) -> None:
        """Called by APScheduler when a job fires."""
        logger.info("Job fired: %s", session_path)
        try:
            self.runner.run_file(session_path)
            logger.info("Job completed: %s", session_path)
        except FileNotFoundError:
            logger.error("Session file not found: %s", session_path)
        except Exception as exc:
            logger.exception("Job failed (%s): %s", session_path, exc)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _parse_dt(s: str) -> datetime:
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    raise ValueError(f"Cannot parse datetime: {s!r}  (use 'YYYY-MM-DD HH:MM:SS' UTC)")


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog        = "rich_scheduler",
        description = "Record, schedule, and replay rich GUI task sessions for the GUI Agent.",
    )
    sub = p.add_subparsers(dest="command", required=True)

    # record
    r = sub.add_parser("record", help="Record a new rich task session")
    r.add_argument("--name",        default="unnamed_session", help="Task name")
    r.add_argument("--desc",        default="",                help="Task description")
    r.add_argument("--file",        default=None,              help="Output filename (default: <name>.json in sessions/)")
    r.add_argument("--no-after",    action="store_true",       help="Skip after-action screenshot (faster)")
    r.add_argument("--no-ocr",      action="store_true",       help="Disable OCR intent capture")
    r.add_argument("--no-semantic", action="store_true",       help="Disable window/process metadata")

    # schedule
    sc = sub.add_parser("schedule", help="Schedule a session for replay")
    sc.add_argument("--session", required=True, help="Path to session JSON")
    sc.add_argument("--id",      default=None,  help="Job ID (auto-generated if omitted)")
    mode = sc.add_mutually_exclusive_group(required=True)
    mode.add_argument("--at",   help="Run once at this UTC datetime, e.g. '2025-04-07 09:00:00'")
    mode.add_argument("--cron", action="store_true", help="Repeating cron schedule")
    sc.add_argument("--hour",        default=None)
    sc.add_argument("--minute",      default=None)
    sc.add_argument("--second",      default=None)
    sc.add_argument("--day",         default=None)
    sc.add_argument("--month",       default=None)
    sc.add_argument("--day-of-week", default=None, dest="day_of_week")

    # run
    ru = sub.add_parser("run", help="Run a session immediately (no schedule)")
    ru.add_argument("--session", required=True, help="Path to session JSON")

    # list
    sub.add_parser("list", help="List all scheduled jobs")

    # cancel
    ca = sub.add_parser("cancel", help="Cancel a scheduled job")
    ca.add_argument("--id", required=True, help="Job ID to cancel")

    return p


def main() -> None:
    parser = _build_parser()
    args   = parser.parse_args()

    # In CLI mode: no live agent → dry-run (prints the task prompt)
    runner    = RichScriptRunner(agent=None)
    scheduler = RichScheduler(runner=runner)

    if args.command == "record":
        path = SESSIONS_DIR / (args.file or f"{args.name}.json")
        print(f"\n{'─'*60}")
        print(f"  Recording: {args.name}")
        if args.desc:
            print(f"  {args.desc}")
        print(f"  → {path}")
        print(f"{'─'*60}")
        print("  Perform your task on screen, then press Enter to stop.")
        print(f"{'─'*60}\n")

        rec = RichRecorder(
            capture_after_shot  = not args.no_after,
            run_ocr             = not args.no_ocr,
            capture_semantics   = not args.no_semantic,
        )
        rec.start()
        try:
            input()
        except (EOFError, KeyboardInterrupt):
            pass

        saved = rec.save(str(path), name=args.name, description=args.desc)
        with open(saved) as f:
            session = json.load(f)

        print(f"\nSession saved: {saved}")
        print(f"Packets: {session.get('packet_count', '?')}")
        print(f"\nTo schedule:  python rich_scheduler.py schedule --session {saved} --at 'YYYY-MM-DD HH:MM:SS'")

    elif args.command == "schedule":
        scheduler.start()
        if args.at:
            jid = scheduler.add_job_once(args.session, run_at=args.at, job_id=args.id)
        else:
            cron = {k: v for k, v in {
                "hour":        args.hour,
                "minute":      args.minute,
                "second":      args.second,
                "day":         args.day,
                "month":       args.month,
                "day_of_week": args.day_of_week,
            }.items() if v is not None}
            if not cron:
                print("ERROR: --cron requires at least one of --hour / --minute / etc.")
                sys.exit(1)
            jid = scheduler.add_job_cron(args.session, job_id=args.id, **cron)
        print(f"Scheduled job: {jid}")
        print("Press Ctrl-C to exit (job is persisted to DB and will survive restarts).")
        scheduler.wait()

    elif args.command == "run":
        runner.run_file(args.session)

    elif args.command == "list":
        scheduler.start()
        jobs = scheduler.list_jobs()
        if not jobs:
            print("No jobs scheduled.")
        else:
            print(f"\n{'ID':<40} {'Next run (UTC)':<28} Trigger")
            print("─" * 100)
            for j in jobs:
                print(f"{j['id']:<40} {j['next_run']:<28} {j['trigger']}")

    elif args.command == "cancel":
        scheduler.start()
        ok = scheduler.cancel_job(args.id)
        print("Cancelled." if ok else "Job not found.")


if __name__ == "__main__":
    main()
