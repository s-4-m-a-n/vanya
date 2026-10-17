import math

import pytest

from app.models import Geoprivacy, Taxon
from app.services.geoprivacy import generalise, strictest, taxon_geoprivacy

OPEN, OBSCURED, HIDDEN = Geoprivacy.OPEN, Geoprivacy.OBSCURED, Geoprivacy.HIDDEN


def taxon(status="LC", source="seed", sensitivity=None) -> Taxon:
    return Taxon(iucn_status=status, status_source=source, sensitivity=sensitivity)


@pytest.mark.parametrize(
    ("kwargs", "level"),
    [
        ({}, OPEN),
        ({"status": "NT"}, OPEN),
        ({"status": "VU"}, OBSCURED),
        ({"status": "EN"}, OBSCURED),
        ({"status": "CR"}, OBSCURED),
        ({"status": "DD"}, OBSCURED),
        ({"status": "NE", "source": None}, OBSCURED),  # never checked: could be threatened
        ({"status": "NE", "source": "gbif_iucn"}, OPEN),  # checked: genuinely unassessed
        ({"status": "LC", "sensitivity": "hidden"}, HIDDEN),  # e.g. a poaching target
        ({"status": "EN", "sensitivity": "open"}, OPEN),  # admin decided it's safe
    ],
)
def test_species_sensitivity(kwargs, level):
    assert taxon_geoprivacy(taxon(**kwargs)) == level


def test_unknown_species_is_obscured():
    assert taxon_geoprivacy(None) == OBSCURED


def test_strictest_wins():
    assert strictest(OPEN, OBSCURED) == OBSCURED
    assert strictest(HIDDEN, OPEN, OBSCURED) == HIDDEN
    assert strictest(OPEN) == OPEN


def test_open_points_are_rounded_to_protect_observers():
    point = generalise(51.507234, -0.127612, 8.0, OPEN)
    assert (point.latitude, point.longitude) == (51.507, -0.128)
    assert point.accuracy_m == 110


@pytest.mark.parametrize(
    ("lat", "lon", "level", "center"),
    [
        (51.5072, -0.1276, OBSCURED, (51.5, -0.1)),
        (-33.8568, 151.2153, OBSCURED, (-33.9, 151.3)),
        (27.7172, 85.3240, HIDDEN, (27.5, 85.5)),
        (0.0, 0.0, OBSCURED, (0.1, 0.1)),
    ],
)
def test_protected_points_snap_to_cell_centres(lat, lon, level, center):
    point = generalise(lat, lon, 10, level)
    assert (point.latitude, point.longitude) == pytest.approx(center)
    step = 0.2 if level == OBSCURED else 1.0
    assert abs(point.latitude - lat) <= step / 2 and abs(point.longitude - lon) <= step / 2


def test_reported_accuracy_covers_the_whole_cell():
    point = generalise(0.05, 0.05, 5, OBSCURED)
    assert point.accuracy_m == pytest.approx(math.hypot(11_132, 11_132), rel=0.01)  # ~15.7 km
    assert generalise(60.05, 10.05, 5, OBSCURED).accuracy_m < point.accuracy_m  # narrower cells
