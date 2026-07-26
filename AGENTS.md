# agents.md — AI Agent Guide for y_automation

This document helps AI agents quickly understand and control **y_automation** ESP32-C3 hardware
through the USB serial protocol using Python.

---

## Quick Start

### Prerequisites

```bash
pip install pyserial
```

### Connect and Blink an LED (copy-paste ready)

```python
import serial, time

ser = serial.Serial("COM3", 115200, timeout=3)
time.sleep(0.1)          # let the port settle
ser.dtr = False          # prevent accidental reset
ser.rts = False

def cmd(text, timeout=5.0):
    """Send one command, return (ok: bool, line: str, data: list[str])."""
    seq = 1
    ser.write(f"{seq}:{text}\n".encode())
    data, line, ok = [], "", False
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        raw = ser.readline().decode().strip()
        if not raw:
            continue          # readline() hit its own timeout; the deadline bounds us
        if raw.startswith(f"{seq}:DAT "):
            data.append(raw.split("DAT ", 1)[1])
        elif raw.startswith(f"{seq}:OK"):
            ok, line = True, raw.split("OK", 1)[1].strip()
            break
        elif raw.startswith(f"{seq}:ERR"):
            line = raw.split("ERR", 1)[1].strip()
            break
    else:
        raise TimeoutError(f"no reply to {text!r} within {timeout}s")
    return ok, line, data

# --- Example: blink GPIO 2 five times ---
for _ in range(5):
    cmd("DOUT 2 1")   # ON
    time.sleep(0.5)
    cmd("DOUT 2 0")   # OFF
    time.sleep(0.5)

cmd("ALLOFF")
ser.close()
```

---

## Reusable Helper Class

Copy this into your script for a clean, importable interface:

