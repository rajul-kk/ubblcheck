"""Walking distance to the nearest storey exit, by-law 165(1).

"measured on the floor ... along the centre line of the natural path of
travel, starting 0.300 metre from the most remote point of occupancy, curving
around any corners or obstructions with 0.300 metre clearance therefrom and
ending at the storey exit."

Method: rasterise the storey's walking surface (its spaces) on a square grid.
Cells inside walls, or closer than 0.3 m to them, are blocked; door openings
stay passable across their full width.  A multi-source Dijkstra from the exit
door cells, with 16 move directions (knight moves included), gives every cell
its walking distance.  16 directions over-estimate a straight run by at most
~2.7 %, and the grid adds up to about one cell; findings inside that band are
reported as marginal rather than as failures.
"""
from __future__ import annotations

import heapq
import math
from dataclasses import dataclass

import numpy as np
import shapely
from shapely.ops import unary_union

CLEARANCE = 0.3
START_OFFSET = 0.3
MOVE_ERROR = 0.027        # worst-case over-estimate of 16-direction moves

_MOVES = []
for dx in (-2, -1, 0, 1, 2):
    for dy in (-2, -1, 0, 1, 2):
        if (dx, dy) == (0, 0) or (abs(dx) == 2 and abs(dy) != 1) or (abs(dy) == 2 and abs(dx) != 1):
            continue
        # cells a move crosses, which must be free too
        if abs(dx) == 2 or abs(dy) == 2:
            sx, sy = int(math.copysign(1, dx)) if dx else 0, int(math.copysign(1, dy)) if dy else 0
            via = [(sx, 0), (sx, sy)] if abs(dx) == 2 else [(0, sy), (sx, sy)]
        elif dx and dy:
            via = [(dx, 0), (0, dy)]
        else:
            via = []
        _MOVES.append((dx, dy, math.hypot(dx, dy), via))


@dataclass
class Field:
    """Distance field over one storey."""
    x0: float
    y0: float
    cell: float
    free: np.ndarray        # (ny, nx) bool
    dist: np.ndarray        # (ny, nx) metres, inf where unreachable
    exits: np.ndarray       # (ny, nx) bool

    def centres(self):
        ny, nx = self.free.shape
        xs = self.x0 + (np.arange(nx) + 0.5) * self.cell
        ys = self.y0 + (np.arange(ny) + 0.5) * self.cell
        return np.meshgrid(xs, ys)

    def mask(self, geom) -> np.ndarray:
        X, Y = self.centres()
        return shapely.contains_xy(geom, X, Y)

    def tolerance(self, d: float) -> float:
        return d * MOVE_ERROR + 1.5 * self.cell


def field(walk, obstacles, exits, doors, cell: float = 0.2) -> Field:
    """walk: polygon(s) people can stand on; obstacles: wall/column cut polygons;
    exits: outlines of storey exit doors; doors: outlines of all doors."""
    obstacles = unary_union([o for o in obstacles if o is not None and not o.is_empty]) if obstacles else None
    door_zone = unary_union([d.buffer(CLEARANCE + cell) for d in doors]) if doors else None
    exit_u = unary_union(exits) if exits else None
    domain = unary_union([walk] + list(doors)) if doors else walk
    minx, miny, maxx, maxy = domain.bounds
    minx -= cell
    miny -= cell
    nx = int(math.ceil((maxx - minx) / cell)) + 2
    ny = int(math.ceil((maxy - miny) / cell)) + 2
    f = Field(minx, miny, cell, np.zeros((ny, nx), bool), np.full((ny, nx), np.inf), np.zeros((ny, nx), bool))
    X, Y = f.centres()
    free = shapely.contains_xy(domain.buffer(cell * 0.5), X, Y)
    if obstacles is not None:
        hard = shapely.contains_xy(obstacles, X, Y)
        clear = shapely.contains_xy(obstacles.buffer(CLEARANCE), X, Y)
        if door_zone is not None:
            clear &= ~shapely.contains_xy(door_zone, X, Y)
        free &= ~(hard | clear)
    if doors:
        free |= shapely.contains_xy(unary_union(doors).buffer(cell * 0.5), X, Y)
    f.free = free
    if exit_u is not None:
        f.exits = shapely.contains_xy(exit_u.buffer(cell * 0.5), X, Y) & free
    _dijkstra(f)
    return f


