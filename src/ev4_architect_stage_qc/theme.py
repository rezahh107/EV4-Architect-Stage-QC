from tkinter import ttk

status_neutral = "#6B7280"
status_processing = "#2563EB"
status_success = "#15803D"
status_warning = "#B45309"
status_danger = "#B91C1C"

STATUS_COLORS = {
    "not_run": status_neutral,
    "processing": status_processing,
    "passed": status_success,
    "warning": status_warning,
    "failed": status_danger,
    "internal_error": status_danger,
}


def apply(root):
    style = ttk.Style(root)
    style.configure("Title.TLabel", font=("Segoe UI", 15, "bold"))
    style.configure("StatusTitle.TLabel", font=("Segoe UI", 10, "bold"))
    style.configure("TButton", padding=(8, 5))
