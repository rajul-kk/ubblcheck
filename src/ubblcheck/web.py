"""A local web page for both checks: `ubblcheck serve`, then open http://127.0.0.1:8765.

Upload an IFC model, or fill in the project form and add the passive and active PDF sets.
Each check runs in its own folder under the work directory:

    <work>/<run id>/in/    the uploaded files
    <work>/<run id>/out/   JSON, PDF report, BCF, heatmaps (what `ubblcheck ifc|pdf -o` writes)
    <work>/<run id>/run.json   what was run, when, and which output file is which

The page only listens on 127.0.0.1 by default: drawings are confidential, and nothing here
authenticates anyone.  Needs Flask (`pip install -e .[web]`).
"""
from __future__ import annotations

import datetime
import json
import os
import logging
import re
import shutil
import sys
import tempfile
import threading
import time
import uuid
from urllib.parse import urlsplit

from flask import Flask, abort, redirect, render_template, request, send_from_directory, url_for
from werkzeug.utils import secure_filename

from . import pdfcheck, run, ubbl
from . import findings as fnd

RUN_ID = re.compile(r"^[0-9a-f]{32}$")
MAX_UPLOAD_MB = 512
# What each upload must start with: the extension alone says nothing about the content.
MAGIC = {".ifc": b"ISO-10303-21", ".pdf": b"%PDF-"}
# Headers on every response: no framing (clickjacking), no type sniffing, own scripts only.
HEADERS = {"X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY", "Referrer-Policy": "no-referrer",
           "Content-Security-Policy": "default-src 'self'; img-src 'self' data:; frame-ancestors 'none'"}

log = logging.getLogger("ubblcheck.web")


class InputError(ValueError):
    """Something wrong with what was uploaded or typed; shown on the form page, not as a crash."""


LOCAL_HOSTS = ["127.0.0.1", "localhost", "[::1]"]     # as Host headers write them


