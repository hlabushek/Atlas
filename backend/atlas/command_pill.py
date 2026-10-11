"""Small non-modal Windows overlay for local voice request status."""
from __future__ import annotations

import queue
import threading
import ctypes
from ctypes import wintypes


class CommandPill:
    """Display transient request/status text without taking keyboard focus."""

    def __init__(self, close_after_seconds: float = 5.0):
        self._close_after = max(2.0, min(20.0, float(close_after_seconds)))
        self._events: queue.Queue[tuple[str, str, float | None]] = queue.Queue(maxsize=8)
        self._thread = threading.Thread(target=self._run, name='atlas-command-pill', daemon=True)
        self._thread.start()

    def show(self, command: str, status: str = 'Понял запрос…') -> None:
        self._post(command, status, None)

    def update(self, command: str, status: str) -> None:
        self._post(command, status, None)

    def finish(self, command: str, status: str, close_after: float | None = None) -> None:
        self._post(command, status, self._close_after if close_after is None else close_after)

    def _post(self, command: str, status: str, timeout: float | None) -> None:
        item = (str(command)[:180], str(status)[:180], timeout)
        try:
            self._events.put_nowait(item)
        except queue.Full:
            try:
                self._events.get_nowait()
            except queue.Empty:
                pass
            try:
                self._events.put_nowait(item)
            except queue.Full:
                pass

    def _run(self) -> None:
        try:
            import tkinter as tk

            root = tk.Tk()
            root.withdraw()
            root.overrideredirect(True)
            root.attributes('-topmost', True)
            root.attributes('-alpha', 0.97)
            root.configure(bg='#0B1119')
            root.resizable(False, False)
            root.title('Atlas')
            user32 = ctypes.WinDLL('user32', use_last_error=True)
            user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
            user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
            user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
            user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
            user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
            user32.SetWindowPos.restype = wintypes.BOOL
            root.update_idletasks()
            hwnd = root.winfo_id()
            style = user32.GetWindowLongPtrW(hwnd, -20)
            user32.SetWindowLongPtrW(hwnd, -20, style | 0x08000000 | 0x00000080)  # NOACTIVATE | TOOLWINDOW
            width, height = 560, 116
            frame = tk.Frame(root, bg='#121C28', highlightbackground='#314252', highlightthickness=1)
            frame.pack(fill='both', expand=True, padx=1, pady=1)
            accent = tk.Frame(frame, bg='#47D7C8', width=5)
            accent.pack(side='left', fill='y')
            content = tk.Frame(frame, bg='#121C28', padx=17, pady=12)
            content.pack(side='left', fill='both', expand=True)
            tk.Label(content, text='ATLAS', bg='#121C28', fg='#47D7C8', font=('Segoe UI Semibold', 9)).pack(anchor='w', fill='x')
            command_label = tk.Label(content, text='', bg='#121C28', fg='#F4F7FA',
                                     font=('Segoe UI Semibold', 12), anchor='w', justify='left', wraplength=495)
            command_label.pack(anchor='w', fill='x', pady=(3, 1))
            status_label = tk.Label(content, text='', bg='#121C28', fg='#A8B5C3',
                                    font=('Segoe UI', 9), anchor='w', justify='left', wraplength=495)
            status_label.pack(anchor='w', fill='x')

            hide_id: str | None = None
            def poll() -> None:
                nonlocal hide_id
                latest = None
                while True:
                    try:
                        latest = self._events.get_nowait()
                    except queue.Empty:
                        break
                if latest is not None:
                    command, status, timeout = latest
                    command_label.configure(text=command)
                    status_label.configure(text=status)
                    root.update_idletasks()
                    screen_w = root.winfo_screenwidth()
                    root.geometry(f'{width}x{height}+{max(0, (screen_w - width) // 2)}+28')
                    root.deiconify()
                    user32.SetWindowPos(hwnd, wintypes.HWND(-1), 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010 | 0x0040)
                    if hide_id is not None:
                        try: root.after_cancel(hide_id)
                        except tk.TclError: pass
                        hide_id = None
                    if timeout is not None:
                        hide_id = root.after(int(max(1.0, min(30.0, timeout)) * 1000), root.withdraw)
                root.after(80, poll)

            root.after(80, poll)
            root.mainloop()
        except Exception:
            # Overlay failure must never interrupt local commands or Atlas requests.
            return
