# Purpose: Exposes API helpers to record, schedule, list, and execute macro sessions.
from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import subprocess
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from apscheduler.schedulers.background import BackgroundScheduler  # type: ignore[import-not-found]
from apscheduler.triggers.date import DateTrigger  # type: ignore[import-not-found]

from scheduler.rich_recorder import RichRecorder
from scheduler.rich_script_runner import build_rich_task_prompt

logger = logging.getLogger("macro_scheduler")

MAX_UI_TARS_PROMPT_TOKENS = 6000
CHARS_PER_TOKEN = 4


def _iso_utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_client_datetime(value: str) -> datetime:
    """
    Parse datetime sent by Flutter.
    Accepts either local naive ISO string or timezone-aware ISO string.
    """
    if not value:
        raise ValueError("run_at is required")

    cleaned = value.strip().replace("Z", "+00:00")
    dt = datetime.fromisoformat(cleaned)
    if dt.tzinfo is None:
        dt = dt.astimezone()
    return dt.astimezone(timezone.utc)


def _estimate_tokens(text: str) -> int:
    """Simplified token estimation."""
    return len(text) // CHARS_PER_TOKEN


def _truncate_prompt_to_token_limit(text: str, max_tokens: int) -> tuple[str, bool]:
    """
    Smart truncation that preserves step structure.
    """
    if not text:
        return "", False

    estimated = _estimate_tokens(text)
    if estimated <= max_tokens:
        return text, False

    # Extract steps section.
    steps_match = re.search(
        r"EXECUTE THESE STEPS IN ORDER.*?:\n(.*?)\n\nRULES:",
        text,
        re.DOTALL,
    )

    if not steps_match:
        # Fallback: simple character truncation.
        max_chars = max_tokens * CHARS_PER_TOKEN
        return text[:max_chars] + "\n\n[TRUNCATED]", True

    header = text[: steps_match.start(1)]
    footer_start = steps_match.end(1)
    footer = text[footer_start:]
    steps_text = steps_match.group(1)

    # Calculate available tokens for steps.
    header_tokens = _estimate_tokens(header)
    footer_tokens = _estimate_tokens(footer)
    available_tokens = max_tokens - header_tokens - footer_tokens - 50

    if available_tokens <= 0:
        return header + "\n[ERROR: Header too long]\n" + footer, True

    # Keep steps that fit.
    steps = steps_text.strip().split("\n")
    kept_steps: List[str] = []
    used_tokens = 0

    for step in steps:
        step_tokens = _estimate_tokens(step) + 1
        if used_tokens + step_tokens > available_tokens:
            break
        kept_steps.append(step)
        used_tokens += step_tokens

    if not kept_steps:
        return header + "\n[ERROR: No steps fit in token budget]\n" + footer, True

    truncated_steps = "\n".join(kept_steps)

    # Update footer with new step count.
    total_steps = len(steps)
    kept_count = len(kept_steps)
    footer = re.sub(
        r"After step \d+:",
        f"After step {kept_count}:",
        footer,
    )
    footer = re.sub(
        r"steps 1→\d+",
        f"steps 1→{kept_count}",
        footer,
    )

    # Add truncation note.
    truncation_note = f"\n\n[Note: {total_steps - kept_count} steps truncated to fit {max_tokens} token limit]"

    result = header + truncated_steps + truncation_note + footer
    return result, True


