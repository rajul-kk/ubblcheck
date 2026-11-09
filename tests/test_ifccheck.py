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
