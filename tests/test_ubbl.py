import math

import pytest

from ubblcheck import ubbl


def test_compartment_limits_follow_fifth_schedule():
    assert ubbl.compartment_limit("V", 10).area_m2 == 2000
    assert ubbl.compartment_limit("V", 10).volume_m3 == 7000
    assert ubbl.compartment_limit("III", 28).volume_m3 == 8500      # "not exceeding 28 m"
    assert ubbl.compartment_limit("III", 28.1).volume_m3 == 5500
    assert ubbl.compartment_limit("VI", 20).area_m2 == math.inf
    assert ubbl.compartment_limit("VIII", 40).area_m2 == 1000
    assert ubbl.compartment_limit("II", 5, single_storey=True).area_m2 == 3000


def test_groups_without_limits():
    for g in ("I", "IV", "VII"):
        assert ubbl.compartment_limit(g, 50) is None
    assert ubbl.compartment_limit("V", 5, single_storey=True) is None


def test_sprinklers_double_limits():
    lim = ubbl.compartment_limit("V", 10, sprinklered=True)
    assert (lim.area_m2, lim.volume_m3) == (4000, 14000)
    assert "doubled" in lim.clause


def test_travel_distances():
    assert ubbl.travel_limit("IV", False).travel_m == 45
    assert ubbl.travel_limit("IV", True).travel_m == 60
    assert ubbl.travel_limit("IV", True).dead_end_m == 15
    assert ubbl.travel_limit("II", False, "school").travel_m == 45
    assert ubbl.travel_limit("II", False).travel_m == 30          # hospital default
    assert ubbl.travel_limit("VII", True).travel_m == 61
    assert ubbl.travel_limit("I", True).travel_m is None
    assert ubbl.travel_limit("V", False).travel_m == 45 and "V" in ubbl.ERRATA
    with pytest.raises(KeyError):
        ubbl.travel_limit("IV", False, "casino")


def test_occupant_load_and_exit_width():
    assert ubbl.occupant_load(1000, "IV") == 100                   # 10 m2 gross per person
    assert ubbl.occupant_load(100, "VII") == 67                    # 1.5 m2 net, rounded up
    assert ubbl.occupant_load(30, "III", "flat") == 8
    assert ubbl.exit_units(100, "IV", "door") == 1
    assert ubbl.exit_units(101, "IV", "door") == 2
    assert ubbl.exit_units(61, "IV", "stair") == 2
    assert ubbl.exit_units(23, "II", "stair", "hospital") == 2
    assert ubbl.width_units(1100) == 2 and ubbl.width_units(1099) == 1
    with pytest.raises(ValueError):
        ubbl.exit_units(10, "II", "escalator")


def test_fire_appliance_access_scale():
    assert ubbl.access_perimeter_share(7000) == 0
    assert ubbl.access_perimeter_share(7001) == pytest.approx(1 / 6)
    assert ubbl.access_perimeter_share(30000) == pytest.approx(1 / 4)
    assert ubbl.access_perimeter_share(200000) == 1.0
