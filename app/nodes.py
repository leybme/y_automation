"""Declarative catalogue of the blocks that can be dropped on the canvas.

Each node type knows its parameters, how to render itself and - for the plain
ones - which firmware command it maps to.  Control-flow types (start, delay,
repeat, waitfor, log) return None and are interpreted by the runner.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional, Sequence

LEVELS = ("ON", "OFF", "TOGGLE")
FUNCS = ("OUT", "IN", "IN_PULLUP", "IN_PULLDOWN", "SERVO", "FREQ", "NONE")

# Pins the stock firmware build allows (Y_PIN_MASK = GPIO0..GPIO10).
DEFAULT_PINS = tuple(range(0, 11))


@dataclass(frozen=True)
class Param:
    key: str
    label: str
    kind: str  # "int" | "float" | "choice" | "text"
    default: object
    choices: Sequence[str] = ()
    lo: Optional[float] = None
    hi: Optional[float] = None
    unit: str = ""


@dataclass(frozen=True)
class NodeType:
    key: str
    label: str
    group: str
    color: str
    params: Sequence[Param] = ()
    outs: Sequence[str] = ("next",)
    summary: Optional[Callable[[dict], str]] = None
    command: Optional[Callable[[dict], str]] = None
    unique: bool = False
    help: str = ""

    def describe(self, params: dict) -> str:
        if self.summary:
            return self.summary(params)
        return ""


def _num(params: dict, key: str, default=0):
    value = params.get(key, default)
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _int(params: dict, key: str, default=0) -> int:
    return int(_num(params, key, default))


def _fmt(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else f"{value:g}"


NODE_TYPES: dict[str, NodeType] = {}


def _register(node_type: NodeType) -> NodeType:
    NODE_TYPES[node_type.key] = node_type
    return node_type


# -- flow ------------------------------------------------------------------

_register(NodeType(
    key="start",
    label="Start",
    group="Flow",
    color="#2f855a",
    outs=("next",),
    unique=True,
    summary=lambda p: "entry point",
    help="Where the sequence begins. Exactly one per flow.",
))

_register(NodeType(
    key="delay",
    label="Delay",
    group="Flow",
    color="#4a5568",
    params=(Param("ms", "Duration", "int", 500, lo=0, hi=3_600_000, unit="ms"),),
    summary=lambda p: f"wait {_int(p, 'ms', 500)} ms",
    help="Pause the sequence without touching the device.",
))

_register(NodeType(
    key="repeat",
    label="Repeat",
    group="Flow",
    color="#6b46c1",
    params=(Param("count", "Iterations", "int", 3, lo=0, hi=1_000_000,
                  unit="0 = forever"),),
    outs=("loop", "done"),
    summary=lambda p: ("loop forever" if _int(p, "count", 3) == 0
                       else f"loop {_int(p, 'count', 3)}x"),
    help="Send 'loop' into the body and wire the body's end back here. "
         "Once the counter runs out control leaves through 'done'.",
))

_register(NodeType(
    key="waitfor",
    label="Wait For Pin",
    group="Flow",
    color="#805ad5",
    params=(
        Param("pin", "Pin", "int", 3, lo=0, hi=21),
        Param("level", "Level", "choice", "ON", choices=("ON", "OFF")),
        Param("timeout", "Timeout", "int", 5000, lo=0, hi=600_000, unit="ms"),
        Param("poll", "Poll every", "int", 50, lo=5, hi=10_000, unit="ms"),
    ),
    outs=("next", "timeout"),
    summary=lambda p: f"GPIO{_int(p, 'pin', 3)} == {p.get('level', 'ON')}",
    help="Polls a digital input until it matches, then continues. "
         "On expiry control leaves through 'timeout'.",
))

_register(NodeType(
    key="log",
    label="Log Message",
    group="Flow",
    color="#4a5568",
    params=(Param("text", "Message", "text", "checkpoint"),),
    summary=lambda p: str(p.get("text", ""))[:28],
    help="Writes a line into the console. Useful for marking progress.",
))

# -- digital ---------------------------------------------------------------

_register(NodeType(
    key="digital",
    label="Digital Write",
    group="Digital",
    color="#2b6cb0",
    params=(
        Param("pin", "Pin", "int", 2, lo=0, hi=21),
        Param("level", "Level", "choice", "ON", choices=LEVELS),
    ),
    summary=lambda p: f"GPIO{_int(p, 'pin', 2)} -> {p.get('level', 'ON')}",
    command=lambda p: f"DOUT {_int(p, 'pin', 2)} {p.get('level', 'ON')}",
    help="Drives a pin high, low or inverts it. The pin is switched to output "
         "automatically.",
))

_register(NodeType(
    key="read",
    label="Digital Read",
    group="Digital",
    color="#2c7a7b",
    params=(Param("pin", "Pin", "int", 3, lo=0, hi=21),),
    summary=lambda p: f"read GPIO{_int(p, 'pin', 3)}",
    command=lambda p: f"DREAD {_int(p, 'pin', 3)}",
    help="Samples a digital input and reports the level in the console.",
))

_register(NodeType(
    key="mode",
    label="Set Pin Mode",
    group="Digital",
    color="#2c5282",
    params=(
        Param("pin", "Pin", "int", 2, lo=0, hi=21),
        Param("func", "Function", "choice", "OUT", choices=FUNCS),
    ),
    summary=lambda p: f"GPIO{_int(p, 'pin', 2)} = {p.get('func', 'OUT')}",
    command=lambda p: f"MODE {_int(p, 'pin', 2)} {p.get('func', 'OUT')}",
    help="Reconfigures what a pin is used for without driving it.",
))

_register(NodeType(
    key="default",
    label="Set Boot Default",
    group="Digital",
    color="#975a16",
    params=(
        Param("pin", "Pin", "int", 2, lo=0, hi=21),
        Param("level", "Level", "choice", "ON", choices=("ON", "OFF")),
    ),
    summary=lambda p: f"GPIO{_int(p, 'pin', 2)} boots {p.get('level', 'ON')}",
    command=lambda p: f"DEF {_int(p, 'pin', 2)} {p.get('level', 'ON')}",
    help="Stores the power-on level of an output in flash. Applied by the "
         "firmware before it accepts any command.",
))

_register(NodeType(
    key="stop",
    label="Release Pin",
    group="Digital",
    color="#718096",
    params=(Param("pin", "Pin", "int", 2, lo=0, hi=21),),
    summary=lambda p: f"release GPIO{_int(p, 'pin', 2)}",
    command=lambda p: f"STOP {_int(p, 'pin', 2)}",
    help="Detaches servo/frequency hardware and parks the pin as an input.",
))

_register(NodeType(
    key="alloff",
    label="All Off",
    group="Digital",
    color="#9b2c2c",
    summary=lambda p: "every output low",
    command=lambda p: "ALLOFF",
    help="Panic button: drives every output low and frees all PWM channels.",
))

# -- servo -----------------------------------------------------------------

_register(NodeType(
    key="servo",
    label="Servo Angle",
    group="Servo",
    color="#b7791f",
    params=(
        Param("pin", "Pin", "int", 4, lo=0, hi=21),
        Param("angle", "Angle", "float", 90, lo=0, hi=180, unit="deg"),
    ),
    summary=lambda p: f"GPIO{_int(p, 'pin', 4)} -> {_fmt(_num(p, 'angle', 90))} deg",
    command=lambda p: f"SERVO {_int(p, 'pin', 4)} {_fmt(_num(p, 'angle', 90))}",
    help="Attaches the pin to a 50 Hz servo channel and commands an angle.",
))

_register(NodeType(
    key="servo_us",
    label="Servo Pulse",
    group="Servo",
    color="#b7791f",
    params=(
        Param("pin", "Pin", "int", 4, lo=0, hi=21),
        Param("us", "Pulse width", "int", 1500, lo=100, hi=19000, unit="us"),
    ),
    summary=lambda p: f"GPIO{_int(p, 'pin', 4)} -> {_int(p, 'us', 1500)} us",
    command=lambda p: f"SERVOUS {_int(p, 'pin', 4)} {_int(p, 'us', 1500)}",
    help="Commands a raw pulse width, bypassing the angle mapping.",
))

_register(NodeType(
    key="servo_cfg",
    label="Servo Range",
    group="Servo",
    color="#975a16",
    params=(
        Param("pin", "Pin", "int", 4, lo=0, hi=21),
        Param("min_us", "Min pulse", "int", 500, lo=100, hi=19000, unit="us"),
        Param("max_us", "Max pulse", "int", 2500, lo=100, hi=19000, unit="us"),
    ),
    summary=lambda p: f"GPIO{_int(p, 'pin', 4)} {_int(p, 'min_us', 500)}-"
                      f"{_int(p, 'max_us', 2500)} us",
    command=lambda p: (f"SERVOCFG {_int(p, 'pin', 4)} {_int(p, 'min_us', 500)} "
                       f"{_int(p, 'max_us', 2500)}"),
    help="Calibrates the pulse range that maps to 0 and 180 degrees.",
))

# -- frequency -------------------------------------------------------------

_register(NodeType(
    key="freq",
    label="Frequency Out",
    group="Frequency",
    color="#c05621",
    params=(
        Param("pin", "Pin", "int", 5, lo=0, hi=21),
        Param("hz", "Frequency", "int", 1000, lo=1, hi=10_000_000, unit="Hz"),
        Param("duty", "Duty", "float", 50, lo=0, hi=100, unit="%"),
    ),
    summary=lambda p: f"GPIO{_int(p, 'pin', 5)} {_int(p, 'hz', 1000)} Hz "
                      f"{_fmt(_num(p, 'duty', 50))}%",
    command=lambda p: (f"FREQ {_int(p, 'pin', 5)} {_int(p, 'hz', 1000)} "
                       f"{_fmt(_num(p, 'duty', 50))}"),
    help="Turns the pin into a square wave generator on an LEDC channel.",
))

_register(NodeType(
    key="duty",
    label="Change Duty",
    group="Frequency",
    color="#dd6b20",
    params=(
        Param("pin", "Pin", "int", 5, lo=0, hi=21),
        Param("duty", "Duty", "float", 25, lo=0, hi=100, unit="%"),
    ),
    summary=lambda p: f"GPIO{_int(p, 'pin', 5)} duty {_fmt(_num(p, 'duty', 25))}%",
    command=lambda p: f"DUTY {_int(p, 'pin', 5)} {_fmt(_num(p, 'duty', 25))}",
    help="Adjusts the duty cycle of a pin already running Frequency Out.",
))

# -- raw -------------------------------------------------------------------

_register(NodeType(
    key="raw",
    label="Raw Command",
    group="Advanced",
    color="#4a5568",
    params=(Param("text", "Command", "text", "PING"),),
    summary=lambda p: str(p.get("text", "PING"))[:28],
    command=lambda p: str(p.get("text", "PING")),
    help="Sends a literal protocol line. Anything from HELP works here.",
))


def groups() -> list[tuple[str, list[NodeType]]]:
    """Node types bucketed by palette group, in declaration order."""
    ordered: dict[str, list[NodeType]] = {}
    for node_type in NODE_TYPES.values():
        ordered.setdefault(node_type.group, []).append(node_type)
    return list(ordered.items())


def defaults_for(key: str) -> dict:
    node_type = NODE_TYPES[key]
    return {p.key: p.default for p in node_type.params}