class MacroSchedulerManager:
    def __init__(self, base_dir: Path) -> None:
        self.base_dir = base_dir
        self.sessions_dir = base_dir / "scheduler" / "sessions"
        self.sessions_dir.mkdir(parents=True, exist_ok=True)

        self.db_path = base_dir / "scheduler" / "macro_scheduler.db"
        self.helper_script = base_dir / "scheduler" / "run_scheduled_macro.py"
        self.dialog_script = base_dir / "scheduler" / "schedule_dialog.py"

        self._db_lock = threading.Lock()
        self._recorder_lock = threading.Lock()
        self._active_recorder: RichRecorder | None = None
        self._process_lock = threading.Lock()
        self._running_processes: Dict[int, subprocess.Popen[str]] = {}
        self._stop_requested: set[int] = set()

        self._scheduler = BackgroundScheduler(timezone="UTC")
        self._scheduler.start()

        self._init_db()
        self._reload_pending_jobs()

    def shutdown(self) -> None:
        self._scheduler.shutdown(wait=False)

    # ------------------------ recording lifecycle ------------------------
    def recording_status(self) -> Dict[str, Any]:
        with self._recorder_lock:
            active = self._active_recorder is not None
        return {"is_recording": active}

    def start_recording(self) -> Dict[str, Any]:
        with self._recorder_lock:
            if self._active_recorder is not None:
                raise ValueError("A recording is already in progress")

            recorder = RichRecorder()
            recorder.start()
            self._active_recorder = recorder

        logger.info("Macro recording started")
        return {"status": "recording_started"}

    def cancel_recording(self) -> Dict[str, Any]:
        with self._recorder_lock:
            recorder = self._active_recorder
            if recorder is None:
                return {"status": "not_recording"}

            try:
                recorder.stop()
            finally:
                self._active_recorder = None

        logger.info("Macro recording cancelled")
        return {"status": "cancelled"}

    def stop_and_schedule(self, name: str, description: str, run_at: str) -> Dict[str, Any]:
        recorder = self._take_active_recorder()
        self._stop_and_sanitize_recorder(recorder)

        clean_name = (name or "Untitled Macro").strip()
        if not clean_name:
            clean_name = "Untitled Macro"

        run_dt_utc = _parse_client_datetime(run_at)
        if run_dt_utc <= datetime.now(timezone.utc):
            raise ValueError("Scheduled date/time must be in the future")

        return self._save_and_schedule(
            recorder=recorder,
            clean_name=clean_name,
            description=description or "",
            run_dt_utc=run_dt_utc,
        )

    def stop_and_schedule_with_dialog(self, name: str = "", description: str = "") -> Dict[str, Any]:
        recorder = self._take_active_recorder()
        self._stop_and_sanitize_recorder(recorder)

        default_name = (name or "Untitled Macro").strip() or "Untitled Macro"
        default_description = description or ""

        dialog_data = self._open_schedule_dialog(
            default_name=default_name,
            default_description=default_description,
        )

        if not dialog_data.get("ok"):
            return {"status": "cancelled"}

        clean_name = str(dialog_data.get("name") or default_name).strip() or default_name
        clean_description = str(dialog_data.get("description") or "").strip()
        run_at_value = str(dialog_data.get("run_at") or "").strip()

        run_dt_utc = _parse_client_datetime(run_at_value)
        if run_dt_utc <= datetime.now(timezone.utc):
            raise ValueError("Scheduled date/time must be in the future")

        return self._save_and_schedule(
            recorder=recorder,
            clean_name=clean_name,
            description=clean_description,
            run_dt_utc=run_dt_utc,
        )

    def _take_active_recorder(self) -> RichRecorder:
        with self._recorder_lock:
            recorder = self._active_recorder
            if recorder is None:
                raise ValueError("No active recording to stop")
            self._active_recorder = None
        return recorder

    def _stop_and_sanitize_recorder(self, recorder: RichRecorder) -> None:
        """
        Stop recording immediately so scheduler UI interactions are not captured,
        then trim any trailing control packets that slipped in right at stop time.
        """
        try:
            recorder.stop()
        except Exception as exc:
            logger.warning("Failed to stop recorder cleanly: %s", exc)

        packets = getattr(recorder, "_packets", None)
        if not isinstance(packets, list) or not packets:
            return

        def _is_scheduler_control_packet(pkt: Dict[str, Any]) -> bool:
            semantic = pkt.get("semantic") or {}
            title = str(semantic.get("window_title") or "").strip().lower()
            process_name = str(semantic.get("process_name") or "").strip().lower()
            return title in {"localcua", "schedule macro"} or process_name in {"localcua", "localcua.exe"}

        original_count = len(packets)
        while packets and _is_scheduler_control_packet(packets[-1]):
            packets.pop()

        removed_count = original_count - len(packets)
        if removed_count:
            logger.info(
                "Trimmed %d trailing scheduler control packet(s) from recording.",
                removed_count,
            )

    def _save_and_schedule(
        self,
        recorder: RichRecorder,
        clean_name: str,
        description: str,
        run_dt_utc: datetime,
    ) -> Dict[str, Any]:
        if run_dt_utc <= datetime.now(timezone.utc):
            raise ValueError("Scheduled date/time must be in the future")

        safe_name = "_".join(clean_name.lower().split())[:40] or "macro"
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{safe_name}_{timestamp}.json"
        session_path = self.sessions_dir / filename
        prompt_path = self.sessions_dir / f"{safe_name}_{timestamp}.txt"

        recorder.save(str(session_path), name=clean_name, description=description)

        with session_path.open("r", encoding="utf-8") as f:
            session = json.load(f)

        prompt_text = build_rich_task_prompt(
            session,
            include_coords=True,
            skip_hover=True,
        )
        prompt_with_header = (
            f"# Macro: {clean_name}\n"
            f"# Scheduled: {run_dt_utc.isoformat()}\n\n"
            f"{prompt_text}\n"
        )
        prompt_with_header, was_truncated = _truncate_prompt_to_token_limit(
            prompt_with_header,
            max_tokens=MAX_UI_TARS_PROMPT_TOKENS,
        )
        if was_truncated:
            logger.warning(
                "Macro '%s' truncated: exceeded %d token limit",
                clean_name,
                MAX_UI_TARS_PROMPT_TOKENS,
            )
        prompt_path.write_text(prompt_with_header, encoding="utf-8")

        with self._db_lock, sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            created_at = _iso_utc_now()
            cursor.execute(
                """
                INSERT INTO macros(name, description, session_path, prompt_path, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (clean_name, description, str(session_path), str(prompt_path), created_at),
            )
            macro_id = cursor.lastrowid

            cursor.execute(
                """
                INSERT INTO schedules(macro_id, run_at, status, created_at, updated_at)
                VALUES (?, ?, 'scheduled', ?, ?)
                """,
                (macro_id, run_dt_utc.isoformat(), created_at, created_at),
            )
            schedule_id = cursor.lastrowid
            conn.commit()

        self._schedule_job(schedule_id, run_dt_utc)
        return self.get_schedule(schedule_id)

    def _open_schedule_dialog(self, default_name: str, default_description: str) -> Dict[str, Any]:
        if not self.dialog_script.exists():
            raise ValueError(f"Schedule dialog script not found: {self.dialog_script}")

        cmd = [
            sys.executable,
            str(self.dialog_script),
            "--name",
            default_name,
            "--description",
            default_description,
        ]

        result = subprocess.run(
            cmd,
            cwd=str(self.base_dir),
            capture_output=True,
            text=True,
            check=False,
        )

        if result.returncode != 0:
            err_text = (result.stderr or result.stdout or "Schedule dialog failed").strip()
            raise ValueError(err_text)

        raw = (result.stdout or "").strip()
        if not raw:
            raise ValueError("Schedule dialog returned no data")

        parsed: Dict[str, Any] | None = None
        for line in reversed(raw.splitlines()):
            candidate = line.strip()
            if not candidate:
                continue
            try:
                data = json.loads(candidate)
                if isinstance(data, dict):
                    parsed = data
                    break
            except json.JSONDecodeError:
                continue

        if parsed is None:
            raise ValueError("Could not parse schedule dialog output")

        return parsed

    # ------------------------ task CRUD ------------------------
    def list_schedules(self) -> List[Dict[str, Any]]:
        with self._db_lock, sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT s.id as schedule_id, s.run_at, s.status, s.created_at as schedule_created_at,
                       s.updated_at, s.last_run_at, s.last_error,
                       m.id as macro_id, m.name as macro_name, m.description as macro_description,
                      m.session_path, m.prompt_path, m.created_at as macro_created_at
                FROM schedules s
                JOIN macros m ON m.id = s.macro_id
                ORDER BY s.run_at ASC
                """
            ).fetchall()

        return [self._row_to_schedule_dict(r) for r in rows]

    def get_schedule(self, schedule_id: int) -> Dict[str, Any]:
        with self._db_lock, sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                """
                SELECT s.id as schedule_id, s.run_at, s.status, s.created_at as schedule_created_at,
                       s.updated_at, s.last_run_at, s.last_error,
                       m.id as macro_id, m.name as macro_name, m.description as macro_description,
                      m.session_path, m.prompt_path, m.created_at as macro_created_at
                FROM schedules s
                JOIN macros m ON m.id = s.macro_id
                WHERE s.id = ?
                """,
                (schedule_id,),
            ).fetchone()

        if row is None:
            raise ValueError("Scheduled task not found")

        return self._row_to_schedule_dict(row)

    def execute_now(self, schedule_id: int) -> Dict[str, Any]:
        """Trigger a scheduled macro immediately (supports replay at any time)."""
        with self._db_lock, sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            row = cursor.execute(
                """
                SELECT s.status, m.session_path, m.prompt_path
                FROM schedules s
                JOIN macros m ON m.id = s.macro_id
                WHERE s.id = ?
                """,
                (schedule_id,),
            ).fetchone()

            if row is None:
                raise ValueError("Scheduled task not found")

            status = row[0]
            if status == "running":
                raise ValueError("Task is already running")

            cursor.execute(
                """
                UPDATE schedules
                SET status = 'running', updated_at = ?, last_error = NULL
                WHERE id = ?
                """,
                (_iso_utc_now(), schedule_id),
            )
            conn.commit()

            session_path = row[1]
            prompt_path = row[2]

        worker = threading.Thread(
            target=self._execute_schedule_process,
            args=(schedule_id, session_path, prompt_path),
            daemon=True,
        )
        worker.start()
        return self.get_schedule(schedule_id)

    def stop_schedule(self, schedule_id: int) -> Dict[str, Any]:
        """Stop a currently running scheduled macro execution."""
        with self._db_lock, sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT status FROM schedules WHERE id = ?",
                (schedule_id,),
            ).fetchone()

        if row is None:
            raise ValueError("Scheduled task not found")
        if row[0] != "running":
            raise ValueError("Task is not running")

        with self._process_lock:
            process = self._running_processes.get(schedule_id)
            if process is None:
                raise ValueError("Task is not running")
            self._stop_requested.add(schedule_id)

        logger.info("Stopping scheduled task %s", schedule_id)
        pid = process.pid
        try:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(pid)],
                    capture_output=True,
                    check=False,
                )
            else:
                process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
        except Exception as exc:
            logger.exception("Failed to stop scheduled task %s", schedule_id)
            raise ValueError(f"Failed to stop running task: {exc}") from exc

        self._mark_stopped(schedule_id, "Stopped by user")
        return self.get_schedule(schedule_id)

    def reschedule(self, schedule_id: int, run_at: str) -> Dict[str, Any]:
        run_dt_utc = _parse_client_datetime(run_at)
        if run_dt_utc <= datetime.now(timezone.utc):
            raise ValueError("Scheduled date/time must be in the future")

        with self._db_lock, sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            row = cursor.execute(
                "SELECT status FROM schedules WHERE id = ?",
                (schedule_id,),
            ).fetchone()

            if row is None:
                raise ValueError("Scheduled task not found")

            status = row[0]
            if status == "running":
                raise ValueError("Cannot reschedule while task is running")

            cursor.execute(
                """
                UPDATE schedules
                SET run_at = ?, status = 'scheduled', updated_at = ?, last_error = NULL
                WHERE id = ?
                """,
                (run_dt_utc.isoformat(), _iso_utc_now(), schedule_id),
            )
            conn.commit()

        self._remove_job_if_exists(schedule_id)
        self._schedule_job(schedule_id, run_dt_utc)
        return self.get_schedule(schedule_id)

    def delete_schedule(self, schedule_id: int) -> Dict[str, Any]:
        self._remove_job_if_exists(schedule_id)

        with self._db_lock, sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            row = cursor.execute(
                "SELECT macro_id FROM schedules WHERE id = ?",
                (schedule_id,),
            ).fetchone()

            if row is None:
                raise ValueError("Scheduled task not found")

            macro_id = row[0]
            cursor.execute("DELETE FROM schedules WHERE id = ?", (schedule_id,))

            remaining = cursor.execute(
                "SELECT COUNT(*) FROM schedules WHERE macro_id = ?",
                (macro_id,),
            ).fetchone()[0]
            if remaining == 0:
                macro_row = cursor.execute(
                    "SELECT session_path, prompt_path FROM macros WHERE id = ?",
                    (macro_id,),
                ).fetchone()
                cursor.execute("DELETE FROM macros WHERE id = ?", (macro_id,))
                if macro_row:
                    session_path = Path(macro_row[0])
                    if session_path.exists():
                        session_path.unlink(missing_ok=True)
                    prompt_path = Path(macro_row[1]) if macro_row[1] else None
                    if prompt_path and prompt_path.exists():
                        prompt_path.unlink(missing_ok=True)

            conn.commit()

        return {"status": "deleted", "schedule_id": schedule_id}

    # ------------------------ job runtime ------------------------
    def _schedule_job(self, schedule_id: int, run_dt_utc: datetime) -> None:
        job_id = self._job_id(schedule_id)
        self._scheduler.add_job(
            self._run_schedule,
            trigger=DateTrigger(run_date=run_dt_utc),
            args=[schedule_id],
            id=job_id,
            replace_existing=True,
            misfire_grace_time=120,
        )

    def _run_schedule(self, schedule_id: int) -> None:
        with self._db_lock, sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            row = cursor.execute(
                """
                SELECT s.id, m.session_path, m.prompt_path
                FROM schedules s
                JOIN macros m ON m.id = s.macro_id
                WHERE s.id = ?
                """,
                (schedule_id,),
            ).fetchone()
            if row is None:
                return

            cursor.execute(
                "UPDATE schedules SET status = 'running', updated_at = ?, last_error = NULL WHERE id = ?",
                (_iso_utc_now(), schedule_id),
            )
            conn.commit()
            session_path = row[1]
            prompt_path = row[2]

        self._execute_schedule_process(schedule_id, session_path, prompt_path)

    def _execute_schedule_process(
        self,
        schedule_id: int,
        session_path: str,
        prompt_path: str | None,
    ) -> None:
        """Run the helper script then mark completed/failed."""

        cmd = [
            sys.executable,
            str(self.helper_script),
            "--session",
            session_path,
            "--mode",
            "task_schedule",
            "--prompt-token-limit",
            str(MAX_UI_TARS_PROMPT_TOKENS),
        ]
        if prompt_path:
            cmd.extend(["--task-text", prompt_path])
        logger.info("Executing scheduled task %s", schedule_id)

        process: subprocess.Popen[str] | None = None
        try:
            process = subprocess.Popen(
                cmd,
                cwd=str(self.base_dir),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )

            with self._process_lock:
                self._running_processes[schedule_id] = process

            stdout, stderr = process.communicate()

            with self._process_lock:
                was_stopped = schedule_id in self._stop_requested
                self._running_processes.pop(schedule_id, None)
                self._stop_requested.discard(schedule_id)

            if was_stopped:
                self._mark_stopped(schedule_id, "Stopped by user")
            elif process.returncode == 0:
                self._mark_completed(schedule_id)
            else:
                err_text = (stderr or stdout or "Task failed").strip()
                self._mark_failed(schedule_id, err_text[:1500])
        except Exception as exc:
            with self._process_lock:
                self._running_processes.pop(schedule_id, None)
                self._stop_requested.discard(schedule_id)
            self._mark_failed(schedule_id, str(exc))

    def _mark_completed(self, schedule_id: int) -> None:
        with self._db_lock, sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                UPDATE schedules
                SET status = 'completed',
                    last_run_at = ?,
                    updated_at = ?,
                    last_error = NULL
                WHERE id = ?
                """,
                (_iso_utc_now(), _iso_utc_now(), schedule_id),
            )
            conn.commit()

    def _mark_failed(self, schedule_id: int, error: str) -> None:
        with self._db_lock, sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                UPDATE schedules
                SET status = 'failed',
                    last_run_at = ?,
                    updated_at = ?,
                    last_error = ?
                WHERE id = ?
                """,
                (_iso_utc_now(), _iso_utc_now(), error, schedule_id),
            )
            conn.commit()

    def _mark_stopped(self, schedule_id: int, reason: str) -> None:
        with self._db_lock, sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                UPDATE schedules
                SET status = 'stopped',
                    last_run_at = ?,
                    updated_at = ?,
                    last_error = ?
                WHERE id = ?
                """,
                (_iso_utc_now(), _iso_utc_now(), reason, schedule_id),
            )
            conn.commit()

    # ------------------------ setup helpers ------------------------
    def _init_db(self) -> None:
        with self._db_lock, sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS macros (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    description TEXT NOT NULL,
                    session_path TEXT NOT NULL,
                    prompt_path TEXT,
                    created_at TEXT NOT NULL
                )
                """
            )

            columns = conn.execute("PRAGMA table_info(macros)").fetchall()
            column_names = {col[1] for col in columns}
            if "prompt_path" not in column_names:
                conn.execute("ALTER TABLE macros ADD COLUMN prompt_path TEXT")

            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS schedules (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    macro_id INTEGER NOT NULL,
                    run_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_run_at TEXT,
                    last_error TEXT,
                    FOREIGN KEY (macro_id) REFERENCES macros(id)
                )
                """
            )
            conn.commit()

    def _reload_pending_jobs(self) -> None:
        now_iso = _iso_utc_now()
        with self._db_lock, sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT id, run_at FROM schedules
                WHERE status = 'scheduled' AND run_at > ?
                """,
                (now_iso,),
            ).fetchall()

        for schedule_id, run_at in rows:
            run_dt = datetime.fromisoformat(run_at)
            self._schedule_job(schedule_id, run_dt)

    def _remove_job_if_exists(self, schedule_id: int) -> None:
        job = self._scheduler.get_job(self._job_id(schedule_id))
        if job is not None:
            self._scheduler.remove_job(self._job_id(schedule_id))

    @staticmethod
    def _job_id(schedule_id: int) -> str:
        return f"schedule_{schedule_id}"

    @staticmethod
    def _row_to_schedule_dict(row: sqlite3.Row) -> Dict[str, Any]:
        return {
            "schedule_id": row["schedule_id"],
            "run_at": row["run_at"],
            "status": row["status"],
            "schedule_created_at": row["schedule_created_at"],
            "updated_at": row["updated_at"],
            "last_run_at": row["last_run_at"],
            "last_error": row["last_error"],
            "macro": {
                "macro_id": row["macro_id"],
                "name": row["macro_name"],
                "description": row["macro_description"],
                "session_path": row["session_path"],
                "prompt_path": row["prompt_path"],
                "created_at": row["macro_created_at"],
            },
        }
