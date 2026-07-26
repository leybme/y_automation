"""Drag-and-drop flow editor built on a plain Tk canvas.

Nodes are dropped from the palette, dragged around by their body and wired up
by dragging from an output port onto another node.  The model (``Flow``) is
kept separate from the drawing so it serialises to JSON cleanly.
"""

from __future__ import annotations

import itertools
import json
import tkinter as tk
from typing import Callable, Optional

from nodes import NODE_TYPES, defaults_for

NODE_W = 206
HEAD_H = 26
BODY_H = 26
PORT_H = 19
PAD_B = 8

# Ports are drawn with radius 6 but grabbed with a larger radius, so they stay
# easy to hit without having to aim.
PORT_GRAB = 11

CANVAS_BG = "#eef1f6"
GRID = "#dde3ec"
BODY_FILL = "#ffffff"
BODY_LINE = "#b6c0cf"
SEL_LINE = "#1a73e8"
ACTIVE_LINE = "#38a169"
LINK_COLOR = "#7c8899"
PORT_FILL = "#ffffff"

_ids = itertools.count(1)


def _new_id() -> str:
    return f"n{next(_ids)}"


class FlowNode:
    def __init__(self, node_id: str, type_key: str, x: float, y: float,
                 params: Optional[dict] = None, links: Optional[dict] = None) -> None:
        self.id = node_id
        self.type = type_key
        self.x = float(x)
        self.y = float(y)
        self.params = dict(defaults_for(type_key))
        if params:
            self.params.update(params)
        self.links: dict[str, str] = dict(links or {})

    @property
    def spec(self):
        return NODE_TYPES[self.type]

    @property
    def height(self) -> int:
        return HEAD_H + BODY_H + PORT_H * len(self.spec.outs) + PAD_B

    def port_xy(self, port: str) -> tuple[float, float]:
        index = list(self.spec.outs).index(port)
        return (self.x + NODE_W, self.y + HEAD_H + BODY_H + PORT_H * index + PORT_H / 2)

    def inlet_xy(self) -> tuple[float, float]:
        return (self.x, self.y + HEAD_H / 2)

    def to_dict(self) -> dict:
        return {"id": self.id, "type": self.type, "x": self.x, "y": self.y,
                "params": self.params, "links": self.links}


class Flow:
    def __init__(self) -> None:
        self.nodes: dict[str, FlowNode] = {}

    def add(self, type_key: str, x: float, y: float) -> FlowNode:
        node = FlowNode(_new_id(), type_key, x, y)
        self.nodes[node.id] = node
        return node

    def remove(self, node_id: str) -> None:
        self.nodes.pop(node_id, None)
        for node in self.nodes.values():
            for port, target in list(node.links.items()):
                if target == node_id:
                    del node.links[port]

    def start_node(self) -> Optional[FlowNode]:
        for node in self.nodes.values():
            if node.type == "start":
                return node
        return None

    def to_dict(self) -> dict:
        return {"version": 1, "nodes": [n.to_dict() for n in self.nodes.values()]}

    def load(self, payload: dict) -> None:
        self.nodes.clear()
        highest = 0
        for raw in payload.get("nodes", []):
            type_key = raw.get("type")
            if type_key not in NODE_TYPES:
                continue
            node = FlowNode(raw.get("id") or _new_id(), type_key,
                            raw.get("x", 40), raw.get("y", 40),
                            raw.get("params"), raw.get("links"))
            self.nodes[node.id] = node
            if node.id.startswith("n") and node.id[1:].isdigit():
                highest = max(highest, int(node.id[1:]))
        # Keep generated ids from colliding with the loaded ones.
        global _ids
        _ids = itertools.count(highest + 1)

        # Drop links that point at nodes which did not survive the load.
        for node in self.nodes.values():
            for port, target in list(node.links.items()):
                if target not in self.nodes or port not in node.spec.outs:
                    del node.links[port]

    def dumps(self) -> str:
        return json.dumps(self.to_dict(), indent=2)


