"""
BTP QUANT AI — API FastAPI (socle vectoriel).
Expose le moteur d'analyse DXF via une API REST et une interface web simple.

Endpoints :
  GET  /                  -> interface web d'upload
  GET  /health            -> sonde de santé (utile pour Railway)
  POST /api/analyze       -> analyse un DXF, renvoie le métré en JSON
  POST /api/analyze/excel -> analyse un DXF, renvoie le métré en fichier Excel
"""
from __future__ import annotations
import os
import tempfile

from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from core.analyse import analyser_dxf
from core.export_excel import exporter_excel

app = FastAPI(
    title="BTP QUANT AI",
    description="Analyse automatique de plans vectoriels et métré (socle DXF).",
    version="0.1.0",
)

# Sert les fichiers statiques (interface web)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
if os.path.isdir(STATIC_DIR):
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", response_class=HTMLResponse)
def accueil() -> HTMLResponse:
    """Page d'accueil : formulaire d'upload."""
    index = os.path.join(STATIC_DIR, "index.html")
    if os.path.isfile(index):
        with open(index, encoding="utf-8") as f:
            return HTMLResponse(f.read())
    return HTMLResponse("<h1>BTP QUANT AI</h1><p>Interface non trouvée.</p>")


@app.get("/health")
def health() -> dict:
    """Sonde de santé pour Railway / monitoring."""
    return {"status": "ok", "service": "btp-quant-ai", "version": "0.1.0"}


def _sauver_temp(fichier: UploadFile) -> str:
    """Écrit l'upload dans un fichier temporaire et renvoie son chemin."""
    if not fichier.filename or not fichier.filename.lower().endswith(".dxf"):
        raise HTTPException(status_code=400,
                            detail="Seuls les fichiers .dxf sont acceptés pour l'instant.")
    suffix = ".dxf"
    fd, chemin = tempfile.mkstemp(suffix=suffix)
    with os.fdopen(fd, "wb") as out:
        out.write(fichier.file.read())
    return chemin


@app.post("/api/analyze")
async def analyser(fichier: UploadFile = File(...),
                   hsp: float = 2.70) -> JSONResponse:
    """Analyse un DXF et renvoie le rapport de métré complet en JSON."""
    chemin = _sauver_temp(fichier)
    try:
        rapport = analyser_dxf(chemin, hsp_m=hsp)
        return JSONResponse(rapport.model_dump())
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Erreur d'analyse : {e}")
    finally:
        if os.path.exists(chemin):
            os.remove(chemin)


@app.post("/api/analyze/excel")
async def analyser_excel(fichier: UploadFile = File(...),
                         hsp: float = 2.70) -> FileResponse:
    """Analyse un DXF et renvoie le métré sous forme de fichier Excel."""
    chemin = _sauver_temp(fichier)
    try:
        rapport = analyser_dxf(chemin, hsp_m=hsp)
        sortie = tempfile.mktemp(suffix=".xlsx")
        exporter_excel(rapport, sortie)
        return FileResponse(
            sortie,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            filename="metre_btp_quant_ai.xlsx",
        )
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Erreur d'analyse : {e}")
    finally:
        if os.path.exists(chemin):
            os.remove(chemin)


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)
