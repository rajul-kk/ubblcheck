import collections
import math
import os

import pytest

import geomodel as G
from ubblcheck import geom, georules, rooms, travel, ubbl
from ubblcheck.model import Facts


def rules(fs):
    return collections.Counter(f.rule for f in fs)


def plan(md, cell=0.2, storey=0):
    pl = georules.plans(Facts(md.m))[storey]
    pl.solve(cell)
    return pl


def one_storey(W, D, exits, **kw):
    md = G.Model(**kw)
    st = md.storey("L0", 0.0)
    G.box_floor(md, st, W, D, exits=exits)
    return md, st


@pytest.mark.parametrize("group, second, error", [
    ("V", 5.5, True),       # door edges 2.5 m apart
    ("V", 8.5, False),      # 6.5 m apart
    ("IV", 5.5, False),     # single-storey office: by-law 194 needs only one exit
])
def test_exits_five_metres_apart(group, second, error):
    md, _ = one_storey(40, 20, ((0, 2.0), (0, second)), group=group)
    fs = [x for x in georules.check(md.m) if x.rule == "GEO-EXIT-SPACING"]
    assert bool(fs) is error
    if error:
        assert abs(fs[0].detail["widest_m"] - 2.5) < 0.05 and fs[0].clause == "UBBL by-law 174(1)"


def test_open_floor_distance_matches_geometry():
    md, _ = one_storey(40, 20, ((0, 2.0),))
    pl = plan(md)
    r = travel.space_travel(pl.field, pl.spaces[0][1])
    # 0.3 m in from the far corner (39.6, 19.6) to the near edge of the 1 m exit (2.5, 0.3)
    exact = math.hypot(39.6 - 2.5, 19.6 - 0.3)
    assert abs(r.max_m - exact) <= pl.field.tolerance(exact)
    assert r.unreachable == 0


def test_walking_goes_round_walls():
    md, st = one_storey(40, 20, ((0, 2.0),))
    iw = md.wall(st, (20, 0), (20, 20), 0.0, name="IW")
    md.door(st, iw, 18.0, name="ID")
    pl = plan(md)
    r = travel.space_travel(pl.field, pl.spaces[0][1])
    # far point is the bottom right corner: up to the internal door, then down to the exit
    via = math.hypot(39.6 - 20.5, 17.5 - 0.4) + 1.0 + math.hypot(19.5 - 2.5, 17.5 - 0.3)
    assert r.max_m == pytest.approx(via, rel=0.04)
    assert r.max_m > r.direct_m + 5


def test_closed_room_has_no_route():
    md, st = one_storey(40, 20, ((0, 2.0),))
    md.wall(st, (20, 0), (20, 20), 0.0, name="IW")           # no door in it
    fs = georules.check(md.m)
    assert rules(fs)["GEO-NOROUTE"] == 1


def test_travel_limits_and_sprinklers():
    md, _ = one_storey(60, 20, ((0, 2.0),))                  # ~61 m to the only exit
    assert rules(georules.check(md.m))["GEO-TRAVEL"] == 1
    md, _ = one_storey(60, 20, ((0, 2.0),), sprinklered=True)  # 60 m limit, still over
    assert rules(georules.check(md.m))["GEO-TRAVEL"] == 1
    md, _ = one_storey(60, 20, ((0, 2.0), (0, 58.0)))        # second exit: ~34 m
    assert rules(georules.check(md.m))["GEO-TRAVEL"] == 0


def test_no_exit_door_on_storey():
    md, st = one_storey(20, 10, ())
    assert rules(georules.check(md.m))["GEO-NOEXIT"] == 1


def test_declared_distance_understated():
    md = G.Model()
    st = md.storey("L0", 0.0)
    G.box_floor(md, st, 40, 20, exits=((0, 2.0),), travel=25)
    fs = georules.check(md.m)
    assert rules(fs)["GEO-DECLARED"] == 1 and rules(fs)["GEO-TRAVEL"] == 0


