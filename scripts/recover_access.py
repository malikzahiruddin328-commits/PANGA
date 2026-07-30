"""Standalone recovery tool for PRD §13's "Windows-account-loss recovery":
restores access to data/ using a recovery code generated on the Settings
page's Data Recovery section, without needing the Streamlit app itself to
run - the app needs the key just to load its pages, so it can't be the
thing that recovers a missing key.

No terminal needed - double-click recover_access.vbs instead of running
this directly, same pattern as run_app.vbs/run_app.bat.
"""

import sys
import tkinter as tk
from pathlib import Path
from tkinter import messagebox

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from security.crypto_store import recover_key_with_code  # noqa: E402


def attempt_recovery(code: str, status_var: tk.StringVar, root: tk.Tk) -> None:
    code = code.strip()
    if not code:
        status_var.set("Enter your recovery code first.")
        return
    try:
        recover_key_with_code(code)
    except ValueError as e:
        messagebox.showerror("Recovery failed", str(e))
        return
    messagebox.showinfo(
        "Recovery successful",
        "Access restored on this Windows account. You can close this window and open Panga normally.",
    )
    root.destroy()


def main() -> None:
    root = tk.Tk()
    root.title("Panga - Data Recovery")
    root.resizable(False, False)

    tk.Label(
        root,
        text=(
            "Enter the recovery code you saved from Panga's Settings page.\n"
            "This restores access to your data on this Windows account."
        ),
        justify="left",
        padx=16,
        pady=(16, 8),
    ).pack()

    code_entry = tk.Entry(root, width=45, font=("Consolas", 11))
    code_entry.pack(padx=16, pady=(0, 8))
    code_entry.focus()

    status_var = tk.StringVar()
    tk.Label(root, textvariable=status_var, fg="red", padx=16).pack()

    tk.Button(
        root,
        text="Recover access",
        command=lambda: attempt_recovery(code_entry.get(), status_var, root),
        padx=12, pady=4,
    ).pack(pady=(4, 16))

    root.bind("<Return>", lambda event: attempt_recovery(code_entry.get(), status_var, root))
    root.mainloop()


if __name__ == "__main__":
    main()