```python
import serial
import time
import itertools


class YBoard:
    """Python client for the y_automation ESP32-C3 serial protocol."""

    def __init__(self, port: str, baud: int = 115200, timeout: float = 3.0):
        self.ser = serial.Serial(port, baud, timeout=timeout)
        time.sleep(0.1)
        self.ser.dtr = False
        self.ser.rts = False
        self.ser.reset_input_buffer()
        self._seq = itertools.count(1)

    # --- connection ---

    def close(self):
        self.ser.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # --- protocol ---

    def send(self, command: str, timeout: float = 5.0) -> dict:
        """Send a command, return {"ok": bool, "line": str, "data": list[str]}.

        Raises TimeoutError if the device never terminates the reply, so a
        silent or wedged board surfaces instead of hanging the caller.
        """
        seq = next(self._seq)
        self.ser.write(f"{seq}:{command}\n".encode())
        self.ser.flush()
        data, line, ok = [], "", False
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            raw = self.ser.readline().decode("utf-8", "replace").strip()
            if not raw:
                continue      # readline() hit its own timeout; the deadline bounds us
            prefix = f"{seq}:"
            if raw.startswith(f"{prefix}DAT "):
                data.append(raw[len(prefix) + 4:])
            elif raw.startswith(f"{prefix}OK"):
                ok, line = True, raw[len(prefix) + 2:].strip()
                break
            elif raw.startswith(f"{prefix}ERR"):
                line = raw[len(prefix) + 3:].strip()
                break
            # else: banner / RDY / untagged — skip
        else:
            raise TimeoutError(f"no reply to {command!r} within {timeout}s")
        return {"ok": ok, "line": line, "data": data}

    def assert_ok(self, command: str) -> dict:
        """Send a command and raise on error."""
        r = self.send(command)
        if not r["ok"]:
            raise RuntimeError(f"{command!r} failed: {r['line']}")
        return r

    # --- device info ---

    def ping(self) -> bool:
        return self.send("PING")["ok"]

    def id(self) -> dict:
        """Returns parsed ID fields as a dict."""
        r = self.assert_ok("ID")
        fields = {}
        for part in r["line"].split():
            if "=" in part:
                k, v = part.split("=", 1)
                fields[k] = v
        return fields

    def help(self) -> list[str]:
        return self.assert_ok("HELP")["data"]

    def state(self, pin: int | None = None) -> list[str]:
        cmd = f"STATE {pin}" if pin is not None else "STATE"
        return self.assert_ok(cmd)["data"]

    # --- digital I/O ---

    def digital_write(self, pin: int, level) -> dict:
        """level: 0/1/'TOGGLE'/'ON'/'OFF'/'HIGH'/'LOW'."""
        return self.assert_ok(f"DOUT {pin} {level}")

    def digital_read(self, pin: int) -> int:
        """Returns 0 or 1."""
        r = self.assert_ok(f"DREAD {pin}")
        for part in r["line"].split():
            if part.startswith("level="):
                return int(part.split("=")[1])
        raise RuntimeError(f"could not parse DREAD reply: {r['line']}")

    def set_mode(self, pin: int, mode: str) -> dict:
        """mode: NONE / OUT / IN / IN_PULLUP / IN_PULLDOWN / SERVO / FREQ."""
        return self.assert_ok(f"MODE {pin} {mode}")

    def all_off(self) -> dict:
        """Drive every output low and free all LEDC channels."""
        return self.assert_ok("ALLOFF")

    # --- servo ---

    def servo(self, pin: int, angle: float) -> dict:
        """Set servo angle (0–180 degrees, one decimal)."""
        return self.assert_ok(f"SERVO {pin} {angle}")

    def servo_pulse(self, pin: int, us: int) -> dict:
        """Set raw servo pulse width in microseconds."""
        return self.assert_ok(f"SERVOUS {pin} {us}")

    def servo_config(self, pin: int, min_us: int, max_us: int) -> dict:
        """Calibrate the pulse range for 0 and 180 degrees."""
        return self.assert_ok(f"SERVOCFG {pin} {min_us} {max_us}")

    # --- frequency / PWM ---

    def freq(self, pin: int, hz: int, duty: float = 50) -> dict:
        """Square wave output. duty: 0–100 %."""
        return self.assert_ok(f"FREQ {pin} {hz} {duty}")

    def duty(self, pin: int, duty: float) -> dict:
        """Change duty of a pin already running FREQ."""
        return self.assert_ok(f"DUTY {pin} {duty}")

    def stop(self, pin: int) -> dict:
        """Detach servo/PWM, park pin as input."""
        return self.assert_ok(f"STOP {pin}")

    # --- boot defaults (NVS persistence) ---

    def def_set(self, pin: int, level: int) -> dict:
        """Persist the power-on level of an output."""
        return self.assert_ok(f"DEF {pin} {level}")

    def def_snap(self) -> dict:
        """Persist the entire live configuration as boot state."""
        return self.assert_ok("DEFSNAP")

    def def_get(self) -> list[str]:
        """List stored boot defaults."""
        return self.assert_ok("DEFGET")["data"]

    def def_clear(self, pin: int | None = None) -> dict:
        """Forget one stored default, or all."""
        cmd = f"DEFCLR {pin}" if pin is not None else "DEFCLR"
        return self.assert_ok(cmd)

    def def_apply(self) -> dict:
        """Re-apply stored defaults now."""
        return self.assert_ok("DEFAPPLY")

    # --- misc ---

    def reboot(self) -> dict:
        return self.assert_ok("REBOOT")

    def raw(self, command: str) -> dict:
        """Send any arbitrary protocol line."""
        return self.assert_ok(command)
```

---

## Usage Examples with the Helper Class

### Basic Connection

```python
with YBoard("COM3") as board:
    print(board.ping())           # True
    print(board.id())             # {'name': 'y_automation', 'ver': '1.0.0', ...}
```

### Digital I/O

```python
with YBoard("COM3") as b:
    b.set_mode(2, "OUT")
    b.digital_write(2, 1)         # HIGH
    time.sleep(1)
    b.digital_write(2, 0)         # LOW

    b.set_mode(3, "IN_PULLUP")
    level = b.digital_read(3)     # 0 or 1
    print(f"Pin 3 is {'HIGH' if level else 'LOW'}")
```

### Blink with Toggle

```python
import time

with YBoard("COM3") as b:
    b.set_mode(2, "OUT")
    for _ in range(10):
        b.digital_write(2, "TOGGLE")
        time.sleep(0.5)
    b.all_off()
```

### Servo Sweep

```python
import time

with YBoard("COM3") as b:
    b.servo_config(4, 500, 2500)
    for angle in range(0, 181, 5):
        b.servo(4, angle)
        time.sleep(0.05)
    for angle in range(180, -1, -5):
        b.servo(4, angle)
        time.sleep(0.05)
    b.stop(4)
```

### Frequency Generator

```python
import time

with YBoard("COM3") as b:
    b.freq(5, 1000)               # 1 kHz, 50% duty
    time.sleep(2)
    b.duty(5, 25)                 # change to 25% duty
    time.sleep(2)
    b.stop(5)
```

### Read All Pin States

