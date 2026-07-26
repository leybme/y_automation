"""y_automation studio - drag-and-drop control panel for the ESP32-C3 firmware.

Run with:  python app/main.py
"""

from __future__ import annotations

import json
import os
import queue
import re
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from flow_canvas import FlowCanvas  # noqa: E402
from nodes import NODE_TYPES, groups  # noqa: E402
from runner import FlowRunner  # noqa: E402
from serial_link import DEFAULT_BAUD, SerialLink, describe_ports  # noqa: E402

BAUDS = ("115200", "230400", "460800", "921600")
PALETTE_BG = "#f4f6fa"


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("y_automation studio")
        self.geometry("1280x820")
        self.minsize(1000, 640)

        self.events: queue.Queue = queue.Queue()
        self.link = SerialLink(on_line=self._queue_line, on_disconnect=self._queue_drop)
        self.runner: FlowRunner | None = None
        self.flow_path: str | None = None

        self.dry_run = tk.BooleanVar(value=False)
        self.status = tk.StringVar(value="disconnected")
        self.port_var = tk.StringVar()
        self.baud_var = tk.StringVar(value=str(DEFAULT_BAUD))

        self._drag_key: str | None = None
        self._ghost: tk.Toplevel | None = None
        self._prop_widgets: dict = {}

        try:
            ttk.Style().theme_use("clam")
        except tk.TclError:
            pass

        self._build_toolbar()
        self._build_body()
        self._build_console()

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.bind("<Delete>", lambda e: self.canvas.delete_selected())
        self.bind("<Control-s>", lambda e: self.save_flow())
        self.bind("<Control-o>", lambda e: self.open_flow())
        self.bind("<F5>", lambda e: self.run_flow())
        self.bind("<Escape>", lambda e: self.stop_flow())

        self.refresh_ports()
        self._seed_flow()
        self.after(40, self._pump)

    # -- layout -------------------------------------------------------------

    def _build_toolbar(self) -> None:
        bar = ttk.Frame(self, padding=(8, 6))
        bar.pack(side="top", fill="x")

        ttk.Label(bar, text="Port").pack(side="left")
        self.port_box = ttk.Combobox(bar, textvariable=self.port_var, width=32,
                                     state="readonly")
        self.port_box.pack(side="left", padx=(4, 4))
        ttk.Button(bar, text="Refresh", width=8, command=self.refresh_ports).pack(side="left")

        ttk.Label(bar, text="Baud").pack(side="left", padx=(10, 2))
        ttk.Combobox(bar, textvariable=self.baud_var, width=8, state="readonly",
                     values=BAUDS).pack(side="left")

        self.connect_btn = ttk.Button(bar, text="Connect", width=11,
                                      command=self.toggle_connection)
        self.connect_btn.pack(side="left", padx=(8, 2))
        ttk.Button(bar, text="Reset", width=7,
                   command=lambda: self._async(self.link.pulse_reset)).pack(side="left")

        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=10)
        ttk.Button(bar, text="New", width=6, command=self.new_flow).pack(side="left")
        ttk.Button(bar, text="Open", width=6, command=self.open_flow).pack(side="left", padx=2)
        ttk.Button(bar, text="Save", width=6, command=self.save_flow).pack(side="left")

        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=10)
        self.run_btn = ttk.Button(bar, text="Run  (F5)", width=11, command=self.run_flow)
        self.run_btn.pack(side="left")
        self.stop_btn = ttk.Button(bar, text="Stop", width=7, command=self.stop_flow,
                                   state="disabled")
        self.stop_btn.pack(side="left", padx=2)
        ttk.Checkbutton(bar, text="Dry run", variable=self.dry_run).pack(side="left", padx=6)

        ttk.Label(bar, textvariable=self.status, foreground="#4a5568").pack(side="right")

    def _build_body(self) -> None:
        self.vertical = ttk.PanedWindow(self, orient="vertical")
        self.vertical.pack(side="top", fill="both", expand=True)

        horizontal = ttk.PanedWindow(self.vertical, orient="horizontal")
        self.vertical.add(horizontal, weight=4)

        horizontal.add(self._build_palette(horizontal), weight=0)

        center = ttk.Frame(horizontal)
        center.rowconfigure(0, weight=1)
        center.columnconfigure(0, weight=1)
        self.canvas = FlowCanvas(center, on_select=self._on_select, on_change=lambda: None)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        vbar = ttk.Scrollbar(center, orient="vertical", command=self.canvas.yview)
        hbar = ttk.Scrollbar(center, orient="horizontal", command=self.canvas.xview)
        self.canvas.configure(yscrollcommand=vbar.set, xscrollcommand=hbar.set)
        vbar.grid(row=0, column=1, sticky="ns")
        hbar.grid(row=1, column=0, sticky="ew")
        horizontal.add(center, weight=1)

        horizontal.add(self._build_side(horizontal), weight=0)

    def _build_palette(self, master) -> ttk.Frame:
        frame = ttk.Frame(master, width=210)
        frame.pack_propagate(False)
        ttk.Label(frame, text="Blocks", font=("Segoe UI", 10, "bold"),
                  padding=(8, 8, 8, 2)).pack(anchor="w")
        ttk.Label(frame, text="drag onto the canvas\n(or double-click)",
                  foreground="#64748b", padding=(8, 0, 8, 6)).pack(anchor="w")

        holder = tk.Frame(frame, background=PALETTE_BG)
        holder.pack(fill="both", expand=True)
        canvas = tk.Canvas(holder, background=PALETTE_BG, highlightthickness=0, width=200)
        scroll = ttk.Scrollbar(holder, orient="vertical", command=canvas.yview)
        inner = tk.Frame(canvas, background=PALETTE_BG)
        inner.bind("<Configure>",
                   lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.configure(yscrollcommand=scroll.set)
        canvas.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        canvas.bind("<MouseWheel>",
                    lambda e: canvas.yview_scroll(-1 * (e.delta // 120), "units"))

        for group, specs in groups():
            tk.Label(inner, text=group.upper(), background=PALETTE_BG, fg="#7b8794",
                     font=("Segoe UI", 8, "bold"), anchor="w").pack(fill="x",
                                                                    padx=10, pady=(10, 2))
            for spec in specs:
                chip = tk.Label(inner, text=spec.label, background=spec.color, fg="white",
                                font=("Segoe UI", 9), anchor="w", padx=10, pady=6,
                                cursor="hand2")
                chip.pack(fill="x", padx=8, pady=2)
                chip.bind("<ButtonPress-1>", lambda e, k=spec.key: self._palette_press(e, k))
                chip.bind("<B1-Motion>", self._palette_motion)
                chip.bind("<ButtonRelease-1>", self._palette_release)
                chip.bind("<Double-Button-1>",
                          lambda e, k=spec.key: self.canvas.add_node(k, 80, 80))
        return frame

    def _build_side(self, master) -> ttk.Frame:
        frame = ttk.Frame(master, width=290)
        frame.pack_propagate(False)
        book = ttk.Notebook(frame)
        book.pack(fill="both", expand=True)

        self.prop_frame = ttk.Frame(book, padding=10)
        book.add(self.prop_frame, text="Properties")
        self._on_select(None)

        book.add(self._build_manual(book), text="Manual")
        return frame

    def _build_manual(self, master) -> ttk.Frame:
        frame = ttk.Frame(master, padding=10)
        self.manual_pin = tk.IntVar(value=2)
        self.manual_servo = tk.DoubleVar(value=90)
        self.manual_hz = tk.StringVar(value="1000")
        self.manual_duty = tk.StringVar(value="50")

        ttk.Label(frame, text="Pin", font=("Segoe UI", 9, "bold")).pack(anchor="w")
        ttk.Spinbox(frame, from_=0, to=21, textvariable=self.manual_pin,
                    width=6).pack(anchor="w", pady=(2, 8))

        ttk.Label(frame, text="Digital", font=("Segoe UI", 9, "bold")).pack(anchor="w")
        row = ttk.Frame(frame)
        row.pack(fill="x", pady=(2, 10))
        for text, arg in (("On", "ON"), ("Off", "OFF"), ("Toggle", "TOGGLE")):
            ttk.Button(row, text=text, width=8,
                       command=lambda a=arg: self._manual(f"DOUT {self.manual_pin.get()} {a}")
                       ).pack(side="left", padx=(0, 4))
        row2 = ttk.Frame(frame)
        row2.pack(fill="x", pady=(0, 12))
        ttk.Button(row2, text="Read", width=8,
                   command=lambda: self._manual(f"DREAD {self.manual_pin.get()}")
                   ).pack(side="left", padx=(0, 4))
        ttk.Button(row2, text="Save as boot default",
                   command=self._manual_default).pack(side="left")

        ttk.Separator(frame).pack(fill="x", pady=6)
        ttk.Label(frame, text="Servo", font=("Segoe UI", 9, "bold")).pack(anchor="w")
        scale = ttk.Scale(frame, from_=0, to=180, variable=self.manual_servo,
                          command=lambda v: self.servo_label.config(
                              text=f"{float(v):.0f} deg"))
        scale.pack(fill="x", pady=(4, 0))
        self.servo_label = ttk.Label(frame, text="90 deg", foreground="#64748b")
        self.servo_label.pack(anchor="w")
        ttk.Button(frame, text="Send angle",
                   command=lambda: self._manual(
                       f"SERVO {self.manual_pin.get()} {self.manual_servo.get():.1f}")
                   ).pack(anchor="w", pady=(4, 12))

        ttk.Separator(frame).pack(fill="x", pady=6)
        ttk.Label(frame, text="Frequency", font=("Segoe UI", 9, "bold")).pack(anchor="w")
        grid = ttk.Frame(frame)
        grid.pack(fill="x", pady=(4, 6))
        ttk.Label(grid, text="Hz").grid(row=0, column=0, sticky="w")
        ttk.Entry(grid, textvariable=self.manual_hz, width=10).grid(row=0, column=1, padx=4)
        ttk.Label(grid, text="Duty %").grid(row=1, column=0, sticky="w", pady=(4, 0))
        ttk.Entry(grid, textvariable=self.manual_duty, width=10).grid(row=1, column=1,
                                                                     padx=4, pady=(4, 0))
        ttk.Button(frame, text="Start output",
                   command=lambda: self._manual(
                       f"FREQ {self.manual_pin.get()} {self.manual_hz.get()} "
                       f"{self.manual_duty.get()}")).pack(anchor="w", pady=(4, 2))
        ttk.Button(frame, text="Release pin",
                   command=lambda: self._manual(f"STOP {self.manual_pin.get()}")
                   ).pack(anchor="w")

        ttk.Separator(frame).pack(fill="x", pady=10)
        ttk.Button(frame, text="Query state", command=lambda: self._manual("STATE")
                   ).pack(anchor="w", pady=2)
        ttk.Button(frame, text="ALL OFF", command=lambda: self._manual("ALLOFF")
                   ).pack(anchor="w", pady=2)
        return frame

    def _build_console(self) -> None:
        frame = ttk.Frame(self.vertical)
        self.vertical.add(frame, weight=1)

        self.console = tk.Text(frame, height=10, wrap="none", background="#11161d",
                               foreground="#cbd5e1", insertbackground="#cbd5e1",
                               font=("Consolas", 9), state="disabled",
                               borderwidth=0, padx=8, pady=6)
        bar = ttk.Scrollbar(frame, orient="vertical", command=self.console.yview)
        self.console.configure(yscrollcommand=bar.set)
        self.console.pack(side="top", fill="both", expand=True)
        bar.place(relx=1.0, rely=0, relheight=1.0, anchor="ne")

        for tag, color in (("tx", "#63b3ed"), ("rx", "#a0aec0"), ("ok", "#68d391"),
                           ("err", "#fc8181"), ("sys", "#f6e05e"), ("run", "#d6bcfa")):
            self.console.tag_configure(tag, foreground=color)

        entry_row = ttk.Frame(frame, padding=(0, 4, 0, 0))
        entry_row.pack(side="bottom", fill="x")
        self.cmd_var = tk.StringVar()
        entry = ttk.Entry(entry_row, textvariable=self.cmd_var)
        entry.pack(side="left", fill="x", expand=True, padx=(0, 4))
        entry.bind("<Return>", lambda e: self._send_console())
        ttk.Button(entry_row, text="Send", width=8,
                   command=self._send_console).pack(side="left")
        ttk.Button(entry_row, text="Clear", width=8,
                   command=self._clear_console).pack(side="left", padx=(4, 0))

    # -- palette drag and drop ---------------------------------------------

    def _palette_press(self, event, key: str) -> None:
        self._drag_key = key
        self._destroy_ghost()

    def _palette_motion(self, event) -> None:
        if not self._drag_key:
            return
        if self._ghost is None:
            spec = NODE_TYPES[self._drag_key]
            ghost = tk.Toplevel(self)
            ghost.overrideredirect(True)
            ghost.attributes("-alpha", 0.85)
            tk.Label(ghost, text=spec.label, background=spec.color, fg="white",
                     font=("Segoe UI", 9), padx=12, pady=7).pack()
            self._ghost = ghost
        self._ghost.geometry(f"+{event.x_root + 12}+{event.y_root + 12}")

    def _palette_release(self, event) -> None:
        key, self._drag_key = self._drag_key, None
        had_ghost = self._ghost is not None
        self._destroy_ghost()
        if key and had_ghost and self.canvas.contains_screen_point(event.x_root,
                                                                  event.y_root):
            self.canvas.drop_at_screen(key, event.x_root, event.y_root)

    def _destroy_ghost(self) -> None:
        if self._ghost is not None:
            self._ghost.destroy()
            self._ghost = None

    # -- properties panel ---------------------------------------------------

    def _on_select(self, node) -> None:
        for child in self.prop_frame.winfo_children():
            child.destroy()
        self._prop_widgets = {}

        if node is None:
            ttk.Label(self.prop_frame, text="Select a block to edit it.",
                      foreground="#64748b", wraplength=250).pack(anchor="w")
            ttk.Label(self.prop_frame, wraplength=250, foreground="#94a3b8",
                      text="\nDrag a block from the left onto the canvas. Drag from a "
                           "coloured port on a block's right edge onto another block to "
                           "wire them together. Right-click a port to unwire it. "
                           "Delete removes the selected block.").pack(anchor="w")
            return

        spec = node.spec
        ttk.Label(self.prop_frame, text=spec.label,
                  font=("Segoe UI", 11, "bold")).pack(anchor="w")
        if spec.help:
            ttk.Label(self.prop_frame, text=spec.help, wraplength=250,
                      foreground="#64748b").pack(anchor="w", pady=(2, 10))

        for param in spec.params:
            ttk.Label(self.prop_frame, text=param.label,
                      font=("Segoe UI", 9, "bold")).pack(anchor="w", pady=(6, 1))
            var = tk.StringVar(value=str(node.params.get(param.key, param.default)))
            if param.kind == "choice":
                widget = ttk.Combobox(self.prop_frame, textvariable=var, width=18,
                                      state="readonly", values=list(param.choices))
                widget.bind("<<ComboboxSelected>>",
                            lambda e, p=param, v=var, n=node: self._commit(n, p, v))
            elif param.kind == "int":
                widget = ttk.Spinbox(self.prop_frame, textvariable=var, width=18,
                                     from_=param.lo if param.lo is not None else 0,
                                     to=param.hi if param.hi is not None else 1_000_000,
                                     command=lambda p=param, v=var, n=node:
                                     self._commit(n, p, v))
            else:
                widget = ttk.Entry(self.prop_frame, textvariable=var, width=20)
            widget.pack(anchor="w", fill="x")
            widget.bind("<KeyRelease>",
                        lambda e, p=param, v=var, n=node: self._commit(n, p, v))
            widget.bind("<FocusOut>",
                        lambda e, p=param, v=var, n=node: self._commit(n, p, v))
            if param.unit:
                ttk.Label(self.prop_frame, text=param.unit,
                          foreground="#94a3b8").pack(anchor="w")
            self._prop_widgets[param.key] = var

        ttk.Separator(self.prop_frame).pack(fill="x", pady=12)
        ttk.Button(self.prop_frame, text="Run this block once",
                   command=lambda n=node: self._run_single(n)).pack(anchor="w")
        ttk.Button(self.prop_frame, text="Delete block",
                   command=self.canvas.delete_selected).pack(anchor="w", pady=4)

    def _commit(self, node, param, var) -> None:
        raw = var.get().strip()
        if param.kind in ("int", "float"):
            try:
                value = float(raw)
            except ValueError:
                return
            if param.lo is not None:
                value = max(param.lo, value)
            if param.hi is not None:
                value = min(param.hi, value)
            value = int(value) if param.kind == "int" else value
        else:
            value = raw
        if node.params.get(param.key) != value:
            node.params[param.key] = value
            self.canvas.redraw()

    def _run_single(self, node) -> None:
        spec = node.spec
        if spec.command is None:
            self._log("run", f"{spec.label} is a flow-control block, nothing to send")
            return
        self._manual(spec.command(node.params))

    # -- device actions -----------------------------------------------------

    def refresh_ports(self) -> None:
        ports = describe_ports()
        self.port_box.configure(values=ports)
        if ports and not self.port_var.get():
            self.port_var.set(ports[0])

    def toggle_connection(self) -> None:
        if self.link.is_open:
            self.link.close()
            self._set_connected(False)
            return
        target = self.port_var.get().split(" - ")[0].strip()
        if not target:
            messagebox.showwarning("y_automation", "No serial port selected.")
            return
        try:
            self.link.open(target, int(self.baud_var.get()))
        except Exception as exc:
            messagebox.showerror("y_automation", f"Could not open {target}:\n{exc}")
            return
        self._set_connected(True)
        self._async(lambda: self._identify())

    def _identify(self) -> None:
        response = self.link.send("ID")
        if response.ok:
            self.events.put(("status", f"connected - {response.line}"))
        else:
            self.events.put(("log", ("err", f"ID failed: {response.summary}")))

    def _set_connected(self, connected: bool) -> None:
        self.connect_btn.configure(text="Disconnect" if connected else "Connect")
        self.status.set("connected" if connected else "disconnected")

    def _manual(self, command: str) -> None:
        if self.dry_run.get():
            self._log("run", f"(dry run) {command}")
            return
        if not self.link.is_open:
            self._log("err", "not connected")
            return
        self._async(lambda: self.link.send(command))

    def _manual_default(self) -> None:
        """Persists the pin's *current* level as its power-on level."""
        pin = self.manual_pin.get()
        if self.dry_run.get():
            self._log("run", f"(dry run) DEF {pin} <current level>")
            return
        if not self.link.is_open:
            self._log("err", "not connected")
            return

        def work() -> None:
            reply = self.link.send(f"DREAD {pin}")
            if not reply.ok:
                self.events.put(("log", ("err", f"could not read GPIO{pin}: "
                                                f"{reply.summary}")))
                return
            match = re.search(r"level=(\d+)", reply.line)
            if not match:
                self.events.put(("log", ("err", f"unexpected reply: {reply.line}")))
                return
            self.link.send(f"DEF {pin} {'ON' if match.group(1) == '1' else 'OFF'}")

        self._async(work)

    def _send_console(self) -> None:
        text = self.cmd_var.get().strip()
        if not text:
            return
        self.cmd_var.set("")
        self._manual(text)

    def _clear_console(self) -> None:
        self.console.configure(state="normal")
        self.console.delete("1.0", "end")
        self.console.configure(state="disabled")

    def _async(self, fn) -> None:
        threading.Thread(target=fn, daemon=True).start()

    # -- flow lifecycle -----------------------------------------------------

    def _seed_flow(self) -> None:
        self.canvas.add_node("start", 60, 60)
        self.canvas.select(None)

    def new_flow(self) -> None:
        if not messagebox.askokcancel("y_automation", "Discard the current flow?"):
            return
        self.canvas.clear()
        self.flow_path = None
        self._seed_flow()

    def open_flow(self) -> None:
        path = filedialog.askopenfilename(
            title="Open flow", defaultextension=".json",
            filetypes=[("Flow files", "*.json"), ("All files", "*.*")])
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
            self.canvas.flow.load(payload)
        except Exception as exc:
            messagebox.showerror("y_automation", f"Could not open the flow:\n{exc}")
            return
        self.flow_path = path
        self.canvas.select(None)
        self.canvas.redraw()
        self._log("sys", f"loaded {os.path.basename(path)}")

    def save_flow(self) -> None:
        path = self.flow_path or filedialog.asksaveasfilename(
            title="Save flow", defaultextension=".json",
            filetypes=[("Flow files", "*.json")])
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(self.canvas.flow.dumps())
        except OSError as exc:
            messagebox.showerror("y_automation", f"Could not save the flow:\n{exc}")
            return
        self.flow_path = path
        self._log("sys", f"saved {os.path.basename(path)}")

    def run_flow(self) -> None:
        if self.runner and self.runner.is_alive():
            return
        self.runner = FlowRunner(self.canvas.flow, self.link,
                                 emit=lambda kind, payload: self.events.put((kind, payload)),
                                 dry_run=self.dry_run.get())
        self.run_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        self.status.set("running")
        self._log("run", "--- flow started ---")
        self.runner.start()

    def stop_flow(self) -> None:
        if self.runner and self.runner.is_alive():
            self.runner.stop()
            self._log("run", "stop requested")

    # -- event pump ---------------------------------------------------------

    def _queue_line(self, direction: str, text: str) -> None:
        tag = {"tx": "tx", "rx": "rx", "sys": "sys"}.get(direction, "rx")
        if direction == "rx":
            if text.split(":", 1)[-1].strip().startswith("ERR"):
                tag = "err"
            elif "OK" in text:
                tag = "ok"
        self.events.put(("log", (tag, f"{'>' if direction == 'tx' else '<'} {text}"
                                 if direction != "sys" else f"* {text}")))

    def _queue_drop(self, reason: str) -> None:
        self.events.put(("dropped", reason))

    def _pump(self) -> None:
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "log":
                    if isinstance(payload, tuple):
                        self._log(*payload)
                    else:
                        self._log("run", str(payload))
                elif kind == "node":
                    self.canvas.set_active(payload)
                elif kind == "error":
                    self._log("err", str(payload))
                elif kind == "finished":
                    self._on_run_finished(str(payload))
                elif kind == "status":
                    self.status.set(str(payload))
                elif kind == "dropped":
                    self._set_connected(False)
        except queue.Empty:
            pass
        self.after(40, self._pump)

    def _on_run_finished(self, reason: str) -> None:
        self.run_btn.configure(state="normal")
        self.stop_btn.configure(state="disabled")
        self.canvas.set_active(None)
        self.status.set("connected" if self.link.is_open else "disconnected")
        self._log("run", f"--- flow {reason} ---")

    def _log(self, tag: str, text: str) -> None:
        self.console.configure(state="normal")
        self.console.insert("end", text + "\n", tag)
        self.console.see("end")
        # Keep the console bounded so a long run cannot grow without limit.
        if int(self.console.index("end-1c").split(".")[0]) > 2000:
            self.console.delete("1.0", "500.0")
        self.console.configure(state="disabled")

    def _on_close(self) -> None:
        self.stop_flow()
        self.link.close()
        self.destroy()


def main() -> None:
    App().mainloop()


if __name__ == "__main__":
    main()
