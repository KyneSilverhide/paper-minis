"""paper-minis web app: upload character illustrations, generate fold-over paper minis with your own
ComfyUI, and download print sheets (PNG/PDF) with the matching laser-cut SVG."""
import io
import json
import os
import queue
import re
import shutil
import threading
import time
import uuid
import zipfile
from urllib.parse import urlparse

import numpy as np
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from PIL import Image
from pydantic import BaseModel

from . import figure, workflow
from .comfy import Comfy, ComfyError
from .models import MODELS, Downloader, pick

COMFYUI_URL = os.environ.get("COMFYUI_URL", "http://127.0.0.1:8188")
DATA_DIR = os.environ.get("DATA_DIR", "data")
MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", "25"))
FIGURES = os.path.join(DATA_DIR, "figures")
SHEETS = os.path.join(DATA_DIR, "sheets")
SETTINGS = os.path.join(DATA_DIR, "settings.json")
ID = re.compile(r"^[0-9a-f]{32}$")
FRAME_LABEL = {"en": "Frame: {w:.1f} x {h:.1f} mm (check with a ruler)", "fr": "Cadre : {w:.1f} x {h:.1f} mm (vérifier à la règle)"}

os.makedirs(FIGURES, exist_ok=True)
os.makedirs(SHEETS, exist_ok=True)

def settings():
    return json.load(open(SETTINGS)) if os.path.exists(SETTINGS) else {}


def save_settings(s):
    json.dump(s, open(SETTINGS + ".tmp", "w"))
    os.replace(SETTINGS + ".tmp", SETTINGS)


comfy = Comfy(settings().get("comfyui_url", COMFYUI_URL))
downloader = Downloader(os.environ.get("COMFYUI_MODELS_DIR"))
jobs = queue.Queue()
app = FastAPI(title="paper-minis")


def figure_ids():
    """Figures with their metadata written (an upload interrupted halfway leaves a folder without it)."""
    return [f for f in os.listdir(FIGURES) if ID.match(f) and os.path.exists(os.path.join(FIGURES, f, "meta.json"))]


def figure_dir(fid):
    if fid not in figure_ids():
        raise HTTPException(404, "Unknown figure")
    return os.path.join(FIGURES, fid)


def load_meta(fid):
    return json.load(open(os.path.join(FIGURES, fid, "meta.json")))


def save_meta(fid, meta):
    path = os.path.join(FIGURES, fid, "meta.json")
    json.dump(meta, open(path + ".tmp", "w"))
    os.replace(path + ".tmp", path)


def current_models():
    """-> {role: chosen file or None}, from what ComfyUI actually has."""
    saved = settings().get("models", {})
    return {role: pick(role, comfy.models(spec["folder"]), saved.get(role)) for role, spec in MODELS.items()}


def worker():
    while True:
        fid = jobs.get()
        meta = load_meta(fid)
        meta.update(status="generating", error="")
        save_meta(fid, meta)
        try:
            info = comfy.object_info()
            if missing := workflow.missing_nodes(info):
                raise ComfyError("Your ComfyUI is too old for FLUX.2 Klein, update it (missing: " + ", ".join(missing) + ")")
            models = current_models()
            if not all(models.values()):
                raise ComfyError("Choose the models first (top of the page)")
            d = os.path.join(FIGURES, fid)
            image = comfy.upload(open(os.path.join(d, "source.png"), "rb").read(), f"{fid}.png")
            prompt, pose = workflow.build(info, models, image, meta["description"])
            raw = comfy.run(prompt)
            open(os.path.join(d, "raw.png"), "wb").write(raw)
            rgb = np.asarray(Image.open(io.BytesIO(raw)).convert("RGB"))
            figure.figure(rgb).save(os.path.join(d, "figure.png"), dpi=(figure.DPI, figure.DPI))
            meta.update(status="done", pose=pose, updated=time.time())
        except Exception as e:  # shown on the figure card, the user can regenerate
            meta.update(status="error", error=str(e))
        save_meta(fid, meta)