```python
with YBoard("COM3") as b:
    lines = b.state()
    for line in lines:
        print(line)
    # pin=2 func=OUT level=1
    # pin=4 func=SERVO angle=90.0 us=1500 min=500 max=2500
```

### Persist Configuration Across Reboots

```python
with YBoard("COM3") as b:
    b.set_mode(2, "OUT")
    b.digital_write(2, 1)
    b.servo_config(4, 600, 2400)
    b.servo(4, 90)
    b.def_snap()                  # save everything as boot state
    b.reboot()
```

### Scan Available Ports

```python
from serial.tools import list_ports

# List all serial ports
for p in list_ports.comports():
    label = p.description or "n/a"
    vid = f"0x{p.vid:04X}" if p.vid else "---"
    print(f"{p.device:12s}  VID={vid}  {label}")
```

### Auto-Detect the Board

```python
from serial.tools import list_ports

# Most specific first: native USB Serial/JTAG, then the USB-to-UART bridges.
ESP_VIDS = (0x303A, 0x10C4, 0x1A86, 0x0403)

def find_board():
    """Return the port of the most likely ESP32 board, or None if none is attached.

    Only ports whose USB vendor id is a known ESP32 one are considered, so an
    unrelated port (a Bluetooth COM port, say) is never returned as a board.
    """
    ranked = sorted(
        (p for p in list_ports.comports() if p.vid in ESP_VIDS),
        key=lambda p: (ESP_VIDS.index(p.vid), p.device),
    )
    return ranked[0].device if ranked else None

port = find_board()
if port:
    with YBoard(port) as b:
        print(b.id())
else:
    print("No board found")
```

### Multi-Threaded Concurrent Commands

The firmware's `<seq>:` tag and queue allow multiple callers to share one
connection. The `YBoard` class is NOT thread-safe by itself. Wrap it:

```python
import threading
import time

with YBoard("COM3") as b:
    lock = threading.Lock()

    def safe_send(cmd):
        with lock:
            return b.send(cmd)

    def blinker():
        for _ in range(20):
            safe_send("DOUT 2 TOGGLE")
            time.sleep(0.3)

    def servo_waver():
        for angle in range(0, 181, 10):
            safe_send(f"SERVO 4 {angle}")
            time.sleep(0.1)
        for angle in range(180, -1, -10):
            safe_send(f"SERVO 4 {angle}")
            time.sleep(0.1)

    t1 = threading.Thread(target=blinker)
    t2 = threading.Thread(target=servo_waver)
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    b.all_off()
```

### Sending Raw Protocol Commands

If a command is not wrapped in `YBoard`, use `raw()`:

```python
with YBoard("COM3") as b:
    r = b.raw("HELP")
    for line in r["data"]:
        print(line)

    r = b.raw("ID")
    print(r["line"])
```

---

## Protocol Reference

### Wire Format

- **Baud:** 115200
- **Line ending:** `\n` (or `\r\n`)
- **Request:** `[<seq>:]<VERB> [args...]` — the optional `<seq>` tag is echoed on every reply
- **Reply:** zero or more `DAT` lines, then exactly one `OK` or `ERR` line
- **Verbs are case-insensitive**

### Command Cheat Sheet

| Command | Args | Description |
|---|---|---|
| `PING` | — | Liveness check → `OK PING PONG` |
| `ID` | — | Firmware name, version, chip, pin mask |
| `HELP` | — | List all commands (one `DAT` line each) |
| `STATE [pin]` | optional pin | Live config of one or all pins |
| `MODE` | `<pin> <func>` | Set pin function: `NONE OUT IN IN_PULLUP IN_PULLDOWN SERVO FREQ` |
| `DOUT` | `<pin> <0\|1\|TOGGLE>` | Digital output (`ON OFF HIGH LOW` also work) |
| `DREAD` | `<pin>` | Read digital input → `level=0` or `level=1` |
| `SERVO` | `<pin> <deg>` | 0–180°, one decimal accepted |
| `SERVOUS` | `<pin> <us>` | Raw pulse width in µs |
| `SERVOCFG` | `<pin> <min> <max>` | Calibrate pulse range (µs) for 0° and 180° |
| `FREQ` | `<pin> <hz> [duty%]` | Square wave, duty defaults to 50% |
| `DUTY` | `<pin> <duty%>` | Change duty on a running FREQ pin |
| `STOP` | `<pin>` | Detach servo/PWM, park as input |
| `ALLOFF` | — | All outputs low, free all LEDC channels |
| `DEF` | `<pin> <0\|1>` | Persist power-on level in NVS |
| `DEFSNAP` | — | Persist entire live config as boot state |
| `DEFCLR [pin]` | optional pin | Forget one or all stored defaults |
| `DEFGET` | — | List stored defaults |
| `DEFAPPLY` | — | Re-apply stored defaults now |
| `REBOOT` | — | Restart the device |

