import collections
import json
import zipfile

import pytest
from ifctester import ids

import ifcmodel
from ubblcheck import findings, ids_build, ifccheck, model, ratings, rooms


def test_property_carriers_include_those_set_through_a_type():
    import ifcopenshell.api.root as root
    import ifcopenshell.api.type as itype
    m = ifcmodel.office(storeys=1)
    own, typed, plain = (root.create_entity(m, ifc_class="IfcWall", name=n) for n in ("own", "typed", "plain"))
    wt = root.create_entity(m, ifc_class="IfcWallType", name="WT")
    itype.assign_type(m, related_objects=[typed], relating_type=wt)
    ifcmodel._ps(m, own, "Pset_WallCommon", {"SurfaceSpreadOfFlame": "Class 1"})
    ifcmodel._ps(m, wt, "Pset_WallCommon", {"SurfaceSpreadOfFlame": "Class 2"})
    names = {e.Name for e in model.Facts(m).carriers("SurfaceSpreadOfFlame")}
    assert {"own", "typed"} <= names and "plain" not in names


@pytest.fixture
def write(tmp_path):
    def _w(model, name="m.ifc"):
        p = tmp_path / name
        model.write(str(p))
        return str(p)
    return _w


def rules(fs):
    return collections.Counter(f.rule for f in fs)


def test_packaged_ids_validate_against_schema_and_match_builder(tmp_path):
    built = ids_build.build(str(tmp_path))
    for p, packaged in zip(built, ifccheck.default_ids()):
        ids.open(packaged, validate=True)
        a = [s.name for s in ids.open(p).specifications]
        b = [s.name for s in ids.open(packaged).specifications]
        assert a == b, "packaged IDS is stale: run python -m ubblcheck.ids_build"


def test_compliant_model_is_clean(write):
    assert ifccheck.check(write(ifcmodel.office())) == []


@pytest.mark.parametrize("label, minutes, in_ids", [
    ("60", 60, True), ("FRP 120 min", 120, True), ("REI 120", 120, True), ("EI 60", 60, True),
    ("FD60", 60, True), ("FD 30", 30, True), ("FRD60", 60, True), ("120 minutes", 120, True),
    ("1 HR", 60, True), ("2 HR FRP", 120, True), ("1/2 hour", 30, True), ("1.5 hrs", 90, True),
    ("2 HOURS", 120, False),            # read, but the IDS pack asks for one of its spellings
    ("45", 45, False),                  # not a standard period
    ("fire door", None, False), ("FD", None, False), ("60 FRP", None, False), ("one hour", None, False),
    ("", None, False),
])
def test_fire_ratings(label, minutes, in_ids):
    """How the checker reads a FireRating, and whether the IDS pack accepts it."""
    import re
    assert ratings.frp_minutes(label) == minutes
    assert (re.fullmatch(ids_build.FRP_PATTERN, label) is not None) is in_ids


def test_ids_flags_missing_data(write):
    fs = ifccheck.check(write(ifcmodel.office(group=None, tags=False)), rules=False)
    r = rules(fs)
    assert r["IDS-MY-P01"] == 1 and r["IDS-MY-A03"] == 1
    tagless = next(f for f in fs if f.rule == "IDS-MY-A03")
    assert len(tagless.where) == 2


def test_rule_failures(write):
    m = ifcmodel.office(storeys=10, space_area=1200, comp_split=False, travel=70, exit_width=0.8,
                        exits_per_storey=1, door_frp=None, slab_comp=False, group="V")
    r = rules(ifccheck.check(write(m)))
    assert r["IFC-COMP-AREA"] == 10 and r["IFC-COMP-VOL"] == 10
    assert r["IFC-TRAVEL"] == 20
    assert r["IFC-EXIT-COUNT"] == 10 and r["IFC-EXIT-WIDTH"] == 10
    assert r["IFC-FIREDOOR"] == 10
    assert r["IFC-COMP-FLOOR"] == 7          # floors more than 9 m up in a 40 m building
    assert r["IFC-RISER"] == 1 and r["IFC-FIRELIFT"] == 1


