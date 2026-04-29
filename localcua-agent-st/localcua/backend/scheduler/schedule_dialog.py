# Purpose: Shows a small Tk dialog to collect schedule date/time and macro metadata.
from __future__ import annotations

import argparse
import json
import sys
import tkinter as tk
from datetime import datetime, timedelta
from tkinter import messagebox, ttk


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="schedule_dialog",
        description="Basic Python UI for macro schedule date/time input.",
    )
    parser.add_argument("--name", default="Untitled Macro")
    parser.add_argument("--description", default="")
    return parser


def _run_dialog(default_name: str, default_description: str) -> dict:
    now = datetime.now() + timedelta(minutes=2)
    payload: dict = {"ok": False}

    root = tk.Tk()
    root.title("Schedule Macro")
    root.attributes("-topmost", True)
    root.resizable(False, False)

    root.update_idletasks()
    width = 560
    height = 330
    screen_w = root.winfo_screenwidth()
    screen_h = root.winfo_screenheight()
    x = max(0, (screen_w - width) // 2)
    y = max(0, (screen_h - height) // 2)
    root.geometry(f"{width}x{height}+{x}+{y}")
    root.lift()
    root.focus_force()

    try:
        root.eval("tk::PlaceWindow . center")
    except tk.TclError:
        pass

    frame = ttk.Frame(root, padding=12)
    frame.grid(row=0, column=0, sticky="nsew")

    ttk.Label(frame, text="Macro Name").grid(row=0, column=0, sticky="w")
    name_var = tk.StringVar(value=default_name)
    name_entry = ttk.Entry(frame, textvariable=name_var, width=38)
    name_entry.grid(row=1, column=0, columnspan=5, sticky="ew", pady=(2, 8))

    ttk.Label(frame, text="Description (optional)").grid(row=2, column=0, sticky="w")
    desc_var = tk.StringVar(value=default_description)
    desc_entry = ttk.Entry(frame, textvariable=desc_var, width=38)
    desc_entry.grid(row=3, column=0, columnspan=5, sticky="ew", pady=(2, 10))

    ttk.Label(frame, text="Date").grid(row=4, column=0, sticky="w")
    year_var = tk.StringVar(value=str(now.year))
    month_var = tk.StringVar(value=str(now.month))
    day_var = tk.StringVar(value=str(now.day))

    ttk.Spinbox(frame, from_=2000, to=2100, width=7, textvariable=year_var).grid(row=5, column=0, padx=(0, 4), pady=(2, 10))
    ttk.Spinbox(frame, from_=1, to=12, width=5, textvariable=month_var).grid(row=5, column=1, padx=4, pady=(2, 10))
    ttk.Spinbox(frame, from_=1, to=31, width=5, textvariable=day_var).grid(row=5, column=2, padx=4, pady=(2, 10))

    ttk.Label(frame, text="Time (24h)").grid(row=6, column=0, sticky="w")
    hour_var = tk.StringVar(value=str(now.hour))
    minute_var = tk.StringVar(value=str(now.minute))

    ttk.Spinbox(frame, from_=0, to=23, width=5, textvariable=hour_var).grid(row=7, column=0, padx=(0, 4), pady=(2, 10))
    ttk.Spinbox(frame, from_=0, to=59, width=5, textvariable=minute_var).grid(row=7, column=1, padx=4, pady=(2, 10))

    button_row = ttk.Frame(frame)
    button_row.grid(row=8, column=0, columnspan=5, sticky="e")

    def _cancel() -> None:
        payload.clear()
        payload.update({"ok": False})
        root.destroy()

    def _save() -> None:
        name = name_var.get().strip()
        description = desc_var.get().strip()

        if not name:
            messagebox.showerror("Validation", "Macro name is required.")
            return

        try:
            selected = datetime(
                int(year_var.get()),
                int(month_var.get()),
                int(day_var.get()),
                int(hour_var.get()),
                int(minute_var.get()),
            )
        except ValueError:
            messagebox.showerror("Validation", "Please enter a valid date/time.")
            return

        if selected <= datetime.now() + timedelta(seconds=5):
            messagebox.showerror("Validation", "Please choose a future time (at least 5 seconds ahead).")
            return

        payload.clear()
        payload.update(
            {
                "ok": True,
                "name": name,
                "description": description,
                "run_at": selected.isoformat(),
            }
        )
        root.destroy()

    ttk.Button(button_row, text="Cancel", command=_cancel).grid(row=0, column=0, padx=(0, 6))
    ttk.Button(button_row, text="Save & Schedule", command=_save).grid(row=0, column=1)

    root.protocol("WM_DELETE_WINDOW", _cancel)
    name_entry.focus_set()
    root.mainloop()

    return payload


def main() -> None:
    args = _build_parser().parse_args()

    try:
        result = _run_dialog(
            default_name=args.name,
            default_description=args.description,
        )
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        sys.exit(1)

    print(json.dumps(result))


if __name__ == "__main__":
    main()