threading.Thread(target=worker, daemon=True).start()
# figures left queued or generating by a previous run start again
for fid in figure_ids():
    if load_meta(fid)["status"] in ("queued", "generating"):
        jobs.put(fid)


@app.get("/api/status")
def status():
    out = {"comfyui_url": comfy.url, "comfyui": False, "error": "", "missing_nodes": [], "models": {},
           "can_download": downloader.models_dir is not None, "in_docker": os.path.exists("/.dockerenv")}
    try:
        comfy.system_stats()
        out["comfyui"] = True
        out["missing_nodes"] = workflow.missing_nodes(comfy.object_info())
        saved = settings().get("models", {})
        for role, spec in MODELS.items():
            available = comfy.models(spec["folder"])
            out["models"][role] = {"available": available, "selected": pick(role, available, saved.get(role)),
                                   "official": spec["file"], "folder": spec["folder"], "page": spec["page"], "url": spec["url"],
                                   "size": spec["size"], "gated": spec["gated"], "download": downloader.state[role]}
    except Exception as e:
        out["error"] = str(e)
    return out


class ComfyAddress(BaseModel):
    url: str


@app.post("/api/comfyui")
def set_comfyui(req: ComfyAddress):
    url = req.url.strip().rstrip("/")
    if urlparse(url).scheme not in ("http", "https") or not urlparse(url).netloc:
        raise HTTPException(400, "Expected an address like http://host.docker.internal:8188")
    comfy.url = url
    s = settings()
    s["comfyui_url"] = url
    save_settings(s)
    return status()


class Selection(BaseModel):
    role: str
    file: str


@app.post("/api/models/select")
def select_model(sel: Selection):
    if sel.role not in MODELS or sel.file not in comfy.models(MODELS[sel.role]["folder"]):
        raise HTTPException(400, "Unknown model")
    s = settings()
    s.setdefault("models", {})[sel.role] = sel.file
    save_settings(s)
    return {"ok": True}


class DownloadRequest(BaseModel):
    roles: list[str]
    token: str = ""


@app.post("/api/models/download")
def download(req: DownloadRequest):
    if downloader.models_dir is None:
        raise HTTPException(400, "ComfyUI's models directory is not mounted in this container")
    downloader.start(req.roles, req.token.strip())
    return {"ok": True}


@app.post("/api/figures")
async def add_figures(files: list[UploadFile] = File(...), descriptions: list[str] = Form(default=[])):
    ids = []
    for i, f in enumerate(files):
        data = await f.read()
        if len(data) > MAX_UPLOAD_MB << 20:
            raise HTTPException(413, f"{f.filename}: larger than {MAX_UPLOAD_MB} MB")
        try:
            img = Image.open(io.BytesIO(data))
            img.load()
        except Exception:
            raise HTTPException(400, f"{f.filename}: not an image")
        fid = uuid.uuid4().hex
        d = os.path.join(FIGURES, fid)
        os.makedirs(d)
        if img.mode in ("RGBA", "LA", "P"):  # transparent sources: composite on white
            img = Image.alpha_composite(Image.new("RGBA", img.size, "white"), img.convert("RGBA"))
        img.convert("RGB").save(os.path.join(d, "source.png"))
        name = re.sub(r"[^\w-]+", "_", os.path.splitext(os.path.basename(f.filename or "figure"))[0])[:60] or "figure"
        save_meta(fid, {"id": fid, "name": name, "description": descriptions[i].strip() if i < len(descriptions) else "",
                        "status": "queued", "error": "", "pose": "", "created": time.time(), "updated": time.time()})
        jobs.put(fid)
        ids.append(fid)
    return {"ids": ids}


@app.get("/api/figures")
def list_figures():
    metas = [load_meta(f) for f in figure_ids()]
    return sorted(metas, key=lambda m: m["created"])


class Regenerate(BaseModel):
    description: str | None = None


@app.post("/api/figures/{fid}/regenerate")
def regenerate(fid: str, req: Regenerate):
    figure_dir(fid)
    meta = load_meta(fid)
    if meta["status"] in ("queued", "generating"):
        raise HTTPException(409, "Already in progress")
    if req.description is not None:
        meta["description"] = req.description.strip()
    meta.update(status="queued", error="")
    save_meta(fid, meta)
    jobs.put(fid)
    return meta


