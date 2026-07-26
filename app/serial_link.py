"""Transport for the y_automation line protocol.

One background thread owns the serial port and demultiplexes replies by the
``<seq>:`` tag the firmware echoes back, so several callers (the UI thread and
the flow runner) can share a single link without stepping on each other.
"""

from __future__ import annotations

import itertools
import re
import threading
from dataclasses import dataclass, field
from typing import Callable, List, Optional

import serial
from serial.tools import list_ports

TAGGED = re.compile(r"^(\d+):(.*)$")

DEFAULT_BAUD = 115200
DEFAULT_TIMEOUT = 3.0


@dataclass
class Response:
    """Result of one request: the terminating line plus any DAT payload."""

    ok: bool
    line: str = ""
    data: List[str] = field(default_factory=list)
    error: str = ""

    @property
    def summary(self) -> str:
        return self.error if not self.ok else self.line


class _Pending:
    __slots__ = ("data", "event", "response")

    def __init__(self) -> None:
        self.data: List[str] = []
        self.event = threading.Event()
        self.response: Optional[Response] = None


def available_ports() -> List[str]:
    return [p.device for p in list_ports.comports()]


def describe_ports() -> List[str]:
    out = []
    for p in list_ports.comports():
        desc = (p.description or "").strip()
        out.append(f"{p.device} - {desc}" if desc and desc != "n/a" else p.device)
    return out


class SerialLink:
    def __init__(
        self,
        on_line: Optional[Callable[[str, str], None]] = None,
        on_disconnect: Optional[Callable[[str], None]] = None,
    ) -> None:
        # on_line(direction, text) where direction is "tx", "rx" or "sys".
        self._on_line = on_line or (lambda direction, text: None)
        self._on_disconnect = on_disconnect or (lambda reason: None)

        self._ser: Optional[serial.Serial] = None
        self._reader: Optional[threading.Thread] = None
        self._running = threading.Event()
        self._pending: dict[int, _Pending] = {}
        self._lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._seq = itertools.count(1)

    # -- connection ---------------------------------------------------------

    @property
    def is_open(self) -> bool:
        return self._ser is not None and self._ser.is_open

    def open(self, port: str, baud: int = DEFAULT_BAUD) -> None:
        self.close()
        ser = serial.Serial()
        ser.port = port
        ser.baudrate = baud
        ser.timeout = 0.05
        ser.write_timeout = 2.0
        ser.open()

        # RTS/DTR drive EN/IO0 on the DevKitM-1 auto-reset circuit.  Park both
        # inactive so simply connecting does not reboot the board.
        try:
            ser.dtr = False
            ser.rts = False
        except (OSError, serial.SerialException):
            pass
        ser.reset_input_buffer()

        self._ser = ser
        self._running.set()
        self._reader = threading.Thread(target=self._read_loop, name="y-serial", daemon=True)
        self._reader.start()
        self._on_line("sys", f"connected to {port} @ {baud}")

    def close(self) -> None:
        self._running.clear()
        reader, self._reader = self._reader, None
        if reader and reader.is_alive() and reader is not threading.current_thread():
            reader.join(timeout=1.0)

        ser, self._ser = self._ser, None
        if ser is not None:
            try:
                ser.close()
            except (OSError, serial.SerialException):
                pass
            self._on_line("sys", "disconnected")

        with self._lock:
            pending = list(self._pending.values())
            self._pending.clear()
        for p in pending:
            p.response = Response(False, error="link closed")
            p.event.set()

    def pulse_reset(self) -> None:
        """Toggle the auto-reset lines to restart the board."""
        if not self.is_open:
            return
        try:
            self._ser.dtr = False
            self._ser.rts = True
            threading.Event().wait(0.05)
            self._ser.rts = False
            self._on_line("sys", "reset pulse sent")
        except (OSError, serial.SerialException) as exc:
            self._on_line("sys", f"reset failed: {exc}")

    # -- requests -----------------------------------------------------------

    def send(self, command: str, timeout: float = DEFAULT_TIMEOUT) -> Response:
        """Sends one command and waits for its OK/ERR terminator."""
        command = command.strip()
        if not command:
            return Response(False, error="empty command")
        if not self.is_open:
            return Response(False, error="not connected")

        seq = next(self._seq)
        pending = _Pending()
        with self._lock:
            self._pending[seq] = pending

        payload = f"{seq}:{command}\n".encode("ascii", "replace")
        try:
            with self._write_lock:
                self._ser.write(payload)
                self._ser.flush()
        except (OSError, serial.SerialException) as exc:
            with self._lock:
                self._pending.pop(seq, None)
            return Response(False, error=f"write failed: {exc}")

        self._on_line("tx", command)

        if not pending.event.wait(timeout):
            with self._lock:
                self._pending.pop(seq, None)
            return Response(False, error=f"timeout waiting for reply to {command!r}")

        with self._lock:
            self._pending.pop(seq, None)
        return pending.response or Response(False, error="no response")

    # -- reader thread ------------------------------------------------------

    def _read_loop(self) -> None:
        buf = bytearray()
        ser = self._ser
        while self._running.is_set() and ser is not None:
            try:
                chunk = ser.read(max(1, ser.in_waiting))
            except (OSError, serial.SerialException) as exc:
                if self._running.is_set():
                    self._running.clear()
                    self._on_line("sys", f"link error: {exc}")
                    self._on_disconnect(str(exc))
                return
            if not chunk:
                continue

            buf.extend(chunk)
            while b"\n" in buf:
                raw, _, rest = buf.partition(b"\n")
                buf = bytearray(rest)
                text = raw.decode("utf-8", "replace").strip("\r\n\t ")
                if text:
                    self._dispatch(text)

    def _dispatch(self, text: str) -> None:
        self._on_line("rx", text)

        match = TAGGED.match(text)
        if not match:
            return  # banner, RDY or an untagged async notice

        seq = int(match.group(1))
        body = match.group(2).strip()

        with self._lock:
            pending = self._pending.get(seq)
        if pending is None:
            return

        if body.startswith("DAT "):
            pending.data.append(body[4:].strip())
            return
        if body.startswith("OK"):
            pending.response = Response(True, body[2:].strip(), pending.data)
        elif body.startswith("ERR"):
            pending.response = Response(False, body, pending.data, error=body[3:].strip())
        else:
            return
        pending.event.set()
