"""Tenth Schedule look-ups against the gazetted text (G.N. 5178/1984): one or more cases per group."""
import pytest

from ubblcheck import ubbl


def line(use, **kw):
    r = ubbl.tenth(use, **kw)
    assert r.decided, (use, kw, r.missing)
    return r.row


def codes(row):
    ext = "|".join("".join(sorted(a)) for a in row.ext)
    return ext, "&".join(sorted(row.alarm)), "|".join(sorted(row.light))


@pytest.mark.parametrize("use,kw,ref,expect", [
    ("I", {}, "I", ("", "", "")),
    ("II.education", {"storeys": 2}, "II 1(i)(a)", ("", "", "")),
    ("II.education", {"storeys": 4}, "II 1(i)(b)", ("G", "", "")),
    ("II.education", {"storeys": 8}, "II 1(i)(c)", ("G", "2", "a")),
    ("II.education", {"storeys": 12}, "II 1(i)(d)", ("G", "1&2", "a")),
    ("II.library", {"storeys": 2, "total_area_m2": 800}, "II 1(iv)(b)", ("G", "", "")),
    ("II.library", {"storeys": 1, "total_area_m2": 1200}, "II 1(iv)(c)", ("G", "1&2", "a")),
    ("II.hospital", {"floor_area_m2": 200, "height_m": 7, "storeys": 2}, "II 2(ii)(bb)", ("", "3", "a")),
    ("II.hospital", {"floor_area_m2": 200, "height_m": 20, "storeys": 7}, "II 2(ii)(be)", ("AG", "3", "b|c")),
    ("II.hospital", {"floor_area_m2": 600, "height_m": 10, "storeys": 3}, "II 2(ii)(c)", ("G", "1&3", "a")),
    ("III.hotel", {"height_m": 10, "rooms": 30, "storeys": 3}, "III 1(ii)(c)", ("G", "1&2", "a")),
    ("III.hotel", {"height_m": 25, "rooms": 30, "storeys": 8}, "III 1(ii)(f)", ("AG", "2", "c")),
    ("III.hotel_open", {"storeys": 2, "rooms": 60}, "III 1(i)(b)(ii)", ("G", "", "b")),
    ("III.dormitory", {"storeys": 3}, "III 2(a)(iii)", ("G", "1&2", "a")),
    ("IV", {"height_m": 8, "total_area_m2": 800, "storeys": 3}, "IV 1", ("", "", "")),
    ("IV", {"height_m": 14, "total_area_m2": 2400, "storeys": 4}, "IV 2", ("G", "2", "a")),
    ("IV", {"height_m": 22, "total_area_m2": 6000, "storeys": 6}, "IV 3", ("G", "1&2", "c")),
    ("IV", {"height_m": 40, "total_area_m2": 9000, "storeys": 10}, "IV 4", ("AG", "2", "c")),
    ("IV", {"height_m": 10, "total_area_m2": 12000, "storeys": 3}, "IV 4", ("AG", "2", "c")),
    ("V", {"storeys": 1, "total_area_m2": 900}, "V 2(b)", ("G", "2", "a")),
    ("V", {"storeys": 2, "total_area_m2": 600}, "V 3", ("G", "", "a")),
    ("V", {"storeys": 4, "total_area_m2": 3500}, "V 3", ("AG", "2", "c")),
    ("VI", {"storeys": 1, "total_area_m2": 800}, "VI 1(b)", ("G", "2", "")),
    ("VI", {"storeys": 2, "floor_area_m2": 600}, "VI 3(b)", ("G", "2", "a")),
    ("VI", {"storeys": 4, "volume_m3": 5000}, "VI 4(b)", ("GH", "1&2", "c")),
    ("VI", {"storeys": 4, "volume_m3": 9000}, "VI 4(b)", ("AG", "2", "c")),
    ("VIII.parking", {}, "VIII 1", ("GH", "2", "a")),
    ("VIII.storage", {"storeys": 1, "total_area_m2": 400, "volume_m3": 1200}, "VIII 3(a)(ii)", ("G", "2", "")),
    ("VIII.storage", {"storeys": 1, "total_area_m2": 900, "volume_m3": 5000}, "VIII 3(a)", ("GH", "1&2", "a")),
    ("VIII.storage", {"storeys": 3, "total_area_m2": 1500}, "VIII 3(b)", ("AG", "2", "a")),
])
def test_lines(use, kw, ref, expect):
    row = line(use, **kw)
    assert row.ref == ref and codes(row) == expect


def test_alternatives_and_requires():
    row = line("VI.special")
    assert codes(row)[0] == "A|B|C|D|E|F" and row.refer
    assert not row.requires("A")                 # any one of six systems will do
    big = line("IV", height_m=40, total_area_m2=9000, storeys=10)
    assert big.requires("A") and big.requires("G") and big.requires("2") and not big.requires("1")
    assert "automatic sprinklers and hose reels" in big.describe()


def test_missing_inputs_are_named_not_guessed():
    r = ubbl.tenth("IV", storeys=3)
    assert not r.decided and r.missing == ("height_m",)
    r = ubbl.tenth("III.hotel", height_m=10, storeys=2)
    assert not r.decided and "rooms" in r.missing


def test_assembly_has_no_general_line():
    row = line("VII")
    assert row.ext == () and "special assembly risks" in row.refer


def test_default_uses_from_the_seventh_schedule():
    assert ubbl.tenth_use("II") == "II.hospital"
    assert ubbl.tenth_use("II", "school") == "II.education"
    assert ubbl.tenth_use("VIII", "parking") == "VIII.parking"
    assert ubbl.tenth_use("IV", override="II.library") == "II.library"
    with pytest.raises(KeyError):
        ubbl.tenth_use("IV", override="II.nowhere")


def test_every_use_reads_and_every_errata_key_exists():
    for use in ubbl.tenth_uses():
        for r in ubbl._tenth_data()["uses"][use]["rows"]:
            assert r.get("errata") in (None, *ubbl.ERRATA)
            ubbl._row(use, "", r)
