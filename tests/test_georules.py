import collections
import math

import pytest

import geomodel as G
from ubblcheck import geom, georules, travel
from ubblcheck.ifccheck import Facts, storey_exits


def rules(fs):
    return collections.Counter(f.rule for f in fs)


def plan(md, cell=0.2):
    f = Facts(md.m)
    bys = georules._by_storey(f)
    st = f.storeys[0]
    pl = georules.Plan(f, st, bys[st.id()], storey_exits(f).get(st, ([], False)))
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
    class S:
        Name, LongName, ObjectType = name, None, None
    assert georules.use_group(S) == group


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
    georules.check(md.m, heat_dir=str(tmp_path))
    pngs = list(tmp_path.glob("travel-*.png"))
    assert len(pngs) == 1 and pngs[0].stat().st_size > 500


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
    f = Facts(md.m)
    up = f.storeys[1]
    pl = georules.Plan(f, up, georules._by_storey(f)[up.id()], storey_exits(f)[up])
    assert pl.via_stairs and pl.exit_elements[0][0] == stair
    r = travel.space_travel(pl.solve(0.2), pl.spaces[0][1])
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


def _risk_floor(door="60", wall_ratings=("120",) * 4, own_walls=True, partition=False):
    """A shop floor with a 4 x 4 m boiler room in the middle, walled and doored as given."""
    import ifcopenshell.api.pset as pset
    import ifcopenshell.util.element as ue

    def rate(e, ps, r):
        pset.edit_pset(md.m, pset=md.m.by_id(ue.get_psets(e)[ps]["id"]), properties={"FireRating": r or ""})

    md, st = one_storey(20, 10, ((0, 2.0), (0, 18.0)), group="V")
    named = {}
    if own_walls:
        corners = [(8, 3), (12, 3), (12, 7), (8, 7)]
        walls = [md.wall(st, corners[i], corners[(i + 1) % 4], 0.0, name=f"BR-W{i}") for i in range(4)]
        for w, r in zip(walls, wall_ratings):
            rate(w, "Pset_WallCommon", r)
        named["door"] = md.door(st, walls[0], 2.0, name="BR-D")
        rate(named["door"], "Pset_DoorCommon", door)
        named["walls"] = walls
    if partition:       # an unrated corridor partition butting against the room's east wall
        named["partition"] = md.wall(st, (12.1, 5.0), (16.0, 5.0), 0.0, name="P1")
    named["room"] = md.space(st, G.rect(8.1, 3.1, 11.9, 6.9), 0.0, "B01", long_name="Boiler Room")
    return [x for x in georules.check(md.m) if x.rule == "GEO-RISK-ROOM"], named


def test_risk_room_walled_off_passes():
    fs, _ = _risk_floor()
    assert fs == []
    fs, _ = _risk_floor(door="FD60")                    # the usual door mark is read as 60 min
    assert fs == []
    fs, _ = _risk_floor(partition=True)                 # a partition butting on is not an enclosing wall
    assert fs == []


def test_risk_room_with_weak_door_or_wall():
    fs, n = _risk_floor(door=None)
    assert [x.where for x in fs] == [[n["room"].GlobalId, n["door"].GlobalId]]
    assert "unrated or unreadable" in fs[0].message and fs[0].clause == "UBBL by-law 139"
    fs, n = _risk_floor(wall_ratings=("120", "30", "120", "120"))
    assert [x.where for x in fs] == [[n["room"].GlobalId, n["walls"][1].GlobalId]] and "30 min (needs 60)" in fs[0].message


def test_risk_room_with_no_walls_is_reported():
    fs, n = _risk_floor(own_walls=False)
    assert len(fs) == 1 and "no wall was found" in fs[0].message and fs[0].where == [n["room"].GlobalId]