def test_sprinklers_relax_limits(write):
    m = ifcmodel.office(space_area=1200, comp_split=False, travel=55, group="V", sprinklered=True)
    r = rules(ifccheck.check(write(m)))
    assert r["IFC-COMP-AREA"] == 0 and r["IFC-TRAVEL"] == 0
    assert r["IFC-SPRINKLER"] == 1            # declared but no heads in the model


def test_weaker_door_than_wall_is_a_warning(write):
    fs = ifccheck.check(write(ifcmodel.office(door_frp="30", wall_frp="120")))
    assert {f.rule: f.severity for f in fs} == {"IFC-FIREDOOR-WALL": "warning"}


def test_element_fire_resistance_from_ninth_schedule(write):
    fs = ifccheck.check(write(ifcmodel.office(wall_frp="30", door_frp="30")))
    frp = [f for f in fs if f.rule == "IFC-FRP"]
    assert len(frp) == 2 and all(f.detail["required_min"] == 60 for f in frp)
    big = ifcmodel.office(storeys=10, space_area=1200, comp_split=False, group="V")
    assert rules(ifccheck.check(write(big)))["IFC-FRP-SIZE"] == 1


def test_high_building_with_riser_and_lift(write):
    m = ifcmodel.office(storeys=7, fire_lift=True, breeching=True)
    assert rules(ifccheck.check(write(m)))["IFC-RISER"] == 0


def test_bcf_and_json_output(write, tmp_path):
    fs = ifccheck.check(write(ifcmodel.office(door_frp=None)))
    bcf = tmp_path / "issues.bcf"
    assert findings.to_bcf(fs, str(bcf)) == len(fs) > 0
    with zipfile.ZipFile(bcf) as z:
        names = z.namelist()
        assert "bcf.version" in names
        assert sum(n.endswith("markup.bcf") for n in names) == len(fs)
        assert any(n.endswith("viewpoint.bcfv") for n in names)
        assert b"162" in z.read(next(n for n in names if n.endswith("markup.bcf")))
    findings.to_json(fs, str(tmp_path / "f.json"), {"file": "m.ifc"})
    doc = json.loads((tmp_path / "f.json").read_text(encoding="utf-8"))
    assert doc["meta"] == {"file": "m.ifc"} and doc["summary"] == findings.summary(fs)
    assert [f["rule"] for f in doc["findings"]] == [f.rule for f in fs]


def test_area_and_volume_quantities_use_their_own_units(write):
    # Revit: lengths in mm but areas in m2; the same oversized building must fail the same rules
    def big():
        return ifcmodel.office(group="V", comp_split=False, space_area=3000.0)
    metres = rules(ifccheck.check_rules(big()))
    mm = rules(ifccheck.check_rules(ifcmodel.to_millimetres(big())))
    assert metres["IFC-COMP-AREA"] == 2 and metres["IFC-COMP-VOL"] == 2
    assert mm == metres


def test_structural_elements_without_a_readable_rating_are_reported(write):
    m = ifcmodel.office()
    ifcmodel.add_column(m, rating=None, name="C-none")
    ifcmodel.add_column(m, rating="see schedule", name="C-text")
    ifcmodel.add_column(m, rating="REI 120", name="C-rei")
    fs = ifccheck.check_rules(m)
    data = [f for f in fs if f.rule == "IFC-FRP-DATA"]
    assert len(data) == 1 and data[0].severity == "warning" and len(data[0].where) == 2
    assert rules(fs)["IFC-FRP"] == 0


def test_roof_storey_does_not_make_a_single_storey_building_multi_storey():
    # group II single storey: 3000 m2 per compartment; multi-storey: 2000 m2
    one = ifcmodel.office(group="II", storeys=1, space_area=1200.0, comp_split=False, roof=True)
    assert rules(ifccheck.check_rules(one))["IFC-COMP-AREA"] == 0
    two = ifcmodel.office(group="II", storeys=2, space_area=1200.0, comp_split=False, roof=True)
    assert rules(ifccheck.check_rules(two))["IFC-COMP-AREA"] == 2


