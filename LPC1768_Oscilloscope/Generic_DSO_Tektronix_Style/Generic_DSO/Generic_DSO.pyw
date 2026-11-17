"""Double-click entry point after initial setup."""
from pathlib import Path
import traceback
try:
    from scope_app import main
    main()
except Exception:
    error=traceback.format_exc()
    Path(__file__).with_name('startup_error.log').write_text(error)
    try:
        import tkinter as tk
        from tkinter import messagebox
        root=tk.Tk(); root.withdraw()
        messagebox.showerror('Generic DSO','Start with Start_DSO.bat to install dependencies.\n\n'+error[-1800:])
    except Exception: pass
