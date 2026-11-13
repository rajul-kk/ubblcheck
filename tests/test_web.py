import io
import json
import os

import pytest

pytest.importorskip("flask")

import drawings  # noqa: E402
import geomodel as G  # noqa: E402
import ifcmodel  # noqa: E402
from ubblcheck import web  # noqa: E402


@pytest.fixture
def client(tmp_path):
    app = web.create_app(str(tmp_path / "work"))
    app.config["TESTING"] = True
    return app.test_client()


def _file(path, name=None):
    with open(path, "rb") as fh:
        return io.BytesIO(fh.read()), name or os.path.basename(path)


def _rid(resp):
    assert resp.status_code == 302, resp.get_data(as_text=True)[:500]
    return resp.headers["Location"].rstrip("/").rsplit("/", 1)[-1]


def _pdf_form(tmp_path, **kw):
    d = {"project": "Lot 5 office", "state": "Selangor", "purpose_group": "IV", "sub_use": "",
         "top_floor_m": "8", "volume_m3": "12000", "storeys": "2", "floor_area_m2": "800",
         "gross_area_m2": "1600", "rooms": "", "tenth_use": "", "submission": "passive",
         "passive_approval_ref": ""}
    d.update(kw)
    return d


def test_start_page_lists_both_checks(client):
    html = client.get("/").get_data(as_text=True)
    assert 'action="/pdf"' in html and 'action="/ifc"' in html
    assert "Place of assembly" in html and "hospital" in html
    assert "II.library" in html and 'name="gross_area_m2"' in html


def test_drawing_set_end_to_end(client, tmp_path):
    p = drawings.passive_set(tmp_path / "passive.pdf", drop=["EMERGENCY LIGHT"], ramp="1:10")
    data = {**_pdf_form(tmp_path), "passive": [_file(p)]}
    rid = _rid(client.post("/pdf", data=data, content_type="multipart/form-data"))
    html = client.get(f"/runs/{rid}").get_data(as_text=True)
    assert "Lot 5 office" in html and "PDF-RAMP" in html and "PDF-P12" in html and "by-law 253" in html
    assert "Tenth Schedule IV 2" in html
    info = json.loads((tmp_path / "work" / rid / "run.json").read_text(encoding="utf-8"))
    assert info["kind"] == "pdf" and info["uploads"] == ["passive.pdf"] and info["summary"]["error"] >= 2
    rep = client.get(f"/runs/{rid}/files/{info['files']['report']}")
    assert rep.status_code == 200 and rep.data[:4] == b"%PDF"
    j = client.get(f"/runs/{rid}/files/{info['files']['json']}?dl=1")
    assert "attachment" in j.headers["Content-Disposition"]
    assert rid in client.get("/").get_data(as_text=True)       # listed under recent checks


def test_bad_form_values_are_findings_not_crashes(client, tmp_path):
    p = drawings.passive_set(tmp_path / "passive.pdf")
    data = {**_pdf_form(tmp_path, sub_use="hospital", top_floor_m="tall"), "passive": [_file(p)]}
    rid = _rid(client.post("/pdf", data=data, content_type="multipart/form-data"))
    html = client.get(f"/runs/{rid}").get_data(as_text=True)
    assert html.count("PDF-FORM") == 2 and "hospital" in html and "&#39;tall&#39;" in html


def test_missing_or_wrong_uploads_return_to_the_form(client, tmp_path):
    r = client.post("/pdf", data=_pdf_form(tmp_path, project="Kept"), content_type="multipart/form-data")
    assert r.status_code == 400 and "at least one PDF" in r.get_data(as_text=True)
    assert 'value="Kept"' in r.get_data(as_text=True)          # what was typed is not lost
    r = client.post("/pdf", data={**_pdf_form(tmp_path), "passive": [(io.BytesIO(b"x"), "notes.txt")]},
                    content_type="multipart/form-data")
    assert r.status_code == 400 and "not a .pdf" in r.get_data(as_text=True)
    r = client.post("/ifc", data={"model": (io.BytesIO(b"not ifc"), "m.ifc"), "geometry": "on"},
                    content_type="multipart/form-data")
    assert r.status_code == 400 and "not an IFC file" in r.get_data(as_text=True)
    r = client.post("/pdf", data={**_pdf_form(tmp_path), "passive": [(io.BytesIO(b"<html>"), "a.pdf")]},
                    content_type="multipart/form-data")
    assert r.status_code == 400 and "not a PDF file" in r.get_data(as_text=True)
    r = client.post("/ifc", data={"model": (io.BytesIO(b"ISO-10303-21;\nbroken"), "m.ifc")},
                    content_type="multipart/form-data")
    html = r.get_data(as_text=True)
    assert r.status_code == 400 and "could not be checked" in html and str(tmp_path) not in html   # no server paths
    assert not any((tmp_path / "work").iterdir())              # failed runs leave nothing behind


