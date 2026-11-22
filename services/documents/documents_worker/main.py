"""Documents worker: PDF (scanned ones too), images, DOCX, PPTX, XLSX, HTML → Markdown with Docling.

Internal API, on the compose network only:
  GET  /health      liveness
  GET  /info        the service, its models, VRAM estimate, licenses, whether the models are loaded
  POST /convert     {"path", "out", "ocr", "lang", "tables"} → converts a file in the shared uploads volume
  POST /unload      frees the models (and their VRAM) now
  POST /selftest    known documents → expected text, plus introduced errors (NDJSON)

Docling and its models (layout, TableFormer, RapidOCR) load on first use and unload after
IDLE_UNLOAD_MINUTES without work. The models live in a volume (HF_HOME), never in the image.
"""
import asyncio
import contextlib
import json
import os
import shutil
import tempfile
import time

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

VERSION = "0.1.0"
DOCLING_VERSION = "2.151.1"
HERE = os.path.dirname(os.path.abspath(__file__))
UPLOADS = os.environ.get("UPLOADS_DIR", "/data/uploads")
IDLE_MINUTES = int(os.environ.get("IDLE_UNLOAD_MINUTES", "10"))
OCR_MODES = ("auto", "force", "off")


class ConversionError(RuntimeError):
    pass


class DoclingConverter:
    """Docling behind a small interface, loaded lazily: importing this module needs no Docling."""

    def __init__(self, device="auto"):
        self.device = device
        self._cache = {}  # (ocr, lang, tables) → DocumentConverter
        self.loaded_at = None

    @property
    def loaded(self):
        return bool(self._cache)

    def _converter(self, ocr, lang, tables):
        key = (ocr, lang, tables)
        if key in self._cache:
            return self._cache[key]
        from docling.datamodel.accelerator_options import AcceleratorDevice, AcceleratorOptions
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import OcrMode, PdfPipelineOptions, RapidOcrOptions, TableFormerMode
        from docling.document_converter import DocumentConverter, ImageFormatOption, PdfFormatOption

        opts = PdfPipelineOptions()
        opts.accelerator_options = AcceleratorOptions(device=AcceleratorDevice(self.device))
        opts.do_table_structure = tables
        opts.table_structure_options.mode = TableFormerMode.ACCURATE
        opts.table_structure_options.do_cell_matching = True
        opts.do_ocr = ocr != "off"
        if opts.do_ocr:
            # RapidOCR on its torch backend (torch is already here, on the GPU); one language at a time
            o = RapidOcrOptions(backend="torch", lang=[lang])
            if ocr == "force":
                o.mode = OcrMode.FULL_PAGE
            opts.ocr_options = o
        conv = DocumentConverter(format_options={
            InputFormat.PDF: PdfFormatOption(pipeline_options=opts),
            InputFormat.IMAGE: ImageFormatOption(pipeline_options=opts),
        })
        self._cache[key] = conv
        self.loaded_at = time.time()
        return conv

    def convert(self, src, ocr="auto", lang="en", tables=True):
        """→ (markdown, pages, tables). Raises ConversionError with a message for the user."""
        try:
            doc = self._converter(ocr, lang, tables).convert(src, raises_on_error=True).document
        except Exception as e:  # noqa: BLE001 — a broken document is an error message, not a crash
            raise ConversionError(f"{type(e).__name__}: {e}"[:300]) from None
        md = doc.export_to_markdown()
        return md, len(getattr(doc, "pages", {}) or {}), len(getattr(doc, "tables", []) or [])

    def unload(self):
        if not self._cache:
            return False
        self._cache.clear()
        self.loaded_at = None
        try:
            import gc

            import torch
            gc.collect()
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except ImportError:
            pass
        return True


@contextlib.asynccontextmanager
async def lifespan(app):
    async def idle_unloader():
        while True:
            await asyncio.sleep(30)
            idle = time.time() - app.state.last_used
            if IDLE_MINUTES and idle > IDLE_MINUTES * 60 and not app.state.lock.locked():
                app.state.converter.unload()
    task = asyncio.create_task(idle_unloader())
    yield
    task.cancel()


app = FastAPI(title="local-ai-core documents worker", version=VERSION, lifespan=lifespan)
app.state.converter = DoclingConverter(os.environ.get("DOCLING_DEVICE", "auto"))
app.state.lock = asyncio.Lock()  # one conversion at a time: the gateway queues GPU work anyway
app.state.last_used = time.time()


@app.get("/health")
async def health():
    return {"ok": True}


@app.get("/info")
async def info():
    return {
        "worker": "documents", "version": VERSION, "profile": "docs",
        "services": [{
            "name": "documents", "title": "Documents to Markdown",
            "description": "PDF (scanned ones too), images, DOCX, PPTX, XLSX and HTML become Markdown with "
                           "headings and tables; add them to the library and ask questions about them.",
            "model": f"Docling {DOCLING_VERSION} (layout, TableFormer, RapidOCR)", "ollama": False,
            "vram_gb": 3, "license": "MIT · Apache-2.0 · CDLA-Permissive-2.0",
            "license_url": "https://github.com/docling-project/docling",
            "loaded": app.state.converter.loaded, "endpoints": ["/v1/documents/convert"],
        }],
    }