def test_open_plan_direct_distance():
    md, _ = one_storey(40, 20, ((0, 2.0),), group="II", use="open_plan")
    # limit 30 m unsprinklered: travel ~42 m fails, direct ~42 m > 20 m fails too
    r = rules(georules.check(md.m))
    assert r["GEO-TRAVEL"] == 1 and r["GEO-DIRECT"] == 1


def test_small_room_measured_from_its_door():
    # 50 x 10 floor, exit at the west end; a 4.8 x 9.8 m cell office at the east end behind a
    # wall with a door.  47 m2 at 10 m2/person = 5 persons, so by-law 165(3) applies:
    # remote corner to exit ~48 m (over 45), but office door to exit ~43 m and 6.5 m in the room.
    md = G.Model()
    st = md.storey("L0", 0.0)
    c = [(0, 0), (50, 0), (50, 10), (0, 10)]
    walls = [md.wall(st, c[i], c[(i + 1) % 4], 0.0, name=f"W{i}") for i in range(4)]
    md.door(st, walls[0], 2.0, exit=True, name="X")
    iw = md.wall(st, (45, 0), (45, 10), 0.0, name="IW")
    md.door(st, iw, 5.0, name="OfficeDoor")
    md.slab(st, G.rect(-0.1, -0.1, 50.1, 10.1), 0.0)
    md.space(st, G.rect(0.1, 0.1, 44.9, 9.9), 0.0, "Open office")
    md.space(st, G.rect(45.1, 0.1, 49.9, 9.9), 0.0, "Cell office")
    fs = georules.check(md.m)
    assert not [f for f in fs if f.rule in ("GEO-TRAVEL", "GEO-ROOM")], [f.message for f in fs]
    # make the room bigger than six persons: measured from its far corner, it fails
    md2 = G.Model()
    st = md2.storey("L0", 0.0)
    walls = [md2.wall(st, c[i], c[(i + 1) % 4], 0.0, name=f"W{i}") for i in range(4)]
    md2.door(st, walls[0], 2.0, exit=True, name="X")
    iw = md2.wall(st, (40, 0), (40, 10), 0.0, name="IW")
    md2.door(st, iw, 5.0, name="OfficeDoor")
    md2.slab(st, G.rect(-0.1, -0.1, 50.1, 10.1), 0.0)
    md2.space(st, G.rect(0.1, 0.1, 39.9, 9.9), 0.0, "Open office")
    md2.space(st, G.rect(40.1, 0.1, 49.9, 9.9), 0.0, "Big office")
    fs = georules.check(md2.m)
    assert [f for f in fs if f.rule == "GEO-TRAVEL" and "Big office" in f.message]


def test_fire_appliance_access_share():
    # 40 x 20 x 3 storeys of 3.5 m: spaces ~ 40*20*3*3 = 7,000+ m3 -> needs 1/6 of perimeter
    md = G.Model()
    for i in range(3):
        st = md.storey(f"L{i}", i * G.H)
        G.box_floor(md, st, 40, 20, z=i * G.H, exits=((0, 2.0), (0, 38.0)))
    fs = georules.check(md.m)
    assert rules(fs)["GEO-ACCESS"] == 1 and "no element is flagged" in next(
        f.message for f in fs if f.rule == "GEO-ACCESS")
    # a 12 m road along the 40 m south face: 40 / 120 = 33 % of the perimeter
    md.access(G.rect(-10, -16, 50, -4))
    md.hydrant(0, -3)
    r = rules(georules.check(md.m))
    assert r["GEO-ACCESS"] == 0 and r["GEO-HYDRANT"] == 0


def test_narrow_access_and_far_hydrant():
    md = G.Model()
    for i in range(3):
        st = md.storey(f"L{i}", i * G.H)
        G.box_floor(md, st, 40, 20, z=i * G.H, exits=((0, 2.0), (0, 38.0)))
    md.access(G.rect(-10, -10, 50, -4))         # only 6 m wide
    md.hydrant(150, 150)
    r = rules(georules.check(md.m))
    assert r["GEO-ACCESS-WIDTH"] == 1 and r["GEO-HYDRANT"] == 1