class FlowCanvas(tk.Canvas):
    def __init__(self, master, on_select: Optional[Callable] = None,
                 on_change: Optional[Callable] = None, **kw) -> None:
        super().__init__(master, background=CANVAS_BG, highlightthickness=0, **kw)
        self.flow = Flow()
        self.selected: Optional[str] = None
        self.active: Optional[str] = None
        self._on_select = on_select or (lambda node: None)
        self._on_change = on_change or (lambda: None)

        self._drag_node: Optional[str] = None
        self._drag_off = (0.0, 0.0)
        self._link_from: Optional[tuple[str, str]] = None
        self._panning = False

        self.configure(scrollregion=(0, 0, 2400, 1800))
        self.bind("<Button-1>", self._on_press)
        self.bind("<B1-Motion>", self._on_motion)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<Button-3>", self._on_right_press)
        self.bind("<Configure>", lambda e: self._draw_grid())
        self.bind("<MouseWheel>", lambda e: self.yview_scroll(-1 * (e.delta // 120), "units"))
        self.bind("<Shift-MouseWheel>",
                  lambda e: self.xview_scroll(-1 * (e.delta // 120), "units"))
        self._draw_grid()

    # -- public API ---------------------------------------------------------

    def drop_at_screen(self, type_key: str, screen_x: int, screen_y: int) -> None:
        x = self.canvasx(screen_x - self.winfo_rootx()) - NODE_W / 2
        y = self.canvasy(screen_y - self.winfo_rooty()) - HEAD_H / 2
        self.add_node(type_key, max(0, x), max(0, y))

    def add_node(self, type_key: str, x: float = 60, y: float = 60) -> Optional[FlowNode]:
        spec = NODE_TYPES[type_key]
        if spec.unique and any(n.type == type_key for n in self.flow.nodes.values()):
            self.select(None)
            return None
        node = self.flow.add(type_key, x, y)
        self.redraw()
        self.select(node.id)
        self._on_change()
        return node

    def contains_screen_point(self, screen_x: int, screen_y: int) -> bool:
        x0, y0 = self.winfo_rootx(), self.winfo_rooty()
        return (x0 <= screen_x <= x0 + self.winfo_width()
                and y0 <= screen_y <= y0 + self.winfo_height())

    def delete_selected(self) -> None:
        if not self.selected:
            return
        self.flow.remove(self.selected)
        self.selected = None
        self.redraw()
        self._on_select(None)
        self._on_change()

    def clear(self) -> None:
        self.flow = Flow()
        self.selected = None
        self.active = None
        self.redraw()
        self._on_select(None)
        self._on_change()

    def select(self, node_id: Optional[str]) -> None:
        self.selected = node_id
        self.redraw()
        self._on_select(self.flow.nodes.get(node_id) if node_id else None)

    def set_active(self, node_id: Optional[str]) -> None:
        self.active = node_id
        self.redraw()

    def refresh(self) -> None:
        self.redraw()
        self._on_change()

    # -- drawing ------------------------------------------------------------

    def _draw_grid(self) -> None:
        self.delete("grid")
        x1, y1, x2, y2 = (int(v) for v in self.cget("scrollregion").split())
        for x in range(x1, x2, 25):
            self.create_line(x, y1, x, y2, fill=GRID, tags="grid")
        for y in range(y1, y2, 25):
            self.create_line(x1, y, x2, y, fill=GRID, tags="grid")
        self.tag_lower("grid")

    def redraw(self) -> None:
        self.delete("node")
        self.delete("link")
        for node in self.flow.nodes.values():
            self._draw_node(node)
        self._draw_links()
        self.tag_lower("link")
        self.tag_lower("grid")

    def _rounded(self, x, y, w, h, r, **kw):
        pts = [x + r, y, x + w - r, y, x + w, y, x + w, y + r, x + w, y + h - r,
               x + w, y + h, x + w - r, y + h, x + r, y + h, x, y + h,
               x, y + h - r, x, y + r, x, y]
        return self.create_polygon(pts, smooth=True, **kw)

    def _draw_node(self, node: FlowNode) -> None:
        spec = node.spec
        tag = f"node:{node.id}"
        h = node.height

        outline = BODY_LINE
        width = 1
        if node.id == self.active:
            outline, width = ACTIVE_LINE, 3
        elif node.id == self.selected:
            outline, width = SEL_LINE, 2

        self._rounded(node.x, node.y, NODE_W, h, 8, fill=BODY_FILL,
                      outline=outline, width=width, tags=("node", tag, "body"))
        self._rounded(node.x, node.y, NODE_W, HEAD_H + 10, 8, fill=spec.color,
                      outline=spec.color, tags=("node", tag, "body"))
        self.create_rectangle(node.x, node.y + HEAD_H - 2, node.x + NODE_W,
                              node.y + HEAD_H + 8, fill=spec.color, outline=spec.color,
                              tags=("node", tag, "body"))
        self.create_text(node.x + 12, node.y + HEAD_H / 2, text=spec.label, anchor="w",
                         fill="#ffffff", font=("Segoe UI", 9, "bold"),
                         tags=("node", tag, "body"))

        self.create_text(node.x + 12, node.y + HEAD_H + BODY_H / 2 + 2,
                         text=spec.describe(node.params), anchor="w", fill="#334155",
                         width=NODE_W - 24, font=("Segoe UI", 9),
                         tags=("node", tag, "body"))

        # Inlet marker (targets are picked by dropping on the node itself).
        ix, iy = node.inlet_xy()
        self.create_oval(ix - 5, iy - 5, ix + 5, iy + 5, fill=PORT_FILL,
                         outline="#ffffff", width=2, tags=("node", tag, "body"))

        for port in spec.outs:
            px, py = node.port_xy(port)
            self.create_text(px - 12, py, text=port, anchor="e", fill="#64748b",
                             font=("Segoe UI", 8), tags=("node", tag, "body"))
            self.create_oval(px - 6, py - 6, px + 6, py + 6, fill=spec.color,
                             outline="#ffffff", width=2, tags=("node", tag, "port"))

        # Deliberately no tag_bind here.  Item bindings do not survive the
        # redraw that a drag triggers, and they fight with the widget level
        # handlers over clicks on a port that overhangs the node edge.  All
        # hit testing is done against the model in _port_at / _node_at.

    def _draw_links(self) -> None:
        for node in self.flow.nodes.values():
            for port, target_id in node.links.items():
                target = self.flow.nodes.get(target_id)
                if target is None or port not in node.spec.outs:
                    continue
                x1, y1 = node.port_xy(port)
                x2, y2 = target.inlet_xy()
                # Cap the control point offset, otherwise a link that loops
                # back to an earlier node swings far off across the canvas.
                bend = 36.0 + min(90.0, abs(x2 - x1) * 0.25)
                self.create_line(x1, y1, x1 + bend, y1, x2 - bend, y2, x2, y2,
                                 smooth=True, width=2, fill=LINK_COLOR,
                                 arrow="last", arrowshape=(11, 13, 4), tags="link")

    # -- interaction --------------------------------------------------------

    def _rubber_to(self, x: float, y: float) -> None:
        if not self._link_from:
            return
        node = self.flow.nodes.get(self._link_from[0])
        if node is None:
            return
        x0, y0 = node.port_xy(self._link_from[1])
        self.delete("rubber")
        self.create_line(x0, y0, x, y, fill=SEL_LINE, width=2, dash=(5, 3),
                         tags="rubber")

    def _finish_link(self, target_id: Optional[str]) -> None:
        source = self._link_from
        self._link_from = None
        self.delete("rubber")
        if not source:
            return
        node_id, port = source
        node = self.flow.nodes.get(node_id)
        if node is None:
            return
        if target_id and target_id != node_id:
            node.links[port] = target_id
        else:
            node.links.pop(port, None)
        self.redraw()
        self._on_change()

    def _clear_link(self, node_id: str, port: str) -> None:
        node = self.flow.nodes.get(node_id)
        if node and node.links.pop(port, None) is not None:
            self.redraw()
            self._on_change()

    # -- hit testing (model space, so a redraw never invalidates it) ---------

    def _node_at(self, x: float, y: float) -> Optional[str]:
        # Reversed: later nodes are drawn on top, so they win a click.
        for node in reversed(list(self.flow.nodes.values())):
            if (node.x <= x <= node.x + NODE_W
                    and node.y <= y <= node.y + node.height):
                return node.id
        return None

    def _port_at(self, x: float, y: float) -> Optional[tuple[str, str]]:
        """Output port under the cursor, with a forgiving grab radius."""
        for node in reversed(list(self.flow.nodes.values())):
            for port in node.spec.outs:
                px, py = node.port_xy(port)
                if (x - px) ** 2 + (y - py) ** 2 <= PORT_GRAB ** 2:
                    return (node.id, port)
        return None

    # -- mouse ---------------------------------------------------------------

    def _on_press(self, event) -> None:
        self.focus_set()
        x, y = self.canvasx(event.x), self.canvasy(event.y)

        # Ports first: they overhang the node's right edge on purpose.
        hit = self._port_at(x, y)
        if hit:
            self._link_from = hit
            self._drag_node = None
            self._rubber_to(x, y)
            return

        node_id = self._node_at(x, y)
        if node_id:
            node = self.flow.nodes[node_id]
            self._drag_node = node_id
            self._drag_off = (x - node.x, y - node.y)
            if self.selected != node_id:
                self.select(node_id)  # safe: nothing depends on the old items
            return

        self._panning = True
        self.scan_mark(event.x, event.y)
        if self.selected:
            self.select(None)

    def _on_motion(self, event) -> None:
        x, y = self.canvasx(event.x), self.canvasy(event.y)
        if self._link_from:
            self._rubber_to(x, y)
            return
        if self._drag_node:
            node = self.flow.nodes.get(self._drag_node)
            if node is None:
                return
            node.x = max(0.0, x - self._drag_off[0])
            node.y = max(0.0, y - self._drag_off[1])
            self.redraw()
            return
        if self._panning:
            self.scan_dragto(event.x, event.y, gain=1)

    def _on_release(self, event) -> None:
        x, y = self.canvasx(event.x), self.canvasy(event.y)
        if self._link_from:
            self._finish_link(self._node_at(x, y))
        elif self._drag_node:
            self._drag_node = None
            self._on_change()
        self._panning = False

    def _on_right_press(self, event) -> None:
        hit = self._port_at(self.canvasx(event.x), self.canvasy(event.y))
        if hit:
            self._clear_link(*hit)
