"""Executes a flow graph against the device on a background thread.

The runner never touches Tk.  It reports progress through an ``emit`` callback
that the UI drains from a queue on the main thread.
"""

from __future__ import annotations

import re
import threading
import time
from typing import Callable, Optional

from nodes import NODE_TYPES

LEVEL_RE = re.compile(r"level=(\d+)")

STEP_TICK = 0.001  # keeps a delay-free forever loop from pinning a core


class _Snapshot:
    """Immutable copy of the graph so edits during a run cannot corrupt it."""

    def __init__(self, flow) -> None:
        self.nodes = {
            node.id: (node.type, dict(node.params), dict(node.links))
            for node in flow.nodes.values()
        }
        self.start = next((nid for nid, (t, _, _) in self.nodes.items() if t == "start"),
                          None)


class FlowRunner(threading.Thread):
    def __init__(self, flow, link, emit: Callable[[str, object], None],
                 dry_run: bool = False) -> None:
        super().__init__(name="y-runner", daemon=True)
        self._graph = _Snapshot(flow)
        self._link = link
        self._emit = emit
        self._dry_run = dry_run
        self._stop = threading.Event()
        self._counters: dict[str, int] = {}

    def stop(self) -> None:
        self._stop.set()

    @property
    def stopping(self) -> bool:
        return self._stop.is_set()

    # -- main loop ----------------------------------------------------------

    def run(self) -> None:
        reason = "finished"
        try:
            if self._graph.start is None:
                self._emit("error", "no Start node in this flow")
                self._emit("finished", "error")
                return
            if not self._dry_run and not self._link.is_open:
                self._emit("error", "not connected to a device")
                self._emit("finished", "error")
                return

            current = self._graph.nodes[self._graph.start][2].get("next")
            if current is None:
                self._emit("log", "Start is not wired to anything")

            while current and not self._stop.is_set():
                node = self._graph.nodes.get(current)
                if node is None:
                    self._emit("error", f"dangling link to {current}")
                    reason = "error"
                    break

                self._emit("node", current)
                type_key, params, links = node
                try:
                    port = self._step(current, type_key, params)
                except _Failure as exc:
                    self._emit("error", str(exc))
                    reason = "error"
                    break

                if port is None:
                    break
                current = links.get(port)
                time.sleep(STEP_TICK)

            if self._stop.is_set():
                reason = "stopped"
        except Exception as exc:  # never let a UI thread hang on a dead runner
            self._emit("error", f"runner crashed: {exc!r}")
            reason = "error"
        finally:
            self._emit("node", None)
            self._emit("finished", reason)

    # -- per node behaviour -------------------------------------------------

    def _step(self, node_id: str, type_key: str, params: dict) -> Optional[str]:
        if type_key == "delay":
            self._sleep(int(float(params.get("ms", 500))) / 1000.0)
            return "next"

        if type_key == "log":
            self._emit("log", str(params.get("text", "")))
            return "next"

        if type_key == "repeat":
            return self._repeat(node_id, params)

        if type_key == "waitfor":
            return self._wait_for(params)

        spec = NODE_TYPES.get(type_key)
        if spec is None or spec.command is None:
            return "next"

        command = spec.command(params)
        response = self._send(command)
        if type_key == "read":
            match = LEVEL_RE.search(response)
            if match:
                self._emit("log", f"GPIO{int(float(params.get('pin', 0)))} reads "
                                  f"{match.group(1)}")
        return "next"

    def _repeat(self, node_id: str, params: dict) -> str:
        count = int(float(params.get("count", 0)))
        if count <= 0:
            return "loop"  # 0 means run until the user stops

        remaining = self._counters.get(node_id, count)
        if remaining > 0:
            self._counters[node_id] = remaining - 1
            return "loop"
        self._counters[node_id] = count  # rearm in case the node is re-entered
        return "done"

    def _wait_for(self, params: dict) -> str:
        pin = int(float(params.get("pin", 0)))
        want = 1 if str(params.get("level", "ON")).upper() in ("ON", "1", "HIGH") else 0
        timeout = int(float(params.get("timeout", 5000))) / 1000.0
        poll = max(0.005, int(float(params.get("poll", 50))) / 1000.0)

        deadline = time.monotonic() + timeout if timeout > 0 else None
        while not self._stop.is_set():
            response = self._send(f"DREAD {pin}")
            match = LEVEL_RE.search(response)
            if match and int(match.group(1)) == want:
                return "next"
            if deadline is not None and time.monotonic() >= deadline:
                self._emit("log", f"wait on GPIO{pin} timed out")
                return "timeout"
            self._sleep(poll)
        return "next"

    # -- helpers ------------------------------------------------------------

    def _send(self, command: str) -> str:
        if self._dry_run:
            self._emit("log", f"(dry run) {command}")
            return ""
        response = self._link.send(command)
        if not response.ok:
            raise _Failure(f"{command} -> {response.summary}")
        return response.line

    def _sleep(self, seconds: float) -> None:
        deadline = time.monotonic() + max(0.0, seconds)
        while not self._stop.is_set():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            time.sleep(min(0.05, remaining))


class _Failure(Exception):
    pass