@pytest.mark.parametrize("name,group", [("Open office", "IV"), ("Bilik Tidur 2", "III"), ("Kedai 3", "V"),
                                        ("Surau", "VII"), ("Car park", "VIII"), ("Corridor", None),
                                        ("Male toilet", None), ("Ward 4B", "II"), ("Hallway", None),
                                        ("Entry hall", None), ("Foyer", None), ("Multi-purpose hall", "VII")])
def test_use_words(name, group):
    assert rooms.use_group(name) == group


def test_declared_group_contradicted_and_mixed_compartment():
    md = G.Model(group="IV")
    st = md.storey("L0", 0.0)
    G.box_floor(md, st, 40, 20, exits=((0, 2.0), (0, 38.0)), name="Retail shop")
    md.space(st, G.rect(1, 1, 15, 15), 0.0, "Office", compartment="C1")
    r = rules(georules.check(md.m))
    assert r["GEO-GROUP"] == 1 and r["GEO-MIXED"] == 1


def test_basement_floor_must_be_compartment():
    md = G.Model()
    b = md.storey("B1", -3.5)
    G.box_floor(md, b, 20, 10, z=-3.5, exits=((0, 2.0), (0, 18.0)), name="Car park")
    g = md.storey("L0", 0.0)
    G.box_floor(md, g, 20, 10, exits=((0, 2.0), (0, 18.0)))
    sev = lambda: [x.severity for x in georules.check(md.m) if x.rule == "GEO-BASEMENT"]
    assert sev() == ["error"]                   # declared not a compartment floor
    import ifcopenshell.api.pset as pset
    import ifcopenshell.util.element as ue
    sl = next(s for s in md.m.by_type("IfcSlab") if s.Name == "L0-SL")
    ps = md.m.by_id(ue.get_psets(sl)["Pset_SlabCommon"]["id"])
    pset.edit_pset(md.m, pset=ps, properties={"Compartmentation": None})
    assert sev() == ["warning"]                 # not declared: ask, don't accuse
    pset.edit_pset(md.m, pset=ps, properties={"Compartmentation": True})
    assert sev() == []


def test_heatmap(tmp_path):
    md, _ = one_storey(30, 15, ((0, 2.0),))
    maps = {}
    georules.check(md.m, heat_dir=str(tmp_path), heatmaps=maps)
    assert list(maps) == ["L0"] and os.path.getsize(maps["L0"]) > 500
    # storeys whose names are the same, or clean up to the same file name, keep a heatmap each
    md = G.Model()
    for i, name in enumerate(("L1/A", "L1 A", "L1 A")):
        G.box_floor(md, md.storey(name, i * G.H), 20, 10, z=i * G.H, exits=((0, 2.0),))
    maps = {}
    georules.check(md.m, heat_dir=str(tmp_path / "dup"), heatmaps=maps)
    assert len(maps) == 3 and len(set(maps.values())) == 3, maps


def _stair(md, lower, pts, z):
    """An escape stair the Revit way: an IfcStair with no body of its own, aggregating a flight
    (modelled here as a slab-shaped part)."""
    import ifcopenshell.api.aggregate as agg
    import ifcopenshell.api.root as root
    st = root.create_entity(md.m, ifc_class="IfcStair", name="ST1")
    G._ps(md.m, st, "Pset_StairCommon", {"FireExit": True})
    flight = md.slab(lower, pts, z, name="ST1-flight")
    agg.assign_object(md.m, products=[flight], relating_object=st)
    md._contain(st, lower)
    return st


def test_upper_storey_travels_to_its_stair():
    md = G.Model(group="V")
    l0, l1 = md.storey("L0", 0.0), md.storey("L1", G.H)
    G.box_floor(md, l0, 20, 10, exits=((0, 2.0), (0, 18.0)))
    G.box_floor(md, l1, 20, 10, z=G.H, exits=())
    stair = _stair(md, l0, G.rect(17, 7, 19, 9.5), 0.0)
    assert geom.outline(stair).area > 4.5
    fs = georules.check(md.m)
    assert not [x for x in fs if x.rule in ("GEO-NOEXIT", "GEO-NOROUTE")], [x.message for x in fs]
    pl = plan(md, storey=1)
    assert pl.via_stairs and pl.exit_elements[0][0] == stair
    r = travel.space_travel(pl.field, pl.spaces[0][1])
    # from the far corner (0.3, 0.3) to the stair's nearest corner (17, 7)
    assert abs(r.max_m - math.hypot(17 - 0.3, 7 - 0.3)) <= pl.field.tolerance(18.0)