def test_spaces_without_area_quantities_are_measured():
    import geomodel as G
    md = G.Model(group="V")
    for i, z in enumerate((0.0, 3.5)):
        G.box_floor(md, md.storey(f"L{i}", z), 60, 40, z=z, exits=((0, 2.0), (2, 2.0)))
    r = rules(ifccheck.check_rules(md.m))       # 2400 m2 per storey, no Qto: limit 2000 m2
    assert r["IFC-COMP-AREA"] == 2


def test_ninth_schedule_line_per_compartment():
    # group IV, 8 m: a 300 m2 / 1050 m3 compartment is on the 15 m line (60 min);
    # a 6000 m2 one is on the "over 28 m" line (90 min)
    m = ifcmodel.office(space_area=300.0)
    for i in range(2):
        ifcmodel.set_space_area(m, f"L{i}-S1", 6000.0)
    small = ifcmodel.add_column(m, rating="60", name="C-small", compartment="C0-0")
    big = ifcmodel.add_column(m, rating="60", name="C-big", compartment="C0-1")
    frp = [f for f in ifccheck.check_rules(m) if f.rule == "IFC-FRP"]
    cols = [f for f in frp if "column" in f.message]
    assert len(cols) == 1 and cols[0].where == [big.GlobalId] and cols[0].detail["required_min"] == 90
    assert cols[0].detail["basis"] == "compartment C0-1"
    assert small.GlobalId not in {g for f in frp for g in f.where}
    # the storey's walls carry no compartment id: held to the longer of the two periods
    walls = [f for f in frp if "wall" in f.message]
    assert walls and all(f.detail["required_min"] == 90 for f in walls)


def test_elements_are_placed_in_compartments_by_geometry():
    import ifcopenshell.api.pset as pset
    import geomodel as G
    md = G.Model(group="IV")
    l0, l1 = md.storey("L0", 0.0), md.storey("L1", 3.5)
    md.space(l0, G.rect(0, 0, 10, 10), 0.0, "Small", compartment="CS")        # 100 m2: 30 min
    md.space(l0, G.rect(12, 0, 92, 80), 0.0, "Big", compartment="CB")         # 6400 m2: 90 min
    md.space(l1, G.rect(12, 0, 92, 80), 3.5, "Big L1", compartment="CB1")
    walls = {}
    for name, a, b in (("in-small", (0, 5), (10, 5)), ("in-big", (50, 5), (60, 5))):
        w = walls[name] = md.wall(l0, a, b, 0.0, name=name)
        ps = next(r.RelatingPropertyDefinition for r in w.IsDefinedBy
                  if r.RelatingPropertyDefinition.Name == "Pset_WallCommon")
        pset.edit_pset(md.m, pset=ps, properties={"LoadBearing": True, "FireRating": "60"})
    frp = [f for f in ifccheck.check_rules(md.m) if f.rule == "IFC-FRP"]
    assert len(frp) == 1 and frp[0].where == [walls["in-big"].GlobalId]
    assert frp[0].detail["required_min"] == 90 and frp[0].detail["basis"] == "compartment CB"


def test_tenth_schedule_systems():
    # IV, 40 m: line IV 4, sprinklers and hose reels, manual electrical alarm, generators
    tall = ifccheck.check_rules(ifcmodel.office(storeys=10, fire_lift=True, breeching=True, height=40.0))
    a = [f for f in tall if f.rule == "IFC-TENTH-A"]
    assert len(a) == 1 and a[0].severity == "error" and a[0].detail["line"] == "IV 4"
    assert not any(f.rule in ("IFC-TENTH-G", "IFC-TENTH-2") for f in tall)
    # IV, two storeys, 1600 m2: line IV 2 (hose reels, manual alarm); take the break glass units away
    m = ifcmodel.office()
    for e in m.by_type("IfcAlarm"):
        m.remove(e)
    r = rules(ifccheck.check_rules(m))
    assert r["IFC-TENTH-2"] == 1 and r["IFC-TENTH-G"] == 0 and r["IFC-TENTH-A"] == 0