class ConvertReq(BaseModel):
    path: str = Field(description="file inside the uploads volume")
    out: str = Field(description="where to write the Markdown, inside the uploads volume")
    ocr: str = "auto"
    lang: str = "en"
    tables: bool = True


def inside_uploads(p):
    real = os.path.realpath(p)
    return real == os.path.realpath(UPLOADS) or real.startswith(os.path.realpath(UPLOADS) + os.sep)


async def run_convert(src, dst, ocr, lang, tables):
    if ocr not in OCR_MODES:
        raise HTTPException(422, f"ocr must be one of {OCR_MODES}")
    async with app.state.lock:
        t0 = time.time()
        try:
            md, pages, ntables = await asyncio.to_thread(app.state.converter.convert, src, ocr, lang, tables)
        finally:
            app.state.last_used = time.time()
    if not md.strip():
        hint = "a scan without OCR? try ocr=force" if ocr == "off" else "try ocr=force"
        raise ConversionError(f"no text extracted ({hint})")
    os.makedirs(os.path.dirname(dst) or ".", exist_ok=True)
    with open(dst, "w", encoding="utf-8") as f:
        f.write(md.rstrip() + "\n")
    return {"pages": pages, "tables": ntables, "chars": len(md), "secs": round(time.time() - t0, 1)}


@app.post("/convert")
async def convert(req: ConvertReq):
    if not (inside_uploads(req.path) and inside_uploads(req.out)):
        raise HTTPException(400, "paths must be inside the uploads volume")
    if not os.path.isfile(req.path):
        raise HTTPException(404, "no such file")
    try:
        return await run_convert(req.path, req.out, req.ocr, req.lang, req.tables)
    except ConversionError as e:
        raise HTTPException(422, str(e)) from None


@app.post("/unload")
async def unload():
    async with app.state.lock:
        return {"unloaded": app.state.converter.unload()}


# ------------------------------------------------------------------------------------------ self-test

def missing(text, words):
    return [w for w in words if w not in text]


NATIVE_EXPECT = ["# Electricity bill", "| Energy", "47.60", "71.25", "direct debit", "Meter reading"]
SCAN_EXPECT = ["Energy", "47.60", "71.25", "reading", "invoice"]


async def selftest_checks():
    """An invented electricity bill (selftest/invoice.pdf, with real text) and the same page printed
    crooked and noisy (invoice-scan.pdf, image only). Introduced errors: the scan without OCR must not
    "read" anything, a broken PDF must be an error, and a figure changed in the expected text must make
    the comparison fail."""
    tmp = tempfile.mkdtemp(prefix="selftest-", dir=UPLOADS if os.path.isdir(UPLOADS) else None)
    fx = os.path.join(HERE, "selftest")

    def check(name, ok, detail):
        return {"type": "check", "name": name, "ok": bool(ok), "detail": detail}

    try:
        out = os.path.join(tmp, "native.md")
        try:
            r = await run_convert(os.path.join(fx, "invoice.pdf"), out, "auto", "en", True)
            text = open(out, encoding="utf-8").read()
            miss = missing(text, NATIVE_EXPECT)
            yield check("PDF with text: title, table, amounts", not miss and r["tables"] >= 1,
                        f"{r['tables']} table(s), {r['secs']} s" + (f"; missing {miss}" if miss else ""))
            altered = [w.replace("71.25", "71.52") for w in NATIVE_EXPECT]
            yield check("introduced error: a changed figure fails the comparison", missing(text, altered) == ["71.52"],
                        "expected 71.52 instead of 71.25: reported as missing")
        except ConversionError as e:
            yield check("PDF with text: title, table, amounts", False, str(e))

        out = os.path.join(tmp, "scan.md")
        try:
            r = await run_convert(os.path.join(fx, "invoice-scan.pdf"), out, "auto", "en", True)
            text = open(out, encoding="utf-8").read()
            miss = missing(text, SCAN_EXPECT)
            yield check("scanned PDF read with OCR", not miss and r["tables"] >= 1,
                        f"{r['tables']} table(s), {r['secs']} s" + (f"; missing {miss}" if miss else ""))
        except ConversionError as e:
            yield check("scanned PDF read with OCR", False, str(e))

        try:
            await run_convert(os.path.join(fx, "invoice-scan.pdf"), os.path.join(tmp, "off.md"), "off", "en", True)
            yield check("introduced error: a scan without OCR", False, "text was 'read' from a scan without OCR")
        except ConversionError as e:
            yield check("introduced error: a scan without OCR", "no text" in str(e), f"refused: {e}")

        broken = os.path.join(tmp, "broken.pdf")
        with open(broken, "w") as f:
            f.write("%PDF-1.4\nthis is not a pdf\n")
        try:
            await run_convert(broken, os.path.join(tmp, "broken.md"), "auto", "en", True)
            yield check("introduced error: a broken PDF", False, "a broken PDF was converted")
        except ConversionError as e:
            yield check("introduced error: a broken PDF", True, f"error message: {str(e)[:100]}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@app.post("/selftest")
async def selftest():
    async def body():
        ok = True
        async for c in selftest_checks():
            ok = ok and c["ok"]
            yield json.dumps(c, ensure_ascii=False) + "\n"
        yield json.dumps({"type": "result", "ok": ok}) + "\n"
    return StreamingResponse(body(), media_type="application/x-ndjson")