def test_exit_without_plan_geometry_is_a_warning():
    import ifcopenshell.api.root as root
    md = G.Model(group="V")
    l0, l1 = md.storey("L0", 0.0), md.storey("L1", G.H)
    G.box_floor(md, l0, 20, 10, exits=((0, 2.0), (0, 18.0)))
    G.box_floor(md, l1, 20, 10, z=G.H, exits=())
    stair = root.create_entity(md.m, ifc_class="IfcStair", name="ST-empty")     # no body, no parts
    G._ps(md.m, stair, "Pset_StairCommon", {"FireExit": True})
    md._contain(stair, l0)
    fs = georules.check(md.m)
    assert [x.rule for x in fs if x.rule.startswith(("GEO-NOEXIT", "GEO-EXIT-GEOM"))] == ["GEO-EXIT-GEOM"]
    assert next(x for x in fs if x.rule == "GEO-EXIT-GEOM").where == [stair.GlobalId]


def _risk_floor(door="60", wall_ratings=("120",) * 4, own_walls=True, partition=False, thick_partition=False,
                door_geometry=True, room="Boiler Room", window=False, rule="GEO-RISK-ROOM", external=(),
                group="V", width=4.0, door_hosted=True):
    """A shop floor with a `width` x 4 m room in the middle (a boiler room unless named otherwise),
    walled and doored as given."""
    import ifcopenshell.api.pset as pset
    import ifcopenshell.util.element as ue

    def rate(e, ps, r):
        pset.edit_pset(md.m, pset=md.m.by_id(ue.get_psets(e)[ps]["id"]), properties={"FireRating": r or ""})

    md, st = one_storey(20, 10, ((0, 2.0), (0, 18.0)), group=group)
    x1 = 8 + width
    named = {}
    if own_walls:
        corners = [(8, 3), (x1, 3), (x1, 7), (8, 7)]
        walls = [md.wall(st, corners[i], corners[(i + 1) % 4], 0.0, name=f"BR-W{i}") for i in range(4)]
        for w, r in zip(walls, wall_ratings):
            rate(w, "Pset_WallCommon", r)
        for i in external:
            pset.edit_pset(md.m, pset=md.m.by_id(ue.get_psets(walls[i])["Pset_WallCommon"]["id"]),
                           properties={"IsExternal": True})
        named["door"] = md.door(st, walls[0] if width > 1 else walls[1], 2.0, name="BR-D", hosted=door_hosted)
        rate(named["door"], "Pset_DoorCommon", door)
        if not door_geometry:           # a door the geometry kernel can't read: still in its wall
            named["door"].Representation = None
            named["door"].FillsVoids[0].RelatingOpeningElement.Representation = None
        named["walls"] = walls
        if window:
            named["window"] = md.window(st, walls[2], 2.0, name="BR-WIN")
    if partition:       # an unrated corridor partition butting against the room's east wall
        named["partition"] = md.wall(st, (12.1, 5.0), (16.0, 5.0), 0.0, name="P1")
    if thick_partition:  # a 0.6 m masonry wall butting on end-first
        named["partition"] = md.wall(st, (12.1, 5.0), (16.0, 5.0), 0.0, name="P2", thickness=0.6)
    named["room"] = md.space(st, G.rect(8.1, 3.1, x1 - 0.1, 6.9), 0.0, "B01", long_name=room)
    return [x for x in georules.check(md.m) if x.rule == rule], named


def test_risk_room_walled_off_passes():
    fs, _ = _risk_floor()
    assert fs == []
    fs, _ = _risk_floor(door="FD60")                    # the usual door mark is read as 60 min
    assert fs == []
    fs, _ = _risk_floor(partition=True)                 # a partition butting on is not an enclosing wall
    assert fs == []
    fs, _ = _risk_floor(thick_partition=True)           # nor is a thick one
    assert fs == []


