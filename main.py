"""
Plan Analyzer Pro — API FastAPI (socle vectoriel).
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

from fastapi import FastAPI, UploadFile, File, HTTPException, Depends, Form
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy.orm import Session

from core.analyse import analyser_fichier
from core.dwg_converter import conversion_disponible, ConversionDWGError
from core.export_excel import exporter_excel
from core.models import RapportAnalyse
from core.database import init_db, get_session
from core import repository as repo
from core import ai_groq

app = FastAPI(
    title="Plan Analyzer Pro",
    description="Analyse automatique de plans vectoriels et métré (socle DXF).",
    version="0.3.0",
)


@app.on_event("startup")
def _demarrage() -> None:
    """Crée les tables de la base de données au démarrage."""
    try:
        init_db()
    except Exception as e:  # noqa: BLE001
        print(f"[WARN] Base de données non initialisée : {e}")

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
    return HTMLResponse("<h1>Plan Analyzer Pro</h1><p>Interface non trouvée.</p>")


@app.get("/health")
def health() -> dict:
    """Sonde de santé pour Railway / monitoring."""
    return {"status": "ok", "service": "plan-analyzer-pro", "version": "0.3.0"}


@app.get("/api/capabilities")
def capabilities() -> dict:
    """Indique les capacités disponibles (conversion DWG, IA)."""
    return {
        "dxf": True,
        "dwg": conversion_disponible(),
        "ia": ai_groq.ia_disponible(),
        "ia_fournisseur": ai_groq.LLM_PROVIDER,
    }


# ----------------------- Projets & historique -----------------------

class ProjetIn(BaseModel):
    nom: str
    description: str = ""


@app.post("/api/projects")
def creer_projet(data: ProjetIn, db: Session = Depends(get_session)) -> dict:
    """Crée un nouveau projet."""
    p = repo.creer_projet(db, data.nom, data.description)
    return {"id": p.id, "nom": p.nom, "description": p.description}


@app.get("/api/projects")
def lister_projets(db: Session = Depends(get_session)) -> list[dict]:
    """Liste tous les projets avec le nombre d'analyses."""
    out = []
    for p in repo.lister_projets(db):
        out.append({
            "id": p.id, "nom": p.nom, "description": p.description,
            "nb_analyses": len(p.analyses),
            "date_creation": p.date_creation.isoformat(),
        })
    return out


@app.get("/api/projects/{projet_id}/analyses")
def historique(projet_id: int, db: Session = Depends(get_session)) -> list[dict]:
    """Historique versionné des analyses d'un projet."""
    analyses = repo.lister_analyses(db, projet_id)
    return [{
        "id": a.id, "version": a.version, "nom_fichier": a.nom_fichier,
        "nb_pieces": a.nb_pieces, "surface_habitable_m2": a.surface_habitable_m2,
        "date_creation": a.date_creation.isoformat(),
    } for a in analyses]


@app.get("/api/analyses/{analyse_id}")
def obtenir_analyse(analyse_id: int, db: Session = Depends(get_session)) -> dict:
    """Renvoie le rapport complet d'une analyse sauvegardée."""
    a = repo.obtenir_analyse(db, analyse_id)
    if not a:
        raise HTTPException(status_code=404, detail="Analyse introuvable.")
    return {"id": a.id, "version": a.version, "donnees": a.donnees}


@app.get("/api/compare")
def comparer(a: int, b: int, db: Session = Depends(get_session)) -> dict:
    """Compare deux versions d'analyse (écarts de métré poste par poste)."""
    return repo.comparer_analyses(db, a, b)


# ----------------------- IA Groq -----------------------

class ChatIn(BaseModel):
    question: str
    donnees: dict  # rapport d'analyse (RapportAnalyse sérialisé)


@app.post("/api/chat")
def chat(data: ChatIn) -> dict:
    """Répond à une question sur le métré via l'IA Groq."""
    try:
        rapport = RapportAnalyse.model_validate(data.donnees)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"Données invalides : {e}")
    return ai_groq.repondre(data.question, rapport)


@app.post("/api/coherence")
def coherence(data: dict) -> dict:
    """Vérification de cohérence + suggestions de matériaux par l'IA."""
    try:
        rapport = RapportAnalyse.model_validate(data)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"Données invalides : {e}")
    return ai_groq.analyser_coherence(rapport)


def _sauver_temp(fichier: UploadFile) -> str:
    """Écrit l'upload dans un fichier temporaire et renvoie son chemin."""
    nom = (fichier.filename or "").lower()
    if not (nom.endswith(".dxf") or nom.endswith(".dwg")):
        raise HTTPException(
            status_code=400,
            detail="Formats acceptés : .dxf et .dwg.")
    suffix = ".dwg" if nom.endswith(".dwg") else ".dxf"
    fd, chemin = tempfile.mkstemp(suffix=suffix)
    with os.fdopen(fd, "wb") as out:
        out.write(fichier.file.read())
    return chemin


@app.post("/api/analyze")
async def analyser(fichier: UploadFile = File(...),
                   hsp: float | None = None,
                   projet_id: int | None = None,
                   mapping: str | None = Form(None),
                   db: Session = Depends(get_session)) -> JSONResponse:
    """
    Analyse un DXF ou DWG et renvoie le rapport de métré complet en JSON.
    mapping : JSON {nom_calque: categorie} fourni par l'utilisateur (prioritaire).
    Si projet_id est fourni, l'analyse est sauvegardée comme nouvelle version.
    """
    import json as _json
    mapping_manuel = None
    if mapping:
        try:
            mapping_manuel = _json.loads(mapping)
        except Exception:  # noqa: BLE001
            mapping_manuel = None
    chemin = _sauver_temp(fichier)
    try:
        rapport = analyser_fichier(chemin, hsp_m=hsp, mapping_manuel=mapping_manuel)
        rapport.fichier = fichier.filename or rapport.fichier
        resultat = rapport.model_dump()
        if projet_id is not None:
            analyse = repo.sauvegarder_analyse(db, projet_id, rapport)
            resultat["_sauvegarde"] = {
                "analyse_id": analyse.id, "version": analyse.version}
        return JSONResponse(resultat)
    except ConversionDWGError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Erreur d'analyse : {e}")
    finally:
        if os.path.exists(chemin):
            os.remove(chemin)


@app.post("/api/analyze/excel")
async def analyser_excel(fichier: UploadFile = File(...),
                         hsp: float | None = None) -> FileResponse:
    """Analyse un DXF/DWG et renvoie le métré sous forme de fichier Excel."""
    chemin = _sauver_temp(fichier)
    try:
        rapport = analyser_fichier(chemin, hsp_m=hsp)
        sortie = tempfile.mktemp(suffix=".xlsx")
        exporter_excel(rapport, sortie)
        return FileResponse(
            sortie,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            filename="metre_plan_analyzer_pro.xlsx",
        )
    except ConversionDWGError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Erreur d'analyse : {e}")
    finally:
        if os.path.exists(chemin):
            os.remove(chemin)


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)