@app.delete("/api/figures/{fid}")
def delete_figure(fid: str):
    d = figure_dir(fid)
    if load_meta(fid)["status"] in ("queued", "generating"):
        raise HTTPException(409, "Wait for the generation to finish")
    shutil.rmtree(d)
    return {"ok": True}


@app.get("/api/figures/{fid}/{kind}.png")
def figure_image(fid: str, kind: str):
    if kind not in ("source", "raw", "figure"):
        raise HTTPException(404)
    path = os.path.join(figure_dir(fid), f"{kind}.png")
    if not os.path.exists(path):
        raise HTTPException(404)
    meta = load_meta(fid)
    return FileResponse(path, filename=f"{meta['name']}_{kind}.png", content_disposition_type="inline")


class SheetRequest(BaseModel):
    ids: list[str]
    height_mm: float = 40.0
    copies: int = 1
    lang: str = "en"


@app.post("/api/sheets")
def make_sheet(req: SheetRequest):
    if not req.ids or not 10 <= req.height_mm <= 250 or not 1 <= req.copies <= 50:
        raise HTTPException(400, "Pick at least one figure, a height of 10-250 mm and 1-50 copies")
    figs, names = [], []
    for fid in req.ids:
        raw = os.path.join(figure_dir(fid), "raw.png")
        if not os.path.exists(raw):
            raise HTTPException(400, "A selected figure is not generated yet")
        figs.append(figure.figure(np.asarray(Image.open(raw).convert("RGB")), req.height_mm))
        names.append(load_meta(fid)["name"])
        if (fit := figure.page_fit(figs[-1])) < 1:
            raise HTTPException(400, f"{names[-1]} does not fit on an A4 sheet at {req.height_mm:g} mm, use {int(req.height_mm * fit)} mm or less")
    sid = uuid.uuid4().hex
    d = os.path.join(SHEETS, sid)
    os.makedirs(d)
    pages = figure.sheets(figs, req.copies, FRAME_LABEL.get(req.lang, FRAME_LABEL["en"]))
    files = []
    for i, (img, svg) in enumerate(pages, 1):
        img.save(os.path.join(d, f"sheet-{i}.png"), dpi=(figure.DPI, figure.DPI))
        open(os.path.join(d, f"sheet-{i}-cut.svg"), "w").write(svg)
        files += [f"sheet-{i}.png", f"sheet-{i}-cut.svg"]
    # one PDF page per sheet, each page exactly the sheet's size: nothing to fit, it prints at true size
    pages[0][0].save(os.path.join(d, "sheets.pdf"), save_all=True, append_images=[p for p, _ in pages[1:]], resolution=figure.DPI)
    for i, (f, name) in enumerate(zip(figs, names), 1):
        f.save(os.path.join(d, f"figure-{i}-{name}.png"), dpi=(figure.DPI, figure.DPI))
    return {"id": sid, "pages": len(pages), "files": ["sheets.pdf"] + files}


@app.get("/api/sheets/{sid}/all.zip")
def sheet_zip(sid: str):
    if not ID.match(sid) or not os.path.isdir(os.path.join(SHEETS, sid)):
        raise HTTPException(404)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for f in sorted(os.listdir(os.path.join(SHEETS, sid))):
            z.write(os.path.join(SHEETS, sid, f), f)
    return Response(buf.getvalue(), media_type="application/zip", headers={"Content-Disposition": 'attachment; filename="paper-minis.zip"'})


@app.get("/api/sheets/{sid}/{name}")
def sheet_file(sid: str, name: str):
    path = os.path.join(SHEETS, sid, name)
    if not ID.match(sid) or os.path.basename(name) != name or name.startswith(".") or not os.path.isfile(path):
        raise HTTPException(404)
    return FileResponse(path, filename=name, content_disposition_type="inline")


app.mount("/", StaticFiles(directory=os.path.join(os.path.dirname(__file__), "static"), html=True), name="static")