def test_risk_room_with_weak_door_or_wall():
    fs, n = _risk_floor(door=None)
    assert [x.where for x in fs] == [[n["room"].GlobalId, n["door"].GlobalId]]
    assert "unrated or unreadable" in fs[0].message and fs[0].clause == "UBBL by-law 139"
    fs, n = _risk_floor(wall_ratings=("120", "30", "120", "120"))
    assert [x.where for x in fs] == [[n["room"].GlobalId, n["walls"][1].GlobalId]] and "30 min (needs 60)" in fs[0].message


def test_risk_room_door_without_geometry_is_still_checked():
    fs, n = _risk_floor(door=None, door_geometry=False)
    assert [x.where for x in fs] == [[n["room"].GlobalId, n["door"].GlobalId]]


def test_risk_room_with_no_walls_is_reported():
    fs, n = _risk_floor(own_walls=False)
    assert len(fs) == 1 and "no wall was found" in fs[0].message and fs[0].where == [n["room"].GlobalId]


def test_protected_shaft():
    fs, _ = _risk_floor(room="Pipe Shaft", rule="GEO-SHAFT")              # rated walls and a fire door
    assert fs == []
    fs, n = _risk_floor(room="Pipe Shaft", rule="GEO-SHAFT", window=True)  # a rated window is still not allowed
    assert [x.where for x in fs] == [[n["room"].GlobalId, n["window"].GlobalId]]
    assert "not allowed" in fs[0].message and fs[0].clause == "UBBL by-law 150"
    fs, _ = _risk_floor(room="Boiler Room", window=True)                 # a risk room may have a rated window
    assert fs == []
    fs, n = _risk_floor(room="Riser", rule="GEO-SHAFT", wall_ratings=("120", "30", "120", "120"))
    assert [x.where for x in fs] == [[n["room"].GlobalId, n["walls"][1].GlobalId]]


@pytest.mark.parametrize("name, shaft", [("Pipe Shaft", True), ("Lift Shaft 2", True), ("Lift", True), ("Lif 3", True),
                                         ("M&E Riser", True), ("Service Duct", True), ("Syaf Paip", True),
                                         ("Elev", True), ("ELEV EQ RM", False), ("Lift Lobby", False), ("Productions", False), ("Conduct room", False)])
def test_shaft_names(name, shaft):
    assert bool(rooms.SHAFT.search(name)) is shaft


def test_shaft_checked_whatever_the_purpose_group():
    fs, n = _risk_floor(room="Riser", rule="GEO-SHAFT", group=None, wall_ratings=("120", "30", "120", "120"))
    assert [x.where for x in fs] == [[n["room"].GlobalId, n["walls"][1].GlobalId]]


def test_shaft_drawn_as_a_void_is_checked():
    fs, n = _risk_floor(room="Shaft Void", rule="GEO-SHAFT", wall_ratings=("120", "30", "120", "120"))
    assert [x.where for x in fs] == [[n["room"].GlobalId, n["walls"][1].GlobalId]]


def test_unhosted_door_on_an_enclosing_wall_is_checked():
    fs, n = _risk_floor(room="Riser", rule="GEO-SHAFT", door=None, door_hosted=False)
    assert [x.where for x in fs] == [[n["room"].GlobalId, n["door"].GlobalId]]
    fs, _ = _risk_floor(room="Riser", rule="GEO-SHAFT", door_hosted=False)
    assert fs == []


def test_narrow_duct_end_walls_enclose_it():
    fs, n = _risk_floor(room="Service Duct", rule="GEO-SHAFT", width=0.6, wall_ratings=("30", "120", "120", "120"))
    assert [x.where for x in fs] == [[n["room"].GlobalId, n["walls"][0].GlobalId]]


def test_room_walled_only_by_external_walls_gives_no_finding():
    fs, _ = _risk_floor(room="Riser", rule="GEO-SHAFT", wall_ratings=(None,) * 4, external=(0, 1, 2, 3))
    assert fs == []


def test_external_walls_of_a_risk_room_are_not_held_to_the_period():
    fs, _ = _risk_floor(wall_ratings=("120", "30", "120", "120"), external=(1,))
    assert fs == []                 # the weak wall faces outside: it separates the room from the open air


