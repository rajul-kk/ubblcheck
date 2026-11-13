"""Plan geometry from an IFC model: storey cut sections, footprints, volumes.

Everything is in metres, in world coordinates.  Meshes come from IfcOpenShell's
geometry kernel with openings subtracted, so a wall cut at 1 m above its storey
shows the gap where its door is.
"""
from __future__ import annotations

from dataclasses import dataclass

import logging
import os
import threading

import ifcopenshell
import ifcopenshell.geom
import ifcopenshell.util.placement as uplace
import ifcopenshell.util.unit as uunit
import numpy as np
import shapely
from shapely.geometry import LineString, MultiPolygon, Polygon
from shapely.ops import polygonize, unary_union

CUT_HEIGHT = 1.0          # m above storey level: below door heads, above sills
EPS = 1e-9

log = logging.getLogger("ubblcheck.geom")


@dataclass
class Mesh:
    verts: np.ndarray     # (n, 3) metres, world
    faces: np.ndarray     # (m, 3) int


_SETTINGS = None


def settings():
    global _SETTINGS
    if _SETTINGS is None:
        s = ifcopenshell.geom.settings()
        s.set("use-world-coords", True)
        _SETTINGS = s
    return _SETTINGS


def _to_mesh(g) -> Mesh | None:
    v = np.asarray(g.verts, dtype=float).reshape(-1, 3)
    f = np.asarray(g.faces, dtype=int).reshape(-1, 3)
    return Mesh(v, f) if len(f) else None


def mesh(product) -> Mesh | None:
    if not getattr(product, "Representation", None):
        return None
    try:
        sh = ifcopenshell.geom.create_shape(settings(), product)
    except Exception as e:      # the kernel's own errors: the rules treat it as an element without a body
        log.debug("no geometry for #%d %s: %s", product.id(), product.is_a(), e)
        return None
    return _to_mesh(sh.geometry)


# one batch at a time, whatever runs at once (two checks from the web page): each uses every core
_BATCH = threading.Lock()
BATCH_SIZE = 200


def meshes(model, products) -> dict:
    """element id -> Mesh or None for those of `products` the kernel tessellates in a batch, on all
    cores at once.  An element missing from the result (no body, a representation the batch does
    not read, or the kernel stopped before it) is for mesh() to try on its own."""
    include = [e for e in products if getattr(e, "Representation", None)]
    out = {}
    with _BATCH:
        # in chunks: the kernel's working memory grows with the batch (+45 MB peak at 470 walls)
        for i in range(0, len(include), BATCH_SIZE):
            chunk = include[i:i + BATCH_SIZE]
            try:
                it = ifcopenshell.geom.iterator(settings(), model, max(1, (os.cpu_count() or 2) - 1), include=chunk)
                if it.initialize():
                    while True:
                        sh = it.get()
                        out[sh.id] = _to_mesh(sh.geometry)
                        if not it.next():
                            break
            except Exception as e:  # the kernel's own errors: what is left goes one by one through mesh()
                log.warning("batch tessellation of %d elements stopped (%s); the rest go one by one", len(chunk), e)
    return out


def footprint(m: Mesh | None):
    """Union of the mesh's triangles projected to plan (slivers under 1e-8 m2 left out)."""
    if m is None:
        return None
    t = m.verts[m.faces][:, :, :2]                      # (triangles, 3 corners, xy)
    e1, e2 = t[:, 1] - t[:, 0], t[:, 2] - t[:, 0]
    t = t[np.abs(e1[:, 0] * e2[:, 1] - e1[:, 1] * e2[:, 0]) / 2 > 1e-8]
    if not len(t):
        return None
    return shapely.union_all(shapely.polygons(np.concatenate([t, t[:, :1]], axis=1))).buffer(0)


def section(m: Mesh | None, z: float):
    """Plan polygon(s) where the plane at height z cuts a closed mesh."""
    if m is None:
        return None
    zs = m.verts[:, 2]
    if zs.min() > z or zs.max() < z:
        return None
    d = m.verts[m.faces][:, :, 2] - z
    segs = []
    # only a triangle touching the plane can give a segment
    for tri in m.faces[~((d >= EPS).all(1) | (d <= -EPS).all(1))]:
        p = m.verts[tri]
        d = p[:, 2] - z
        pts = []
        for i in range(3):
            j = (i + 1) % 3
            if abs(d[i]) < EPS:
                pts.append(p[i, :2])
            elif d[i] * d[j] < 0:
                t = d[i] / (d[i] - d[j])
                pts.append(p[i, :2] + t * (p[j, :2] - p[i, :2]))
        uniq = []
        for q in pts:
            if not any(np.allclose(q, u, atol=1e-7) for u in uniq):
                uniq.append(q)
        if len(uniq) == 2:
            segs.append(LineString([tuple(np.round(uniq[0], 6)), tuple(np.round(uniq[1], 6))]))
    if not segs:
        return None
    polys = list(polygonize(unary_union(segs)))
    if not polys:
        return None
    # polygonize returns holes as separate faces too; symmetric difference rebuilds them
    out = polys[0]
    for p in polys[1:]:
        out = out.symmetric_difference(p)
    return out.buffer(0)


def volume(m: Mesh | None) -> float | None:
    """Enclosed volume of a closed mesh (divergence theorem)."""
    if m is None:
        return None
    v = m.verts[m.faces]
    return float(abs(np.einsum("ij,ij->i", v[:, 0], np.cross(v[:, 1], v[:, 2])).sum()) / 6.0)


def location(product, m: Mesh | None) -> np.ndarray | None:
    """Plan position: the centroid of its mesh `m`, else its placement origin."""
    if m is not None:
        return m.verts[:, :2].mean(axis=0)
    if getattr(product, "ObjectPlacement", None) is None:
        return None
    mat = uplace.get_local_placement(product.ObjectPlacement)
    return mat[:2, 3] * uunit.calculate_unit_scale(product.file)


def polygons(g):
    if g is None or g.is_empty:
        return []
    if isinstance(g, Polygon):
        return [g]
    if isinstance(g, MultiPolygon):
        return list(g.geoms)
    return [p for p in getattr(g, "geoms", []) if isinstance(p, Polygon)]

