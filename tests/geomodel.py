"""Synthetic IFC4 models with geometry: rectangular floor plates, walls with
door openings cut, extruded spaces, an access road and hydrants."""
import math

import ifcopenshell
import ifcopenshell.api.aggregate as agg
import ifcopenshell.api.context as ctxapi
import ifcopenshell.api.feature as feat
import ifcopenshell.api.geometry as geo
import ifcopenshell.api.pset as pset
import ifcopenshell.api.root as root
import ifcopenshell.api.spatial as spatial
import ifcopenshell.api.unit as unit
import numpy as np

T = 0.2       # wall thickness
H = 3.5       # storey height


def _ps(m, el, name, props):
    p = pset.add_pset(m, product=el, name=name)
    pset.edit_pset(m, pset=p, properties=props)


def _mat(x, y, z, ang=0.0):
    c, s = math.cos(ang), math.sin(ang)
    M = np.eye(4)
    M[:2, :2] = [[c, -s], [s, c]]
    M[:3, 3] = [x, y, z]
    return M


class Model:
    def __init__(self, group="IV", sprinklered=False, use=None):
        m = self.m = ifcopenshell.file(schema="IFC4")
        proj = root.create_entity(m, ifc_class="IfcProject", name="Geo")
        unit.assign_unit(m, length={"is_metric": True, "raw": "METRES"})
        model = ctxapi.add_context(m, context_type="Model")
        self.body = ctxapi.add_context(m, context_type="Model", context_identifier="Body",
                                       target_view="MODEL_VIEW", parent=model)
        self.site = root.create_entity(m, ifc_class="IfcSite", name="Site")
        self.bld = root.create_entity(m, ifc_class="IfcBuilding", name="B")
        agg.assign_object(m, products=[self.site], relating_object=proj)
        agg.assign_object(m, products=[self.bld], relating_object=self.site)
        geo.edit_object_placement(m, product=self.site, matrix=np.eye(4))
        geo.edit_object_placement(m, product=self.bld, matrix=np.eye(4))
        props = {"PurposeGroup": group}
        if use:
            props["SubUse"] = use
        _ps(m, self.bld, "MY_FireSafety", props)
        _ps(m, self.bld, "Pset_BuildingCommon", {"SprinklerProtectionAutomatic": sprinklered})
        self.storeys = {}
        self.ends = {}

    def storey(self, name, z):
        st = root.create_entity(self.m, ifc_class="IfcBuildingStorey", name=name)
        st.Elevation = z
        geo.edit_object_placement(self.m, product=st, matrix=_mat(0, 0, z))
        agg.assign_object(self.m, products=[st], relating_object=self.bld)
        self.storeys[name] = st
        return st

    def _contain(self, el, st):
        spatial.assign_container(self.m, products=[el], relating_structure=st)

    def slab(self, st, pts, z, depth=0.2, compartment=False, name="SL"):
        sl = root.create_entity(self.m, ifc_class="IfcSlab", name=name, predefined_type="FLOOR")
        geo.assign_representation(self.m, product=sl,
                                  representation=geo.add_slab_representation(self.m, context=self.body,
                                                                             depth=depth, polyline=pts))
        geo.edit_object_placement(self.m, product=sl, matrix=_mat(0, 0, z - depth))
        _ps(self.m, sl, "Pset_SlabCommon", {"Compartmentation": compartment, "FireRating": "120"})
        self._contain(sl, st)
        return sl

    def wall(self, st, a, b, z, name="W", thickness=T):
        (x1, y1), (x2, y2) = a, b
        L = math.hypot(x2 - x1, y2 - y1)
        ang = math.atan2(y2 - y1, x2 - x1)
        w = root.create_entity(self.m, ifc_class="IfcWall", name=name)
        geo.assign_representation(self.m, product=w, representation=geo.add_wall_representation(
            self.m, context=self.body, length=L, height=H, thickness=thickness, offset=-thickness / 2))
        geo.edit_object_placement(self.m, product=w, matrix=_mat(x1, y1, z, ang))
        _ps(self.m, w, "Pset_WallCommon", {"Compartmentation": False})
        self._contain(w, st)
        self.ends[w.id()] = (a, b, ang)
        return w

    def door(self, st, wall, at, width=1.0, exit=False, z=0.0, name="D"):
        """Door centred `at` metres along `wall` from its start."""
        (x1, y1), _, ang = self.ends[wall.id()]
        s = at - width / 2
        x, y = x1 + s * math.cos(ang), y1 + s * math.sin(ang)
        op = root.create_entity(self.m, ifc_class="IfcOpeningElement", name=name + "-op")
        geo.assign_representation(self.m, product=op, representation=geo.add_wall_representation(
            self.m, context=self.body, length=width, height=2.1, thickness=T + 0.4, offset=-(T + 0.4) / 2))
        geo.edit_object_placement(self.m, product=op, matrix=_mat(x, y, z, ang))
        feat.add_feature(self.m, feature=op, element=wall)
        d = root.create_entity(self.m, ifc_class="IfcDoor", name=name)
        d.OverallWidth = width
        geo.edit_object_placement(self.m, product=d, matrix=_mat(x, y, z, ang))
        feat.add_filling(self.m, opening=op, element=d)
        _ps(self.m, d, "Pset_DoorCommon", {"FireExit": exit, "FireRating": "60"})
        self._contain(d, st)
        return d

    def space(self, st, pts, z, name, compartment="C1", height=3.0, travel=None, long_name=None):
        sp = root.create_entity(self.m, ifc_class="IfcSpace", name=name)
        if long_name:
            sp.LongName = long_name
        geo.assign_representation(self.m, product=sp, representation=geo.add_slab_representation(
            self.m, context=self.body, depth=height, polyline=pts))
        geo.edit_object_placement(self.m, product=sp, matrix=_mat(0, 0, z))
        agg.assign_object(self.m, products=[sp], relating_object=st)
        props = {"CompartmentId": compartment}
        if travel is not None:
            props["MaxTravelDistance"] = self.m.createIfcLengthMeasure(float(travel))
        _ps(self.m, sp, "MY_FireSafety", props)
        return sp

    def access(self, pts, name="Fire appliance access"):
        e = root.create_entity(self.m, ifc_class="IfcGeographicElement", name=name)
        geo.assign_representation(self.m, product=e, representation=geo.add_slab_representation(
            self.m, context=self.body, depth=0.1, polyline=pts))
        geo.edit_object_placement(self.m, product=e, matrix=_mat(0, 0, -0.1))
        _ps(self.m, e, "MY_FireSafety", {"FireApplianceAccess": True})
        spatial.assign_container(self.m, products=[e], relating_structure=self.site)
        return e

    def hydrant(self, x, y, tag="H1"):
        h = root.create_entity(self.m, ifc_class="IfcFireSuppressionTerminal", name=tag, predefined_type="FIREHYDRANT")
        h.Tag = tag
        geo.edit_object_placement(self.m, product=h, matrix=_mat(x, y, 0))
        spatial.assign_container(self.m, products=[h], relating_structure=self.site)
        return h


def rect(x0, y0, x1, y1):
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


def box_floor(md, st, W, D, z=0.0, exits=((0, 2.0),), name="Office", travel=None, long_name=None):
    """A W x D open floor with four perimeter walls; exits = [(wall index, at)],
    walls 0 south, 1 east, 2 north, 3 west, each running anticlockwise."""
    corners = [(0, 0), (W, 0), (W, D), (0, D)]
    walls = [md.wall(st, corners[i], corners[(i + 1) % 4], z, name=f"{st.Name}-W{i}") for i in range(4)]
    for k, (wi, at) in enumerate(exits):
        md.door(st, walls[wi], at, width=1.0, exit=True, z=z, name=f"{st.Name}-X{k}")
    md.slab(st, rect(-T / 2, -T / 2, W + T / 2, D + T / 2), z, name=f"{st.Name}-SL")
    sp = md.space(st, rect(T / 2, T / 2, W - T / 2, D - T / 2), z, name, travel=travel, long_name=long_name)
    return walls, sp