@pytest.mark.parametrize("capacity, cls, exits", [(99, None, 0), (100, "C", 2), (299, "C", 2), (300, "B", 2),
                                                  (600, "B", 2), (601, "B", 3), (999, "B", 3), (1000, "A", 4)])
def test_assembly_class_and_exits(capacity, cls, exits):
    assert ubbl.assembly_class(capacity) == cls and ubbl.assembly_exits(capacity) == exits


@pytest.mark.parametrize("mm, units", [(500, 0), (552, 1), (851, 1), (852, 1.5), (1104, 2), (1404, 2.5)])
def test_assembly_exit_units(mm, units):
    assert ubbl.assembly_units(mm) == units


def _hall(W=20, D=10, exits=((0, 2.0),), long_name="Auditorium", seats=None, group="VII", sprinklered=False,
          widths=None):
    """One storey that is one room; the exit doors' widths (m) are set after building when given."""
    md = G.Model(group=group, sprinklered=sprinklered)
    st = md.storey("L0", 0.0)
    _, sp = G.box_floor(md, st, W, D, exits=exits, long_name=long_name)
    if seats is not None:
        G._ps(md.m, sp, "MY_FireSafety", {"FixedSeats": seats})
    doors = md.m.by_type("IfcDoor")
    for d, w in zip(doors, widths or ()):
        d.OverallWidth = w
    return md, sp, doors


def _assembly(md):
    return [x for x in georules.check(md.m) if x.rule.startswith("GEO-ASSEMBLY")]


def test_assembly_room_capacity_from_area_or_seats():
    md, sp, _ = _hall(long_name="Auditorium")
    persons, how = georules.assembly_capacity(sp, 195.0)
    assert persons == 300 and "0.65" in how                        # 195 / 0.65
    md, sp, _ = _hall(long_name="Conference Room", seats=120)
    assert georules.assembly_capacity(sp, 195.0)[0] == 120   # fixed seats count, not area
    for name in ("Office", "Entrance Hall", "Lift Lobby"):
        md, sp, _ = _hall(long_name=name)
        assert georules.assembly_capacity(sp, 195.0) is None


def test_assembly_room_with_too_few_or_too_narrow_exits():
    md, sp, doors = _hall()                     # ~190 m2 of auditorium: about 290 persons, class C
    fs = _assembly(md)
    assert sorted(x.rule for x in fs) == ["GEO-ASSEMBLY-EXITS", "GEO-ASSEMBLY-WIDTH"]
    assert all(x.severity == "error" for x in fs) and "class C" in fs[0].message
    md, sp, doors = _hall(exits=((0, 2.0), (2, 2.0)), widths=(1.7, 1.7))   # 3 units each: 600 persons
    assert _assembly(md) == []
    md, sp, doors = _hall(exits=((0, 2.0), (2, 2.0), (1, 5.0)), widths=(2.3, 2.3, 0.6))
    fs = _assembly(md)
    assert [x.where for x in fs] == [[doors[2].GlobalId]] and "700 mm" in fs[0].message


def test_assembly_door_without_width_is_a_warning():
    md, sp, doors = _hall(exits=((0, 2.0), (2, 2.0)), widths=(1.1, None))   # 1.5 units known: 150 persons
    fs = _assembly(md)
    assert [(x.rule, x.severity) for x in fs] == [("GEO-ASSEMBLY-WIDTH", "warning")]
    assert "no width given" in fs[0].message


def test_assembly_travel_limit():
    lim = ubbl.assembly_travel(ubbl.travel_limit("VII", True), sprinklered=True)    # the schedule's 61 m
    assert (lim.travel_m, lim.clause) == (60, "UBBL by-law 188")
    tight = ubbl.travel_limit("VI", False)                                            # 30 m: already within
    assert ubbl.assembly_travel(tight, sprinklered=False) is tight
    assert ubbl.assembly_travel(ubbl.TravelLimit(None, None), sprinklered=False).travel_m == 45   # no limit of its own