def create_app(work_dir: str | None = None, trusted_hosts: list[str] | None = LOCAL_HOSTS) -> Flask:
    """trusted_hosts: Host names the page answers to.  Limiting them to loopback stops another
    web site from reaching this one through DNS rebinding; None accepts any host."""
    here = os.path.dirname(__file__)
    app = Flask(__name__, template_folder=os.path.join(here, "webui", "templates"),
                static_folder=os.path.join(here, "webui", "static"))
    app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_MB * 1024 * 1024
    app.config["TRUSTED_HOSTS"] = trusted_hosts
    app.config["WORK_DIR"] = os.path.abspath(work_dir or tempfile.mkdtemp(prefix="ubblcheck-"))
    os.makedirs(app.config["WORK_DIR"], exist_ok=True)
    # One check at a time: a large model takes GBs of RAM and every core, so a second one at once
    # could run the machine out of memory.  The second is told to retry, not queued.
    busy = threading.Lock()

    @app.before_request
    def same_origin_posts():
        """A page on another site can post a form here (the browser sends it to 127.0.0.1 as is);
        a post whose Origin is not this page is refused."""
        origin = request.headers.get("Origin")
        if request.method == "POST" and origin and urlsplit(origin).netloc != request.host:
            log.warning("refused a post from %s", origin)
            abort(403)

    @app.after_request
    def security_headers(resp):
        for k, v in HEADERS.items():
            resp.headers.setdefault(k, v)
        return resp

    def run_dir(rid: str) -> str:
        if not RUN_ID.match(rid):
            abort(404)
        d = os.path.join(app.config["WORK_DIR"], rid)
        if not os.path.isfile(os.path.join(d, "run.json")):
            abort(404)
        return d

    def new_run() -> tuple[str, str]:
        rid = uuid.uuid4().hex
        d = os.path.join(app.config["WORK_DIR"], rid)
        os.makedirs(os.path.join(d, "in"))
        return rid, d

    def page(error=None, form=None, status=200):
        return render_template("index.html", error=error, form=form or {}, groups=ubbl.PURPOSE_GROUPS,
                               sub_uses=_sub_uses(), tenth_uses=ubbl.tenth_uses(),
                               submissions=pdfcheck.SUBMISSIONS,
                               recent=_recent(app.config["WORK_DIR"])), status

    @app.get("/")
    def index():
        return page()

    def start(work, fields=None):
        """Run work(d) -> (Run, upload names) in a new run folder; on bad input, back to the form
        with the message and what was typed, and nothing left behind."""
        if not busy.acquire(blocking=False):
            return page("Another check is running; try again when it has finished.", fields, 503)
        rid, d = new_run()
        t0 = time.perf_counter()
        try:
            r, names = work(d)
        except InputError as e:
            shutil.rmtree(d, ignore_errors=True)
            log.info("run %s refused in %.1f s: %s", rid, time.perf_counter() - t0, e)
            return page(str(e), fields, 400)
        except Exception:
            shutil.rmtree(d, ignore_errors=True)
            raise
        finally:
            busy.release()
        _record(d, r, names)
        log.info("run %s: %s %s, %s in %.1f s", rid, r.kind, ", ".join(names), r.counts,
                 time.perf_counter() - t0)
        return redirect(url_for("result", rid=rid))

    @app.post("/ifc")
    def check_ifc():
        up = request.files.get("model")
        geometry = request.form.get("geometry") == "on"
        heatmaps = geometry and request.form.get("heatmaps") == "on"

        def work(d):
            if up is None or not up.filename:
                raise InputError("Choose an IFC model to check.")
            cell = _number(request.form.get("cell"), 0.2, 0.05, 1.0,
                           "Grid cell must be a number of metres between 0.05 and 1.")
            path = _save(up, os.path.join(d, "in"), ".ifc")
            try:
                r = run.ifc(path, os.path.join(d, "out"), geometry=geometry, heatmaps=heatmaps, cell=cell)
            except Exception as exc:  # noqa: BLE001  an unreadable model is bad input, not our crash
                log.exception("IFC check of %s failed", up.filename)     # the detail stays in the log:
                raise InputError(f"{up.filename} could not be checked ({type(exc).__name__}); "  # it names
                                 "the server log has the details.") from exc                   # server paths
            return r, [up.filename]

        return start(work)

    @app.post("/pdf")
    def check_pdf():
        fields = {k: request.form.get(k, "") for k in ("project", "state", "purpose_group", "sub_use",
                                                       "top_floor_m", "volume_m3", "storeys", "floor_area_m2",
                                                       "gross_area_m2", "rooms", "tenth_use", "submission",
                                                       "passive_approval_ref")}
        fields["sprinklered"] = request.form.get("sprinklered") == "on"

        def work(d):
            sets, names = {}, []
            for st in pdfcheck.SETS:
                for up in request.files.getlist(st):
                    if not up or not up.filename:
                        continue
                    sets.setdefault(st, []).append(_save(up, os.path.join(d, "in", st), ".pdf"))
                    names.append(up.filename)
            if not sets:
                raise InputError("Add at least one PDF drawing set.")
            try:
                r = run.pdf(pdfcheck.Form.from_dict({**fields, "sets": sets}), os.path.join(d, "out"))
            except Exception as exc:  # noqa: BLE001
                log.exception("drawing-set check failed")
                raise InputError(f"The drawings could not be read ({type(exc).__name__}); "
                                 "the server log has the details.") from exc
            return r, names

        return start(work, fields)

    @app.get("/runs/<rid>")
    def result(rid):
        d = run_dir(rid)
        info = _load(os.path.join(d, "run.json"))
        doc = _load(os.path.join(d, "out", info["files"]["json"]))
        fs = sorted(doc["findings"], key=lambda f: fnd.order(f["severity"], f["rule"]))
        return render_template("result.html", rid=rid, info=info, meta=doc["meta"], summary=doc["summary"],
                               findings=fs, severities=fnd.SEVERITIES)

    @app.get("/runs/<rid>/files/<path:name>")
    def output(rid, name):
        d = run_dir(rid)
        return send_from_directory(os.path.join(d, "out"), name, as_attachment=request.args.get("dl") == "1")

    @app.errorhandler(413)
    def too_big(_):
        return page(f"Upload is larger than {MAX_UPLOAD_MB} MB.", status=413)

    return app


