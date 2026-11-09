import json

import pypdf

import drawings
import ifcmodel
from ubblcheck import cli


def test_pdf_command_writes_json_and_report(tmp_path, capsys):
    p = drawings.passive_set(tmp_path / "p.pdf", drop=["EMERGENCY LIGHT"], ramp="1:10")
    form = drawings.form(tmp_path, p, None, state="Selangor", project="Lot 5 office")
    rc = cli.main(["pdf", form, "-o", str(tmp_path / "out")])
    assert rc == 1
    j = json.loads((tmp_path / "out" / "project.json").read_text(encoding="utf-8"))
    assert {f["rule"] for f in j["findings"]} >= {"PDF-P12", "PDF-RAMP"}
    text = "".join(pg.extract_text() for pg in pypdf.PdfReader(str(tmp_path / "out" / "project-report.pdf")).pages)
    assert "Lot 5 office" in text and "PDF-RAMP" in text and "by-law 253" in text


def test_ifc_command_writes_bcf(tmp_path):
    m = tmp_path / "m.ifc"
    ifcmodel.office(door_frp=None).write(str(m))
    assert cli.main(["ifc", str(m), "-o", str(tmp_path / "o")]) == 1
    assert (tmp_path / "o" / "m.bcf").exists() and (tmp_path / "o" / "m-report.pdf").exists()


def test_clean_model_exit_code(tmp_path):
    m = tmp_path / "m.ifc"
    ifcmodel.office().write(str(m))
    assert cli.main(["ifc", str(m), "-o", str(tmp_path / "o")]) == 0


def test_form_template(tmp_path):
    f = tmp_path / "project.yaml"
    assert cli.main(["form", str(f)]) == 0
    assert "purpose_group" in f.read_text()
    assert cli.main(["form", str(f)]) == 2