def _dijkstra(f: Field) -> None:
    ny, nx = f.free.shape
    free = f.free
    dist = f.dist
    heap = []
    for y, x in zip(*np.nonzero(f.exits)):
        dist[y, x] = 0.0
        heap.append((0.0, int(y), int(x)))
    heapq.heapify(heap)
    c = f.cell
    moves = [(dx, dy, w * c, via) for dx, dy, w, via in _MOVES]
    while heap:
        d, y, x = heapq.heappop(heap)
        if d > dist[y, x]:
            continue
        for dx, dy, w, via in moves:
            xx, yy = x + dx, y + dy
            if not (0 <= xx < nx and 0 <= yy < ny) or not free[yy, xx]:
                continue
            if any(not free[y + vy, x + vx] for vx, vy in via):
                continue
            nd = d + w
            if nd < dist[yy, xx]:
                dist[yy, xx] = nd
                heapq.heappush(heap, (nd, yy, xx))


@dataclass
class SpaceTravel:
    max_m: float            # longest walk, starting 0.3 m in from the remote point
    unreachable: float      # share of the space's cells with no route to an exit
    direct_m: float         # longest straight-line distance to the nearest exit
    door_m: float | None = None     # walk from the room door, for small rooms
    in_room_m: float | None = None  # walk inside the room to its door


def space_travel(f: Field, poly, room_doors=None) -> SpaceTravel | None:
    inner = poly.buffer(-START_OFFSET)
    if inner.is_empty:
        inner = poly
    m = f.mask(inner) & f.free
    if not m.any():
        return None
    d = f.dist[m]
    reach = np.isfinite(d)
    mx = float(d[reach].max()) if reach.any() else math.inf
    X, Y = f.centres()
    ex = np.column_stack([X[f.exits], Y[f.exits]])
    if len(ex):
        pts = np.column_stack([X[m], Y[m]])
        direct = 0.0
        for i in range(0, len(pts), 2048):
            blk = pts[i:i + 2048]
            dd = np.sqrt(((blk[:, None, :] - ex[None, :, :]) ** 2).sum(-1)).min(1)
            direct = max(direct, float(dd.max()))
    else:
        direct = math.inf
    out = SpaceTravel(mx, float((~reach).mean()), direct)
    if room_doors:
        dm = f.mask(unary_union(room_doors).buffer(f.cell)) & f.free & np.isfinite(f.dist)
        if dm.any():
            out.door_m = float(f.dist[dm].min())
            out.in_room_m = max(0.0, mx - out.door_m)
    return out


def heatmap(f: Field, limit: float, path: str, scale: int = 3) -> str:
    """PNG of the distance field: green to amber up to the limit, red beyond,
    grey for walls and unreachable floor, blue for exits."""
    from PIL import Image
    ny, nx = f.free.shape
    img = np.full((ny, nx, 3), 235, np.uint8)
    d = f.dist
    ok = f.free & np.isfinite(d)
    t = np.clip(d / limit, 0, 1)
    img[ok, 0] = (60 + 195 * t[ok]).astype(np.uint8)
    img[ok, 1] = (170 - 40 * t[ok]).astype(np.uint8)
    img[ok, 2] = 60
    over = ok & (d > limit)
    img[over] = (200, 30, 30)
    img[f.free & ~np.isfinite(d)] = (150, 150, 150)
    img[~f.free] = (60, 60, 60)
    img[f.exits] = (30, 90, 200)
    im = Image.fromarray(img[::-1]).resize((nx * scale, ny * scale), Image.NEAREST)
    im.save(path)
    return path