def test_tenth_schedule_needs_the_room_count_for_hotels():
    m = ifcmodel.office(group="III")
    b = m.by_type("IfcBuilding")[0]
    ifcmodel._ps(m, b, "MY_FireSafety", {"SubUse": "hotel"})
    t = [f for f in ifccheck.check_rules(m) if f.rule == "IFC-TENTH"]
    assert len(t) == 1 and t[0].severity == "info" and t[0].detail["missing"] == ["rooms"]


def test_fire_objects_found_through_their_type_in_ifc2x3():
    # IFC2X3 has no IfcFireSuppressionTerminal occurrence: a hose reel is an IfcFlowTerminal typed by
    # an IfcFireSuppressionTerminalType.  Asking for the IFC4 class must not crash.
    import ifcopenshell
    import ifcopenshell.guid as guid
    m = ifcopenshell.file(schema="IFC2X3")
    t = m.create_entity("IfcFireSuppressionTerminalType", GlobalId=guid.new(), Name="HR",
                        PredefinedType="HOSEREEL")
    hr = m.create_entity("IfcFlowTerminal", GlobalId=guid.new(), Name="HR1")
    m.create_entity("IfcRelDefinesByType", GlobalId=guid.new(), RelatedObjects=[hr], RelatingType=t)
    assert model.objects(m, "IfcFireSuppressionTerminal", ("HOSEREEL",)) == [hr]
    assert model.objects(m, "IfcFireSuppressionTerminal", ("FIREHYDRANT",)) == []
    assert model.objects(m, "IfcSensor", ("SMOKESENSOR",)) == []


def test_ifc4_type_object_supplies_the_predefined_type():
    m = ifcmodel.office()
    import ifcopenshell.api.root as root
    import ifcopenshell.api.spatial as spatial
    import ifcopenshell.api.type as typ
    t = root.create_entity(m, ifc_class="IfcSensorType", predefined_type="SMOKESENSOR")
    sd = root.create_entity(m, ifc_class="IfcSensor", name="SD1")
    typ.assign_type(m, related_objects=[sd], relating_type=t)
    spatial.assign_container(m, products=[sd], relating_structure=m.by_type("IfcBuildingStorey")[0])
    assert model.objects(m, "IfcSensor", ("SMOKESENSOR", "HEATSENSOR")) == [sd]


def test_undeclared_exits_are_one_warning_not_an_error_per_storey():
    import ifcopenshell.api.pset as pset
    import ifcopenshell.util.element as ue
    m = ifcmodel.office(storeys=3)
    for d in m.by_type("IfcDoor"):
        ps = m.by_id(ue.get_psets(d)["Pset_DoorCommon"]["id"])
        pset.edit_pset(m, pset=ps, properties={"FireExit": None})
    r = rules(ifccheck.check_rules(m))
    assert r["IFC-EXIT-DATA"] == 1 and not r["IFC-EXIT-COUNT"] and not r["IFC-EXIT-WIDTH"]


def test_voids_and_gross_area_zones_are_not_floor_area():
    m = ifcmodel.office(storeys=1, space_area=400.0)
    sp = m.by_type("IfcSpace")
    sp[0].LongName = "Open to below"
    f = model.Facts(m)
    assert f.floor_area(sp[0]) == 0.0 and f.floor_area(sp[1]) == 400.0 and f.floor_volume(sp[0]) == 0.0
    sp[0].LongName, sp[0].PredefinedType = "Level 1 GFA", "GFA"
    assert model.Facts(m).floor_area(sp[0]) == 0.0


