import collections
import zipfile

import pytest
from ifctester import ids

import ifcmodel
from ubblcheck import findings, ids_build, ifccheck


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


def test_frp_parsing():
    assert ifccheck.frp_minutes("60") == 60
    assert ifccheck.frp_minutes("FRP 120 min") == 120
    assert ifccheck.frp_minutes("1 HR") == 60
    assert ifccheck.frp_minutes("1/2 hour") == 30
    assert ifccheck.frp_minutes("fire door") is None
    assert ifccheck.frp_minutes("REI 120") == 120
    assert ifccheck.frp_minutes("EI 60") == 60
    assert ifccheck.frp_minutes("120 minutes") == 120
    assert ifccheck.frp_minutes("2 HOURS") == 120


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
