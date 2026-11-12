"""Plan geometry from an IFC model: storey cut sections, footprints, volumes.

Everything is in metres, in world coordinates.  Meshes come from IfcOpenShell's
geometry kernel with openings subtracted, so a wall cut at 1 m above its storey
shows the gap where its door is.
"""
from __future__ import annotations

from dataclasses import dataclass

import ifcopenshell
import ifcopenshell.geom
import ifcopenshell.util.element as uel
import ifcopenshell.util.placement as uplace
import ifcopenshell.util.unit as uunit
import numpy as np
from shapely.geometry import LineString, MultiPolygon, Polygon
from shapely.ops import polygonize, unary_union

CUT_HEIGHT = 1.0          # m above storey level: below door heads, above sills
EPS = 1e-9


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


def mesh(product) -> Mesh | None:
    if not getattr(product, "Representation", None):
        return None
    try:
        sh = ifcopenshell.geom.create_shape(settings(), product)
    except Exception:
        return None
    g = sh.geometry
    v = np.asarray(g.verts, dtype=float).reshape(-1, 3)
    f = np.asarray(g.faces, dtype=int).reshape(-1, 3)
    if not len(f):
        return None
    return Mesh(v, f)


def footprint(m: Mesh | None):
    """Union of the mesh's triangles projected to plan."""
    if m is None:
        return None
    tris = []
    for a, b, c in m.faces:
        p = Polygon([m.verts[a, :2], m.verts[b, :2], m.verts[c, :2]])
        if p.area > 1e-8:
            tris.append(p)
    if not tris:
        return None
    return unary_union(tris).buffer(0)


def section(m: Mesh | None, z: float):
    """Plan polygon(s) where the plane at height z cuts a closed mesh."""
    if m is None:
        return None
    zs = m.verts[:, 2]
    if zs.min() > z or zs.max() < z:
        return None
    segs = []
    for tri in m.faces:
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


def location(product) -> np.ndarray | None:
    """Plan position: mesh centroid, else placement origin."""
    m = mesh(product)
    if m is not None:
        return m.verts[:, :2].mean(axis=0)
    if getattr(product, "ObjectPlacement", None) is None:
        return None
    mat = uplace.get_local_placement(product.ObjectPlacement)
    return mat[:2, 3] * uunit.calculate_unit_scale(product.file)


def outline(product):
    """Plan outline of a product: its own body, else the union of the parts it is made of, railings
    left out (Revit exports a stair as an empty IfcStair aggregating flights, landings, stringers and
    railings; other tools use IfcMember or IfcBuildingElementProxy for the flights)."""
    fp = footprint(mesh(product))
    if fp is not None and not fp.is_empty:
        return fp
    parts = [footprint(mesh(p)) for p in uel.get_decomposition(product)
             if not p.is_a("IfcRailing")]
    parts = [p for p in parts if p is not None and not p.is_empty]
    return unary_union(parts) if parts else None


def door_outline(door):
    """Plan outline of a door: its filled opening if it has one, else its own body."""
    for rel in getattr(door, "FillsVoids", []) or []:
        fp = footprint(mesh(rel.RelatingOpeningElement))
        if fp is not None and not fp.is_empty:
            return fp
    return footprint(mesh(door))


def polygons(g):
    if g is None or g.is_empty:
        return []
    if isinstance(g, Polygon):
        return [g]
    if isinstance(g, MultiPolygon):
        return list(g.geoms)
    return [p for p in getattr(g, "geoms", []) if isinstance(p, Polygon)]