def test_single_storey_frame_is_exempt_but_compartment_walls_are_not():
    # by-law 216(1): one storey, no basement -> columns and beams need no period
    one = ifcmodel.office(storeys=1, wall_frp="15")
    ifcmodel.add_column(one, rating="15", name="C-low")
    fs = ifccheck.check_rules(one)
    assert not [f for f in fs if f.rule == "IFC-FRP" and "column" in f.message]
    assert [f for f in fs if f.rule == "IFC-FRP-SINGLE"][0].detail["exempt"] == {"column": 1}
    assert [f for f in fs if f.rule == "IFC-FRP" and "wall" in f.message]     # compartment wall: still 30 min
    two = ifcmodel.office(storeys=2)
    ifcmodel.add_column(two, rating="15", name="C-low")
    fs = ifccheck.check_rules(two)
    assert [f for f in fs if f.rule == "IFC-FRP" and "column" in f.message]
    assert not [f for f in fs if f.rule == "IFC-FRP-SINGLE"]


@pytest.mark.parametrize("storeys, storey_h, group, single", [
    (3, 6.0, "IV", True),       # topmost floor 12 m: allowed
    (3, 6.5, "IV", False),      # 13 m: two exits needed
    (2, 4.0, "III", True),      # flats
    (2, 4.0, "V", False),       # a shop building is not domestic or office
])
def test_single_staircase(write, storeys, storey_h, group, single):
    m = ifcmodel.office(storeys=storeys, storey_h=storey_h, group=group, exits_per_storey=1, space_area=50)
    fs = ifccheck.check(write(m))
    r = rules(fs)
    if single:
        assert r["IFC-EXIT-SINGLE"] == storeys and not r["IFC-EXIT-COUNT"]
        msg = next(f for f in fs if f.rule == "IFC-EXIT-SINGLE")
        assert msg.severity == "warning" and "194" in msg.clause and "2-hour wall" in msg.message
    else:
        assert r["IFC-EXIT-COUNT"] == storeys and not r["IFC-EXIT-SINGLE"]


def test_upper_storey_escapes_by_stair(write):
    import ifcopenshell.api.pset as pset
    import ifcopenshell.util.element as ue
    m = ifcmodel.office(storeys=3, exits_per_storey=1, space_area=50)
    f = model.Facts(m)
    for d in m.by_type("IfcDoor"):
        if f.storey_of(d).Name != "L0":      # upper storeys: the door is not an exit, the stair is
            pset.edit_pset(m, pset=m.by_id(ue.get_psets(d)["Pset_DoorCommon"]["id"]), properties={"FireExit": False})
    fs = ifccheck.check(write(m))
    single = {f.message.split(":")[0]: f for f in fs if f.rule == "IFC-EXIT-SINGLE"}
    assert set(single) == {"L0", "L1", "L2"}
    assert "escape stair" in single["L1"].message and "exit door" in single["L0"].message
    assert single["L1"].where == [s.GlobalId for s in m.by_type("IfcStair") if s.Name == "ST0"]
    assert not rules(fs)["IFC-EXIT-WIDTH"]     # stair widths are not judged
    # too tall for by-law 194: the stair still counts, as one exit of the two needed
    m = ifcmodel.office(storeys=4, storey_h=5.0, exits_per_storey=1, space_area=50)
    fs = ifccheck.check(write(m))
    assert rules(fs)["IFC-EXIT-COUNT"] == 4


@pytest.mark.parametrize("name, void", [("Roof", True), ("Attic space", True), ("Roof garden", False),
                                        ("Roof terrace", False), ("Void", True), ("Avoid store", False),
                                        ("Open to below", True)])
def test_spaces_that_are_not_floor(name, void):
    import ifcopenshell
    m = ifcopenshell.file(schema="IFC4")       # kept: an entity dies with its file
    sp = m.createIfcSpace(Name="01", LongName=name)
    assert model.not_floor(sp) is void, name


@pytest.mark.parametrize("closing, rule, n", [(True, None, 0), (False, "IFC-DOORCLOSER", 4),
                                              (None, "IFC-DOORCLOSER-DATA", 1)])
def test_fire_doors_self_close(write, closing, rule, n):
    r = rules(ifccheck.check(write(ifcmodel.office(self_closing=closing))))
    assert r["IFC-DOORCLOSER"] + r["IFC-DOORCLOSER-DATA"] == n
    if rule:
        assert r[rule] == n