### Error Codes

| Error | Meaning |
|---|---|
| `UNKNOWN_CMD` | Verb not recognized |
| `BAD_ARGC` | Wrong number of arguments |
| `BAD_PIN` | Pin outside allowed mask (see below) |
| `BAD_ARG` | Value out of range |
| `BAD_LEVEL` | Invalid digital level |
| `BAD_MODE` | Unknown pin mode |
| `BAD_ANGLE` | Servo angle not a number or out of range |
| `BAD_US` | Pulse width not valid |
| `BAD_HZ` | Frequency not valid |
| `BAD_DUTY` | Duty not valid |
| `WRONG_FUNC` | Pin is not configured for this operation |
| `NO_LEDC_CHANNEL` | All 6 PWM channels in use (`STOP` one first) |
| `HW_ERROR` | Hardware refused the request |
| `NVS_WRITE` | Flash write failed |
| `QUEUE_FULL` | Command queue full (24 deep), retry |
| `LINE_TOO_LONG` | Input line exceeds buffer |

### Available Pins

Default mask `0x000007FF` → **GPIO 0–10** are usable.

| Pins | Status |
|---|---|
| GPIO 0–10 | Available for general use |
| GPIO 11–17 | Reserved (SPI flash — driving them crashes the chip) |
| GPIO 18–19 | Native USB D-/D+ (on USB-CDC builds) |
| GPIO 20–21 | UART0 RX/TX |

**Strapping pins:** GPIO 2, 8, 9 are sampled at reset. GPIO 9 held low enters
bootloader. GPIO 8 drives the on-board LED (DevKitM-1). Safe to use as outputs
once running — just don't hold them in an unusual state during reset.

**LEDC limit:** The ESP32-C3 has **6 LEDC channels** total. Servo and FREQ
outputs each consume one. Digital I/O does not use LEDC.

### Frequency Output Resolution

| Frequency | Duty Resolution | Steps |
|---|---|---|
| 3 Hz – 2.4 kHz | 14 bits | 16,384 |
| 10 kHz | 11 bits | 2,048 |
| 100 kHz | 8 bits | 256 |
| 1 MHz | 5 bits | 32 |
| 10 MHz | 2 bits | 4 |

Minimum: **3 Hz**. 1–2 Hz are not reachable (`HW_ERROR`).

---

## Troubleshooting

| Problem | Fix |
|---|---|
| No reply at all | Wrong port or wrong firmware variant. Check `pio device monitor` for `RDY`. |
| `ERR BAD_PIN` | Pin outside mask. Default is GPIO 0–10. |
| `ERR NO_LEDC_CHANNEL` | All 6 PWM channels used. `STOP` a pin first. |
| Servo jitters | Power the servo from a separate supply, not the board's 3V3. |
| Board resets on connect | Ensure DTR/RTS are parked low (the helper class does this). |
| `ERR QUEUE_FULL` | Commands sent too fast. Add a small delay or wait for each reply. |

---

## File Layout for Reference

```
y_automation/
├── platformio.ini              # build config (two envs: uart0 and usbcdc)
├── include/
│   ├── config.h                # pin mask, limits, task tuning
│   ├── command.h               # Command struct and CmdType enum
│   ├── protocol.h              # parse / execute declarations
│   └── ...                     # pin_control, persist, io, tasks headers
├── src/
│   ├── main.cpp                # boot, NVS restore, task launch
│   ├── tasks.cpp               # usb_reader + cmd_worker tasks + queue
│   ├── protocol.cpp            # line parser + command dispatch
│   ├── pin_control.cpp         # GPIO / LEDC / servo back end
│   ├── persist.cpp             # NVS boot defaults
│   └── io.cpp                  # mutex-protected serial output
├── app/
│   ├── main.py                 # desktop studio (Tkinter GUI)
│   ├── serial_link.py          # Python serial transport
│   ├── nodes.py                # block definitions for the flow editor
│   ├── flow_canvas.py          # canvas widget
│   ├── runner.py               # flow execution engine
│   ├── requirements.txt        # pyserial
│   └── examples/               # example flow JSON files
└── agents.md                   # this file