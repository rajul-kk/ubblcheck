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
    sheets = pdfread.read_all([drawings.passive_set(tmp_path / "p.pdf", levels=("GROUND", "FIRST", "SECOND"))])[0]
    assert [s.kind for s in sheets] == ["general", "site_plan", "floor_plan", "floor_plan", "floor_plan"]
    assert [s.level for s in sheets[2:]] == ["L0", "L1", "L2"]
    assert sheets[2].number == "A-100"
    assert sheets[1].colours["yellow"] > pdfcheck.COLOUR_MIN
    assert sheets[2].colours["red"] > pdfcheck.COLOUR_MIN


def test_files_read_in_parallel_keep_their_pages(tmp_path, monkeypatch):
    (tmp_path / "a").mkdir(); (tmp_path / "b").mkdir()
    p = drawings.passive_set(tmp_path / "a" / "set.pdf", levels=("GROUND", "FIRST", "SECOND"))
    a = drawings.active_set(tmp_path / "b" / "set.pdf")             # the same file name
    as_dicts = lambda files: [[s.__dict__ for s in f] for f in files]
    monkeypatch.setattr(pdfread, "PARALLEL_FROM_PAGES", 10**6)
    serial = as_dicts(pdfread.read_all([p, a]))
    monkeypatch.setattr(pdfread, "PARALLEL_FROM_PAGES", 1)
    assert as_dicts(pdfread.read_all([p, a])) == serial
    assert [len(f) for f in serial] == [5, 4]


@pytest.mark.parametrize("title,level", [
    ("GROUND FLOOR PLAN", "L0"), ("LEVEL 12 PLAN", "L12"), ("3RD FLOOR PLAN", "L3"),
    ("BASEMENT 2 PLAN", "B2"), ("PELAN TINGKAT 4", "L4"), ("PELAN LANTAI BAWAH", "L0"),
    ("ROOF PLAN", "ROOF"), ("LOWER GROUND FLOOR PLAN", "LG"),
    ("BASEMENT LEVEL 2 PLAN", "B2"), ("BASEMENT LEVEL 1 PLAN", "B1"), ("BASEMENT PLAN", "B1"),
    ("PELAN ARAS BAWAH TANAH 2", "B2")])
def test_levels(title, level):
    assert pdfread.level_of(title) == level


def tenth_line(fs):
    """Split off the info finding that names the Tenth Schedule line; return (line, the rest)."""
    lines = [f for f in fs if f.rule == "PDF-TENTH"]
    assert len(lines) == 1 and lines[0].severity == "info"
    return lines[0].detail["line"], [f for f in fs if f.rule != "PDF-TENTH"]


def test_clean_sets(tmp_path):
    fs, _ = run(tmp_path)
    assert tenth_line(fs) == ("IV 2", [])


def test_passive_only_clean(tmp_path):
    fs, _ = run(tmp_path, with_active=False)
    assert tenth_line(fs) == ("IV 2", [])


def test_tenth_line_decides_active_items(tmp_path):
    # IV 2 needs hose reels and manual alarms but not detectors: their absence is not a finding
    fs, _ = run(tmp_path, active_kw={"drop": ["SMOKE DETECTOR"]})
    assert "PDF-A03" not in rules(fs)
    # a large office (IV 3) does need detectors
    fs, _ = run(tmp_path, active_kw={"drop": ["SMOKE DETECTOR"]}, top_floor_m=22.0, gross_area_m2=6000,
                storeys=6)
    assert tenth_line(fs)[0] == "IV 3" and rules(fs)["PDF-A03"] == 1


def test_tenth_missing_size_checks_everything(tmp_path):
    fs, _ = run(tmp_path, active_kw={"drop": ["SMOKE DETECTOR"]}, gross_area_m2=None)
    form = [f for f in fs if f.rule == "PDF-FORM"]
    assert len(form) == 1 and form[0].detail["fields"] == ["gross_area_m2"]
    assert rules(fs)["PDF-A03"] == 1            # undecided: checked, not skipped


def test_tenth_sprinklers_required_but_not_declared(tmp_path):
    fs, _ = run(tmp_path, top_floor_m=40.0, gross_area_m2=9000, storeys=10)
    r = rules(fs)
    assert r["PDF-TENTH-A"] == 1 and r["PDF-A04"] == 1   # and the sprinkler layout is asked for


def test_assembly_has_no_line_so_everything_is_checked(tmp_path):
    fs, _ = run(tmp_path, active_kw={"drop": ["SMOKE DETECTOR"]}, purpose_group="VII")
    assert rules(fs)["PDF-A03"] == 1


def test_tenth_bad_inputs(tmp_path):
    fs, _ = run(tmp_path, tenth_use="II.nowhere", storeys="two")
    fields = {f.detail["field"] for f in fs if f.rule == "PDF-FORM"}
    assert fields == {"tenth_use", "storeys"}
    fs, _ = run(tmp_path, tenth_use="II.library", storeys=1, gross_area_m2=1200)
    assert tenth_line(fs)[0] == "II 1(iv)(c)"


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


def test_submission_types_follow_their_rules(tmp_path):
    fs, _ = run(tmp_path, submission="both", passive_approval_ref=None)
    order = [f for f in fs if f.rule == "PDF-ORDER"]
    assert [f.severity for f in order] == ["warning"]           # together is allowed; approval not needed
    fs, _ = run(tmp_path, with_active=False, submission="both")
    sets = [f for f in fs if f.rule == "PDF-SET"]
    assert len(sets) == 1 and "no active set" in sets[0].message
    fs, _ = run(tmp_path, with_active=False, submission="active")
    assert {f.rule for f in fs} >= {"PDF-ORDER", "PDF-SET"}