@pytest.mark.parametrize("name, persons", [
    ("Auditorium", 200), ("Dewan Serbaguna", 200), ("Surau", 200), ("Dance Floor", 200),     # 130 m2 / 0.65
    ("Dining Hall", 97), ("Dewan Makan", 97), ("Conference Hall", 97), ("Ruang Pameran", 97),  # / 1.35
    ("Exhibition Hall", 97), ("Gymnasium", 97), ("Waiting Hall", 44),                         # / 3
    ("Guidance Room", None), ("Attendance Office", None), ("Rebar Store", None), ("Entrance Hall", None),
    ("Lift Lobby", None), ("Hallway", None), ("Office", None)])
def test_assembly_load_from_room_name(name, persons):
    import ifcopenshell
    m = ifcopenshell.file(schema="IFC4")            # kept alive: the space dies with its file
    sp = m.createIfcSpace(GlobalId=ifcopenshell.guid.new(), Name="R1", LongName=name)
    cap = georules.assembly_capacity(sp, 130.0)
    assert (cap and cap[0]) == persons


def test_shaft_on_a_long_wall_ignores_other_rooms_openings():
    """A riser at the end of a long corridor wall: the office door and window further along the
    same wall are not the riser's."""
    md, st = one_storey(30, 10, ((0, 2.0), (0, 28.0)), group="IV")
    long_wall = md.wall(st, (0.0, 5.0), (30.0, 5.0), 0.0, name="CW")
    others = {md.door(st, long_wall, 15.0, name="OFFICE-D").GlobalId,
              md.window(st, long_wall, 20.0, name="OFFICE-WIN").GlobalId}
    near = md.window(st, long_wall, 1.0, name="RISER-WIN").GlobalId
    md.wall(st, (2.0, 5.0), (2.0, 10.0), 0.0, name="RW1")
    md.wall(st, (0.0, 7.0), (2.0, 7.0), 0.0, name="RW2")
    md.space(st, G.rect(0.1, 5.1, 1.9, 6.9), 0.0, "R01", long_name="Riser")
    fs = [x for x in georules.check(md.m) if x.rule == "GEO-SHAFT"]
    assert len(fs) == 1 and near in fs[0].where and not others & set(fs[0].where)


@pytest.mark.parametrize("label, cls", [("Class 0", 0), ("Class O", 0), ("class 1", 1), ("2", 2), ("Class 4", 4),
                                        ("Class 5", None), ("non-combustible", None), (None, None)])
def test_flame_class_labels(label, cls):
    from ubblcheck.ratings import flame_class
    assert flame_class(label) == cls


def _flame_floor(room="Corridor", walls="Class 3", ceiling=None, group="IV", sprinklered=False, z=0.0,
                 lining=None, external_exit=False):
    """A floor with a 4 x 4 m room walled in walls of the given class (lined, on its first wall, with a
    covering of class `lining`), and a ceiling over it."""
    import ifcopenshell
    import ifcopenshell.api.pset as pset
    import ifcopenshell.util.element as ue
    md = G.Model(group=group, sprinklered=sprinklered)
    st = md.storey("L0", z)
    G.box_floor(md, st, 20, 10, z=z, exits=((0, 2.0),))
    corners = [(8, 3), (12, 3), (12, 7), (8, 7)]
    ws = [md.wall(st, corners[i], corners[(i + 1) % 4], z, name=f"R-W{i}") for i in range(4)]
    for w in ws:
        pset.edit_pset(md.m, pset=md.m.by_id(ue.get_psets(w)["Pset_WallCommon"]["id"]),
                       properties={"SurfaceSpreadOfFlame": walls})
    if external_exit:
        for d in md.m.by_type("IfcDoor"):
            pset.edit_pset(md.m, pset=md.m.by_id(ue.get_psets(d)["Pset_DoorCommon"]["id"]),
                           properties={"IsExternal": True})
    if lining:
        cov = md.m.createIfcCovering(ifcopenshell.guid.new(), None, "Panelling", PredefinedType="CLADDING")
        G._ps(md.m, cov, "Pset_CoveringCommon", {"SurfaceSpreadOfFlame": lining})
        md.m.createIfcRelCoversBldgElements(ifcopenshell.guid.new(), None, None, None, ws[0], [cov])
        ws = [cov] + ws[1:]
    c = md.ceiling(st, G.rect(8, 3, 12, 7), z, flame=ceiling) if ceiling else None
    sp = md.space(st, G.rect(8.1, 3.1, 11.9, 6.9), z, "R01", long_name=room)
    fs = [x for x in georules.check(md.m) if x.rule == "GEO-FLAME"]
    return fs, sp, ws, c


