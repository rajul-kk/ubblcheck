import collections
import math

import pytest

import geomodel as G
from ubblcheck import georules, travel
from ubblcheck.ifccheck import Facts


def rules(fs):
    return collections.Counter(f.rule for f in fs)


def plan(md, cell=0.2):
    f = Facts(md.m)
    bys = georules._by_storey(f)
    st = f.storeys[0]
    pl = georules.Plan(f, st, bys[st.id()])
    pl.solve(cell)
    return pl


def one_storey(W, D, exits, **kw):
    md = G.Model(**kw)
    st = md.storey("L0", 0.0)
    G.box_floor(md, st, W, D, exits=exits)
    return md, st


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
                                        ("Male toilet", None), ("Ward 4B", "II")])
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
    assert rules(georules.check(md.m))["GEO-BASEMENT"] == 1


def test_heatmap(tmp_path):
    md, _ = one_storey(30, 15, ((0, 2.0),))
    georules.check(md.m, heat_dir=str(tmp_path))
    pngs = list(tmp_path.glob("travel-*.png"))
    assert len(pngs) == 1 and pngs[0].stat().st_size > 500