def _wall(m, name, rating, **props):
    import ifcopenshell.api.root as root
    import ifcopenshell.api.spatial as spatial
    w = root.create_entity(m, ifc_class="IfcWall", name=name)
    common = {"FireRating": rating, **{k: v for k, v in props.items() if k != "SeparatingWall"}}
    ifcmodel._ps(m, w, "Pset_WallCommon", common)
    if "SeparatingWall" in props:
        ifcmodel._ps(m, w, "MY_FireSafety", {"SeparatingWall": props["SeparatingWall"]})
    spatial.assign_container(m, products=[w], relating_structure=m.by_type("IfcBuildingStorey")[0])
    return w


def test_external_and_separating_wall_minimums(write):
    m = ifcmodel.office(storeys=1, space_area=200)       # single storey: no Ninth period for internal walls
    ext = _wall(m, "EXT", "15", IsExternal=True, LoadBearing=True)
    _wall(m, "EXT-NLB", "15", IsExternal=True, LoadBearing=False)   # may be an unprotected area: 214(2)
    sep = _wall(m, "SEP", "30", SeparatingWall=True)
    _wall(m, "INT", "0", LoadBearing=True)                           # exempt, by-law 216(1)
    fs = [f for f in ifccheck.check(write(m)) if f.rule == "IFC-FRP"]
    by = {tuple(f.where): f for f in fs}
    assert set(by) == {(ext.GlobalId,), (sep.GlobalId,)}, [f.message for f in fs]
    assert by[(ext.GlobalId,)].detail["required_min"] == 30     # Ninth Part 2 and 214(1)(a) agree
    assert by[(sep.GlobalId,)].detail["required_min"] == 60
    assert by[(sep.GlobalId,)].clause == "UBBL by-law 214(1)"


def test_unknown_occupancy_use_is_reported(write):
    import ifcopenshell.api.pset as pset
    import ifcopenshell.util.element as ue
    m = ifcmodel.office(storeys=1)
    sp = m.by_type("IfcSpace")[0]
    pset.edit_pset(m, pset=m.by_id(ue.get_psets(sp)["MY_FireSafety"]["id"]), properties={"OccupancyUse": "ballroom"})
    fs = ifccheck.check(write(m))
    occ = [f for f in fs if f.rule == "IFC-OCCUPANCY"]
    assert len(occ) == 1 and occ[0].where == [sp.GlobalId] and "ballroom" in occ[0].message
    f = model.Facts(m)
    assert f.persons(sp) is None and f.persons(m.by_type("IfcSpace")[1]) == 40   # 400 m2 at 10 m2 a person


def test_exit_doors_close_by_themselves(write):
    m = ifcmodel.office(storeys=1, door_frp=None, self_closing=False)     # exit doors, not fire doors
    fs = [x for x in ifccheck.check(write(m)) if x.rule == "IFC-DOORCLOSER"]
    assert len(fs) == 2 and all(x.clause == "UBBL by-law 173(2)" and "exit door" in x.message for x in fs)
    m = ifcmodel.office(storeys=1, self_closing=False)                     # rated exit doors: both by-laws
    fs = [x for x in ifccheck.check(write(m)) if x.rule == "IFC-DOORCLOSER"]
    assert fs and fs[0].clause == "UBBL by-law 164(1), 173(2)"


def _named_space(m, name, area):
    import ifcopenshell.api.aggregate as agg
    import ifcopenshell.api.root as root
    sp = root.create_entity(m, ifc_class="IfcSpace", name="X1")
    sp.LongName = name
    agg.assign_object(m, products=[sp], relating_object=m.by_type("IfcBuildingStorey")[0])
    ifcmodel._ps(m, sp, "MY_FireSafety", {"CompartmentId": "C0-0"})
    ifcmodel._qto(m, sp, "Qto_SpaceBaseQuantities", {"NetFloorArea": area, "Height": 3.5})
    return sp


