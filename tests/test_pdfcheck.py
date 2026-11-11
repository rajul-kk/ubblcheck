import collections

import pytest

import drawings
from ubblcheck import pdfcheck, pdfread


def run(tmp_path, passive_kw=None, active_kw=None, with_active=True, **form_kw):
    p = drawings.passive_set(tmp_path / "passive.pdf", **(passive_kw or {}))
    a = drawings.active_set(tmp_path / "active.pdf", **(active_kw or {})) if with_active else None
    form_kw.setdefault("submission", "active" if with_active else "passive")
    form_kw.setdefault("passive_approval_ref", "JBPM/PASSIVE/1" if with_active else None)
    form = pdfcheck.Form.load(drawings.form(tmp_path, p, a, **form_kw))
    fs, sheets = pdfcheck.check(form)
    return fs, sheets


def rules(fs):
    return collections.Counter(f.rule for f in fs)


def test_sheet_reading(tmp_path):
    sheets = pdfread.read(drawings.passive_set(tmp_path / "p.pdf", levels=("GROUND", "FIRST", "SECOND")))
    assert [s.kind for s in sheets] == ["general", "site_plan", "floor_plan", "floor_plan", "floor_plan"]
    assert [s.level for s in sheets[2:]] == ["L0", "L1", "L2"]
    assert sheets[2].number == "A-100"
    assert sheets[1].colours["yellow"] > pdfcheck.COLOUR_MIN
    assert sheets[2].colours["red"] > pdfcheck.COLOUR_MIN


@pytest.mark.parametrize("title,level", [
    ("GROUND FLOOR PLAN", "L0"), ("LEVEL 12 PLAN", "L12"), ("3RD FLOOR PLAN", "L3"),
    ("BASEMENT 2 PLAN", "B2"), ("PELAN TINGKAT 4", "L4"), ("PELAN LANTAI BAWAH", "L0"),
    ("ROOF PLAN", "ROOF"), ("LOWER GROUND FLOOR PLAN", "LG"),
    ("BASEMENT LEVEL 2 PLAN", "B2"), ("BASEMENT LEVEL 1 PLAN", "B1"), ("BASEMENT PLAN", "B1"),
    ("PELAN ARAS BAWAH TANAH 2", "B2")])
def test_levels(title, level):
    assert pdfread.level_of(title) == level


def test_clean_sets(tmp_path):
    fs, _ = run(tmp_path)
    assert fs == []


def test_passive_only_clean(tmp_path):
    fs, _ = run(tmp_path, with_active=False)
    assert fs == []


def test_missing_items_are_named_with_sheets(tmp_path):
    fs, _ = run(tmp_path, passive_kw={"drop": ["EMERGENCY LIGHT", "OCCUPANCY LOAD", "PERAKUAN"]},
                with_active=False)
    r = rules(fs)
    assert r["PDF-P12"] == 1 and r["PDF-PG2"] == 1 and r["PDF-PG1"] == 1
    el = next(f for f in fs if f.rule == "PDF-P12")
    assert "2 of 2" in el.message and len(el.where) == 2 and "A-100" in el.where[0]
    assert "by-law 253" in el.clause


def test_conditional_items(tmp_path):
    # a tall building needs fire lift, riser and intercom items
    fs, _ = run(tmp_path, with_active=False, top_floor_m=35.0)
    r = rules(fs)
    assert r["PDF-P16"] == 1                    # intercom (> 30.5 m) not drawn
    assert r["PDF-P17"] == 1                    # fire lift: no FL tag on plans
    assert r["PDF-PS2"] == 0                    # BI1 is on the site plan
    fs, _ = run(tmp_path, sprinklered=True)
    assert rules(fs)["PDF-A04"] == 1            # sprinklered but no heads on active plans


def test_passive_active_mismatch(tmp_path):
    fs, _ = run(tmp_path, active_kw={"extra_tags": {"FIRST": ["HR2", "FD3"]}, "levels": ("GROUND", "FIRST", "SECOND")})
    r = rules(fs)
    tagf = [f for f in fs if f.rule == "PDF-X-TAG"]
    assert {f.detail["tag"] for f in tagf} == {"hose_reel", "fire_door"}
    assert all(f.detail["active_only"] for f in tagf)
    assert r["PDF-X-SHEET"] == 1                # SECOND only in the active set


def test_missing_active_level_is_error(tmp_path):
    fs, _ = run(tmp_path, active_kw={"levels": ("GROUND",)})
    x = [f for f in fs if f.rule == "PDF-X-SHEET"]
    assert len(x) == 1 and x[0].severity == "error" and "L1" in x[0].message


def test_yellow_road_on_active(tmp_path):
    fs, _ = run(tmp_path, active_kw={"yellow": True})
    y = [f for f in fs if f.rule == "PDF-YELLOW"]
    assert len(y) == 1 and "M-001" in y[0].where[0]


def test_active_before_passive_approval(tmp_path):
    fs, _ = run(tmp_path, passive_approval_ref=None)
    assert rules(fs)["PDF-ORDER"] == 1


def test_ramp_gradient_by_state(tmp_path):
    fs, _ = run(tmp_path, passive_kw={"ramp": "1:12"}, with_active=False, state="Selangor")
    r = [f for f in fs if f.rule == "PDF-RAMP"]
    assert len(r) == 1 and r[0].detail["limit"] == 15
    fs, _ = run(tmp_path, passive_kw={"ramp": "1:12"}, with_active=False, state="Johor")
    assert rules(fs)["PDF-RAMP"] == 0


def test_travel_distance_against_seventh_schedule(tmp_path):
    fs, _ = run(tmp_path, passive_kw={"td": "52"}, with_active=False)
    t = [f for f in fs if f.rule == "PDF-TRAVEL"]
    assert len(t) == 2 and t[0].detail["limit_m"] == 45
    fs, _ = run(tmp_path, passive_kw={"td": "52"}, with_active=False, sprinklered=True)
    assert rules(fs)["PDF-TRAVEL"] == 0


def test_missing_colour_is_warned(tmp_path):
    p = drawings.write_set(tmp_path / "p.pdf", [("SITE PLAN", "A-1", ["HYDRANT H1", "FIRE ENGINE ACCESS ROAD"], [])])
    form = pdfcheck.Form.load(drawings.form(tmp_path, p, None))
    fs, _ = pdfcheck.check(form)
    assert any(f.rule == "PDF-PS4-COLOUR" for f in fs)


def test_unknown_sub_use_is_reported_not_skipped(tmp_path):
    fs, _ = run(tmp_path, passive_kw={"td": "90"}, with_active=False, sub_use="hospital")   # group IV
    r = rules(fs)
    assert r["PDF-FORM"] == 1 and r["PDF-TRAVEL"] == 0
    assert "hospital" in next(f for f in fs if f.rule == "PDF-FORM").message


def test_blank_numbers_in_the_form_do_not_crash(tmp_path):
    fs, _ = run(tmp_path, with_active=False, top_floor_m=None, volume_m3=None)
    forms = [f for f in fs if f.rule == "PDF-FORM"]
    assert {f.detail["field"] for f in forms} == {"top_floor_m", "volume_m3"}
    assert all(f.severity == "warning" for f in forms)
    # unknown height: the items for tall buildings are checked rather than skipped
    assert rules(fs)["PDF-P16"] == 1


def test_bad_form_values_are_errors(tmp_path):
    fs, _ = run(tmp_path, with_active=False, purpose_group="IX", submission="later")
    bad = {f.detail["field"] for f in fs if f.rule == "PDF-FORM" and f.severity == "error"}
    assert bad == {"purpose_group", "submission"}