def test_flame_spread_by_place():
    assert _flame_floor(room="Office", walls="Class 3")[0] == []          # other spaces: Class 3
    fs, sp, ws, _ = _flame_floor(room="Corridor", walls="Class 3")        # access to exits: Class 1
    assert [x.where for x in fs] == [[sp.GlobalId] + [w.GlobalId for w in ws]]
    assert "access to exits" in fs[0].message and "needs 1 or better" in fs[0].message
    assert _flame_floor(room="Corridor", walls="Class 1")[0] == []
    fs = _flame_floor(room="Stair 1", walls="Class 2")[0]                  # office exits: Class 1
    assert len(fs) == 1 and "an exit" in fs[0].message


def test_flame_spread_ceiling():
    fs, sp, _, c = _flame_floor(room="Lobby", walls="Class 0", ceiling="Class 2")
    assert [x.where for x in fs] == [[sp.GlobalId, c.GlobalId]]
    assert _flame_floor(room="Lobby", walls="Class 0", ceiling="Class 1")[0] == []


def test_flame_spread_sprinklers_relax_one_class():
    assert _flame_floor(room="Corridor", walls="Class 2", sprinklered=True)[0] == []
    fs = _flame_floor(room="Corridor", walls="Class 3", sprinklered=True)[0]
    assert len(fs) == 1 and "needs 2 or better" in fs[0].message


def test_flame_spread_occupied_basement():
    fs = _flame_floor(room="Office", walls="Class 3", z=-3.5)[0]
    assert len(fs) == 1 and "underground" in fs[0].message and "needs 1 or better" in fs[0].message
    assert _flame_floor(room="Store Room", walls="Class 3", z=-3.5)[0] == []   # storage is exempt
    assert _flame_floor(room="Car Park", walls="Class 3", z=-3.5)[0] == []
    fs = _flame_floor(room="Corridor", walls="Class 3", z=-3.5, group="VI")[0]  # factory: 3, but 1 underground
    assert len(fs) == 1 and "underground" in fs[0].message
    assert _flame_floor(room="Office", walls="Class 3", z=-3.5, external_exit=True)[0] == []   # direct exit out


def test_flame_spread_reads_a_wall_lining():
    fs, sp, ws, _ = _flame_floor(room="Corridor", walls="Class 0", lining="Class 3")
    assert [x.where for x in fs] == [[sp.GlobalId, ws[0].GlobalId]] and "Panelling" in fs[0].message
    fs, sp, ws, _ = _flame_floor(room="Corridor", walls="Class 3", lining="Class 0")   # the lined wall passes
    assert [x.where for x in fs] == [[sp.GlobalId] + [w.GlobalId for w in ws[1:]]]


def test_flame_spread_group_with_no_schedule_row_is_said():
    md, st = one_storey(20, 10, ((0, 2.0),), group="VII")
    w = md.m.by_type("IfcWall")[0]
    import ifcopenshell.api.pset as pset
    import ifcopenshell.util.element as ue
    pset.edit_pset(md.m, pset=md.m.by_id(ue.get_psets(w)["Pset_WallCommon"]["id"]),
                   properties={"SurfaceSpreadOfFlame": "Class 1"})
    fs = [x for x in georules.check(md.m) if x.rule == "GEO-FLAME"]
    assert [x.severity for x in fs] == ["info"] and "no row" in fs[0].message


def test_flame_spread_without_data_is_one_note():
    md, st = one_storey(20, 10, ((0, 2.0),), group="IV")
    fs = [x for x in georules.check(md.m) if x.rule.startswith("GEO-FLAME")]
    assert [(x.rule, x.severity) for x in fs] == [("GEO-FLAME-DATA", "info")]