def test_tall_building_rooms(write):
    r = rules(ifccheck.check(write(ifcmodel.office(storeys=2, height=35.0))))
    assert r["IFC-COMMAND-CENTRE"] == 1
    m = ifcmodel.office(storeys=2, height=35.0)
    _named_space(m, "Bilik Kawalan Bomba", 20)
    small = _named_space(m, "Fire Fighting Lobby", 4.0)
    _named_space(m, "Lobi Bomba", 6.0)
    fs = ifccheck.check(write(m))
    assert not rules(fs)["IFC-COMMAND-CENTRE"]
    lob = [x for x in fs if x.rule == "IFC-FIRE-LOBBY"]
    assert [x.where for x in lob] == [[small.GlobalId]] and lob[0].clause == "UBBL by-law 242(a)"
    assert not rules(ifccheck.check(write(ifcmodel.office(storeys=2, height=30.0))))["IFC-COMMAND-CENTRE"]


@pytest.mark.parametrize("name, centre, lobby", [
    ("Fire Command Centre", True, False), ("Command & Control Centre", True, False),
    ("Bilik Kawalan Bomba", True, False), ("Bilik Kawalan CCTV", False, False),
    ("Pusat Kawalan Keselamatan", False, False), ("Firefighters' Lobby", False, True),
    ("Fire Fighting Lift Lobby", False, True), ("Fireman's Lobby", False, True),
    ("Lobi Lif Bomba", False, True), ("Landing FFL +3.000", False, False), ("Lift Lobby", False, False),
])
def test_room_names(name, centre, lobby):
    import ifcopenshell
    m = ifcopenshell.file(schema="IFC4")       # kept: an entity dies with its file
    sp = m.createIfcSpace(Name="01", ObjectType=name)   # a Revit room type
    assert model.named(sp, rooms.COMMAND_CENTRE) is centre
    assert model.named(sp, rooms.FIRE_LOBBY) is lobby


def _basement_boiler(sprinklered=False, inlet=None, room="Boiler Room", elevation=-4.0):
    import ifcopenshell.api.aggregate as agg
    import ifcopenshell.api.root as root
    import ifcopenshell.api.spatial as spatial
    import ifcopenshell.api.type as typ
    m = ifcmodel.office(storeys=2, sprinklered=sprinklered)
    b = root.create_entity(m, ifc_class="IfcBuildingStorey", name="B1")
    b.Elevation = elevation
    agg.assign_object(m, products=[b], relating_object=m.by_type("IfcBuilding")[0])
    sp = root.create_entity(m, ifc_class="IfcSpace", name="B1-01")
    sp.LongName = room
    agg.assign_object(m, products=[sp], relating_object=b)
    fi = None
    if inlet == "typed":
        fi = root.create_entity(m, ifc_class="IfcFireSuppressionTerminal", name="FI1", predefined_type="USERDEFINED")
        fi.ObjectType = "FOAMINLET"
    elif inlet in ("named", "tank"):
        fi = root.create_entity(m, ifc_class="IfcFireSuppressionTerminal",
                                name="Foam Inlet FI1" if inlet == "named" else "Foam Concentrate Tank")
    elif inlet == "type-named":         # Revit: the family type carries the name
        t = root.create_entity(m, ifc_class="IfcFireSuppressionTerminalType", name="Foam Inlet 2-way")
        fi = root.create_entity(m, ifc_class="IfcFireSuppressionTerminal", name="Fire Suppression Terminal:FI-01")
        typ.assign_type(m, related_objects=[fi], relating_type=t)
    if fi is not None:
        spatial.assign_container(m, products=[fi], relating_structure=m.by_type("IfcSite")[0])
    return m, sp