def _number(raw, default: float, lo: float, hi: float, message: str) -> float:
    """A number typed into the form, `default` when blank; InputError unless lo <= it <= hi."""
    try:
        v = float(raw) if raw not in (None, "") else default
    except ValueError:
        raise InputError(message) from None
    if not lo <= v <= hi:
        raise InputError(message)
    return v


def _save(up, folder: str, ext: str) -> str:
    """Saves an upload of type `ext` (a MAGIC key) under a safe name; InputError when its name or
    its content is not of that type."""
    kind = ext[1:].upper()
    if not up.filename.lower().endswith(ext):
        raise InputError(f"{up.filename} is not a{'n' if kind[0] in 'AEIOU' else ''} {ext} file.")
    head = up.stream.read(1024).lstrip(b"\xef\xbb\xbf \t\r\n")
    up.stream.seek(0)
    if not head.startswith(MAGIC[ext]):
        raise InputError(f"{up.filename} is not a{'n' if kind[0] in 'AEIOU' else ''} {kind} file: "
                         f"it does not start with {MAGIC[ext].decode()}.")
    os.makedirs(folder, exist_ok=True)
    name = secure_filename(up.filename) or "upload"
    path = os.path.join(folder, name)
    stem, ext = os.path.splitext(path)
    n = 1
    while os.path.exists(path):        # two uploads with the same name keep both
        path = f"{stem}-{n}{ext}"
        n += 1
    up.save(path)
    return path


def _record(d: str, r: run.Run, uploads: list[str]) -> None:
    out = os.path.join(d, "out")
    info = {"kind": r.kind, "stem": r.stem, "uploads": uploads, "summary": r.summary,
            "created": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
            "files": {role: os.path.relpath(p, out).replace(os.sep, "/") for role, p in r.files.items()},
            "heatmaps": [{"path": os.path.relpath(p, out).replace(os.sep, "/"), "storey": storey}
                         for storey, p in r.heatmaps.items()]}
    with open(os.path.join(d, "run.json"), "w", encoding="utf-8") as fh:
        json.dump(info, fh, indent=1)


def _load(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _recent(work: str, n: int = 8) -> list[dict]:
    runs = []
    for rid in os.listdir(work):
        p = os.path.join(work, rid, "run.json")
        if RUN_ID.match(rid) and os.path.isfile(p):
            runs.append({"rid": rid, "mtime": os.path.getmtime(p), **_load(p)})
    return sorted(runs, key=lambda r: r["mtime"], reverse=True)[:n]


def _sub_uses() -> dict[str, list[str]]:
    """group -> Seventh Schedule sub-uses, for the form's sub-use list."""
    out: dict[str, list[str]] = {}
    for key in ubbl.TRAVEL:
        if "." in key:
            g, use = key.split(".", 1)
            out.setdefault(g, []).append(use)
    return out


def serve(host: str = "127.0.0.1", port: int = 8765, work_dir: str | None = None) -> None:
    local = host in ("::1", *LOCAL_HOSTS)
    if not local:
        print(f"ubblcheck: listening on {host}: anyone who can reach it can upload and read results", file=sys.stderr)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    app = create_app(work_dir, LOCAL_HOSTS if local else None)
    print(f"ubblcheck: http://{host}:{port}  (results in {app.config['WORK_DIR']}; Ctrl+C to stop)")
    app.run(host=host, port=port, debug=False)