def test_ifc_end_to_end_with_bcf(client, tmp_path):
    m = tmp_path / "office.ifc"
    ifcmodel.office(door_frp=None).write(str(m))
    rid = _rid(client.post("/ifc", data={"model": _file(m), "cell": "0.2"}, content_type="multipart/form-data"))
    html = client.get(f"/runs/{rid}").get_data(as_text=True)
    assert "IFC-FIREDOOR" in html and "BCF" in html
    assert client.get(f"/runs/{rid}/files/office.bcf?dl=1").status_code == 200


def test_ifc_heatmaps_are_shown(client, tmp_path):
    md = G.Model()
    G.box_floor(md, md.storey("L0", 0.0), 60, 20, exits=((0, 2.0),))
    p = tmp_path / "g.ifc"
    md.m.write(str(p))
    data = {"model": _file(p), "geometry": "on", "heatmaps": "on", "cell": "0.25"}
    rid = _rid(client.post("/ifc", data=data, content_type="multipart/form-data"))
    html = client.get(f"/runs/{rid}").get_data(as_text=True)
    assert "GEO-TRAVEL" in html and "heatmaps/travel-L0.png" in html
    assert client.get(f"/runs/{rid}/files/heatmaps/travel-L0.png").data[:4] == b"\x89PNG"


def test_only_run_outputs_are_served(client, tmp_path):
    p = drawings.passive_set(tmp_path / "passive.pdf")
    rid = _rid(client.post("/pdf", data={**_pdf_form(tmp_path), "passive": [_file(p)]},
                           content_type="multipart/form-data"))
    assert client.get("/runs/../../etc").status_code == 404
    assert client.get("/runs/" + "0" * 32).status_code == 404
    assert client.get(f"/runs/{rid}/files/../run.json").status_code == 404
    assert client.get(f"/runs/{rid}/files/../in/passive/passive.pdf").status_code == 404


def test_other_host_names_are_refused(client):
    assert client.get("/", headers={"Host": "evil.example:8765"}).status_code == 400
    assert client.get("/", headers={"Host": "127.0.0.1:8765"}).status_code == 200


def test_posts_from_other_sites_are_refused(client):
    r = client.post("/ifc", headers={"Origin": "https://evil.example"}, data={})
    assert r.status_code == 403
    r = client.post("/ifc", headers={"Origin": "http://localhost"}, data={}, content_type="multipart/form-data")
    assert r.status_code == 400                                 # same origin: on to the form check


def test_pages_cannot_be_framed_or_sniffed(client):
    h = client.get("/").headers
    assert h["X-Frame-Options"] == "DENY" and h["X-Content-Type-Options"] == "nosniff"
    assert "default-src 'self'" in h["Content-Security-Policy"]


def test_one_check_at_a_time(client, tmp_path, monkeypatch):
    import threading
    from ubblcheck import run
    started, release = threading.Event(), threading.Event()
    real = run.ifc

    def slow(*a, **kw):
        started.set()
        release.wait(10)
        return real(*a, **kw)
    monkeypatch.setattr(run, "ifc", slow)
    m = tmp_path / "office.ifc"
    ifcmodel.office().write(str(m))
    first = {}
    th = threading.Thread(target=lambda: first.setdefault("r", client.application.test_client().post(
        "/ifc", data={"model": _file(m)}, content_type="multipart/form-data")))
    th.start()
    assert started.wait(10)
    r = client.post("/ifc", data={"model": _file(m)}, content_type="multipart/form-data")
    release.set()
    th.join(30)
    assert r.status_code == 503 and "Another check is running" in r.get_data(as_text=True)
    assert first["r"].status_code == 302