@pytest.mark.parametrize("sprinklered, inlet, room, elevation, warned", [
    (False, None, "Boiler Room", -4.0, True),
    (False, "typed", "Boiler Room", -4.0, False),
    (False, "named", "Boiler Room", -4.0, False),
    (False, "type-named", "Boiler Room", -4.0, False),
    (False, "tank", "Boiler Room", -4.0, True),          # foam equipment that is not an inlet
    (True, None, "Boiler Room", -4.0, False),
    (False, None, "Bilik Stor", -4.0, True),
    (False, None, "Storage", -4.0, True),
    (False, None, "Convenience Store", -4.0, False),     # a shop, not a storage area
    (False, None, "Boiler Room", -0.8, True),            # lower ground storey, still below ground
])
def test_foam_inlet_for_basement_boiler(write, sprinklered, inlet, room, elevation, warned):
    m, sp = _basement_boiler(sprinklered, inlet, room, elevation)
    fs = [x for x in ifccheck.check(write(m)) if x.rule == "IFC-FOAM-INLET"]
    assert bool(fs) is warned
    if warned:
        assert fs[0].where == [sp.GlobalId] and fs[0].clause == "UBBL by-law 233"


@pytest.mark.parametrize("name, long_name, shown", [("101", "Office", "101 Office"), ("101", None, "101"),
                                                    ("Office", "Office", "Office"), (None, None, "GUID")])
def test_findings_name_rooms_by_number_and_name(name, long_name, shown):
    import ifcopenshell
    m = ifcopenshell.file(schema="IFC4")
    sp = m.createIfcSpace(GlobalId="GUID", Name=name, LongName=long_name)
    assert model.label(sp) == shown


def test_ifc2x3_user_defined_type():
    import ifcopenshell
    import ifcopenshell.guid as guid
    m = ifcopenshell.file(schema="IFC2X3")
    t = m.create_entity("IfcFireSuppressionTerminalType", GlobalId=guid.new(), Name="FI",
                        PredefinedType="USERDEFINED", ElementType="FOAMINLET")
    fi = m.create_entity("IfcFlowTerminal", GlobalId=guid.new(), Name="FI1")
    m.create_entity("IfcRelDefinesByType", GlobalId=guid.new(), RelatedObjects=[fi], RelatingType=t)
    assert model.objects(m, "IfcFireSuppressionTerminal", ("FOAMINLET",)) == [fi]


@pytest.mark.parametrize("basement, windows, vent, warned", [
    ("Car Park", True, None, True),
    ("Car Park", True, "Smoke Extract Fan SEF-1", False),
    ("Car Park", True, "Pelega Asap 2", False),
    ("Car Park", True, "Smoke & Heat Vent SHV-1", False),
    ("Car Park", True, "type:AOV roof vent", False),         # named on its type object only
    ("Car Park", True, "Exhaust Fan EF-1", True),            # an ordinary fan is not a smoke vent
    ("Car Park", True, "Peti Sef", True),                    # a safe
    ("Open to below", True, None, False),                    # a void below ground is not an underground storey
    (None, True, None, False),
    (None, False, None, True),                               # windowless
    (None, False, "Smoke Vent", False),
])
def test_smoke_venting(write, basement, windows, vent, warned):
    import ifcopenshell.api.root as root
    import ifcopenshell.api.spatial as spatial
    import ifcopenshell.api.type as typ
    m = _basement_boiler(room=basement)[0] if basement else ifcmodel.office(storeys=2)
    if not windows:
        for w in m.by_type("IfcWindow"):
            m.remove(w)
    if vent:
        on_type = vent.startswith("type:")
        fan = root.create_entity(m, ifc_class="IfcFan", name="F1" if on_type else vent)
        if on_type:
            t = root.create_entity(m, ifc_class="IfcFanType", name=vent[5:])
            typ.assign_type(m, related_objects=[fan], relating_type=t)
        spatial.assign_container(m, products=[fan], relating_structure=m.by_type("IfcBuildingStorey")[0])
    fs = [x for x in ifccheck.check(write(m)) if x.rule == "IFC-SMOKE-VENT"]
    assert bool(fs) is warned, [x.message for x in fs]
    if warned:
        assert fs[0].clause == "UBBL by-law 249" and ("underground" in fs[0].message) is bool(basement)
        if basement:
            assert fs[0].where == [s.GlobalId for s in m.by_type("IfcBuildingStorey") if s.Name == "B1"]
