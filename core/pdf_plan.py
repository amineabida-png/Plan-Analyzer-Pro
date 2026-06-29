"""
Analyseur de PLANS PDF multi-pages (locaux commerciaux / projets à niveaux).

Pensé pour des dossiers PDF comme les plans d'exécution retail : plusieurs pages
(cartouche, photos, façades, plan existant, aménagement, lots techniques…), des
cotes en centimètres, et des titres dessinés en courbes (lus par OCR).

Ce qu'il produit, validé sur un vrai dossier :
  - la SURFACE UTILE intérieure du local (région fermée la plus grande) ;
  - le découpage par NIVEAU (RDC, mezzanine, sous-sol…) si plusieurs niveaux ;
  - le recoupement avec la surface ANNONCÉE lue dans le cartouche ;
  - le HSP lu sur le plan.

Méthode clé : l'échelle est CALIBRÉE automatiquement à partir des grandes cotes
du plan (≥ 1 m) — bien plus fiable que de supposer une échelle. La surface est
mesurée sur l'image rastérisée (fermeture morphologique + régions connexes), ce
qui tolère les coupures de murs (portes) que la géométrie vectorielle ne ferme pas.

Les rideaux métalliques de devanture relèvent d'une autre logique (lecture de
façade) : traités à part, en estimation à valider.
"""
from __future__ import annotations
import re
import math
import statistics
import uuid

from .models import (RapportAnalyse, PieceDetectee, MetreLigne, TypeOuvrage,
                     Unite, Point as PtModel)

# Bornes de surface plausibles pour une région "local" (m²)
_SURF_MIN = 5.0
_SURF_MAX = 5000.0
_ZOOM = 3.0  # rastérisation : 216 dpi


def analyser_pdf_plan(chemin: str, echelle: int | None = None) -> RapportAnalyse:
    import fitz

    doc = fitz.open(chemin)
    hsp = _lire_hsp(doc)

    # 1) Lire d'abord la surface annoncée et les cartouches de toutes les pages
    cartouches = [_ocr_cartouche(doc[i]) for i in range(doc.page_count)]
    surface_annoncee = None
    for c in cartouches:
        surface_annoncee = surface_annoncee or _surface_annoncee(c)

    # 2) Analyser chaque page : niveau, propreté (existant), échelle, régions
    pages_info = []
    for i in range(doc.page_count):
        page = doc[i]
        carto = cartouches[i]
        niveau = _niveau_depuis_titre(carto)
        existant = ("existant" in carto.lower())
        facteur = (echelle and _MM * echelle / 1000.0) or _echelle(page)
        regions = _surface_principale(page, facteur) if facteur else []
        surf = _choisir_surface(regions, surface_annoncee)
        pages_info.append({
            "page": i + 1, "niveau": niveau, "existant": existant,
            "facteur": facteur, "surface": surf,
        })

    # 3) Ne garder que les pages de PLAN avec un niveau reconnu et une surface.
    #    Les pages sans niveau (cartouche, façade, photos) sont ignorées ici.
    plans = [p for p in pages_info if p["niveau"] and p["surface"] >= _SURF_MIN]
    if not plans:  # repli : pas de niveau lu -> prendre les pages avec surface
        plans = [p for p in pages_info if p["surface"] >= 20.0]
        for p in plans:
            p["niveau"] = p["niveau"] or "Niveau principal"

    # 4) Par niveau : préférer une page 'existant' (plan propre), puis la surface
    #    la plus proche de l'annoncée.
    niveaux: dict[str, dict] = {}
    for p in plans:
        niv = p["niveau"]
        score = (1 if p["existant"] else 0,
                 -abs(p["surface"] - surface_annoncee) if surface_annoncee
                 else p["surface"])
        cur = niveaux.get(niv)
        if cur is None or score > cur["score"]:
            niveaux[niv] = {"score": score, "surface": p["surface"],
                            "page": p["page"]}

    pieces: list[PieceDetectee] = []
    lignes: list[MetreLigne] = []
    alertes: list[str] = []

    if not niveaux:
        alertes.append("Aucun plan de niveau exploitable détecté dans ce PDF "
                       "(cotes absentes ou plan trop chargé).")
        return _rapport(chemin, pieces, lignes, hsp, alertes)

    total = 0.0
    for niv, info in sorted(niveaux.items()):
        s = round(info["surface"], 2)
        total += s
        pieces.append(PieceDetectee(
            id=str(uuid.uuid4())[:8], nom=f"{niv} — surface utile",
            surface_m2=s, perimetre_m=0.0, surface_plafond_m2=s,
            surface_carrelage_m2=s, surface_peinture_murs_m2=0.0, contour=[]))
        lignes.append(MetreLigne(
            poste=f"Surface utile — {niv}", type_ouvrage=TypeOuvrage.DALLE,
            quantite=s, unite="m2",
            detail=f"page {info['page']}, échelle calibrée sur les cotes"))

    if len(niveaux) > 1:
        lignes.append(MetreLigne(
            poste="Surface utile TOTALE (tous niveaux)",
            type_ouvrage=TypeOuvrage.DALLE, quantite=round(total, 2), unite="m2",
            detail=f"{len(niveaux)} niveaux"))

    # Niveaux détectés / non détectés
    noms = list(niveaux.keys())
    alertes.append("Niveaux détectés : " + ", ".join(noms) + ".")
    for attendu in ("mezzanine", "sous-sol"):
        if not any(attendu in n.lower() for n in noms):
            alertes.append(f"Aucun plan « {attendu} » trouvé dans le PDF.")

    # Recoupement avec la surface annoncée
    if surface_annoncee:
        s_princ = max(info["surface"] for info in niveaux.values())
        ecart = abs(s_princ - surface_annoncee) / surface_annoncee * 100
        alertes.append(
            f"Surface annoncée sur le plan : {surface_annoncee} m² | "
            f"calculée : {round(s_princ,1)} m² (écart {ecart:.0f} %).")

    # Rideau métallique : nécessite la façade, non automatisé de façon fiable
    alertes.append(
        "Rideau métallique de devanture : non calculé automatiquement (nécessite "
        "la lecture de la façade et une validation). À traiter séparément.")

    alertes.append(
        "PDF multi-pages : surface utile mesurée sur le plan le plus propre de "
        "chaque niveau, échelle calibrée sur les cotes. À vérifier d'un coup d'œil.")
    if hsp:
        alertes.insert(0, f"Hauteur sous plafond lue sur le plan : {hsp} m.")

    return _rapport(chemin, pieces, lignes, hsp or 2.70, alertes,
                    hsp_detectee=bool(hsp))


# ---------------------------------------------------------------- helpers
_MM = 25.4 / 72.0  # mm papier par point


def _rapport(chemin, pieces, lignes, hsp, alertes, hsp_detectee=False):
    return RapportAnalyse(
        fichier=chemin, unite_dessin=Unite.INCONNU, facteur_vers_metre=0.0,
        nb_calques=0, calques=[], calques_detail=[], ouvrages=[],
        pieces=pieces, hsp_m=hsp, hsp_detectee=hsp_detectee, mapping_ia={},
        metre=lignes, alertes=alertes)


def _echelle(page, vmin: int = 100) -> float | None:
    """Facteur m/point calibré sur les grandes cotes (≥ vmin cm)."""
    cotes = []
    for w in page.get_text("words"):
        m = re.fullmatch(r"(\d{2,4})", w[4].strip())
        if m:
            v = int(m.group(1))
            if vmin <= v <= 3000:
                cotes.append((v, (w[0] + w[2]) / 2, (w[1] + w[3]) / 2))
    segs = []
    for d in page.get_drawings():
        for it in d.get("items", []):
            if it[0] == "l":
                p1, p2 = it[1], it[2]
                L = math.hypot(p2.x - p1.x, p2.y - p1.y)
                if L > 15:
                    axial = abs(p2.y - p1.y) < 2 or abs(p2.x - p1.x) < 2
                    segs.append((L, (p1.x + p2.x) / 2, (p1.y + p2.y) / 2, axial))
    cands = []
    for (v, cx, cy) in cotes:
        best = None
        for (L, mx, my, axial) in segs:
            if not axial:
                continue
            dd = math.hypot(mx - cx, my - cy)
            if dd < 35 and (best is None or dd < best[1]):
                best = (v / L, dd)
        if best:
            cands.append(best[0])
    if len(cands) < 3:
        return None
    return statistics.median(cands) / 100.0  # cm/point -> m/point


def _surface_principale(page, facteur: float):
    """Régions fermées plausibles (m²) avec leur taux de remplissage du bbox.
    Renvoie une liste [(aire_m2, taux_remplissage)] triée par aire décroissante.
    Le taux de remplissage distingue une pièce pleine (~0.6) d'un anneau/douve
    autour du bâtiment (faible)."""
    import fitz
    import numpy as np
    import cv2

    pix = page.get_pixmap(matrix=fitz.Matrix(_ZOOM, _ZOOM))
    img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
        pix.height, pix.width, pix.n)
    if pix.n == 4:
        img = cv2.cvtColor(img, cv2.COLOR_RGBA2BGR)
    elif pix.n == 1:
        img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    gris = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    h, w = gris.shape
    _, murs = cv2.threshold(gris, 0, 255,
                            cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    k = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
    murs = cv2.morphologyEx(murs, cv2.MORPH_CLOSE, k, iterations=2)
    n, _, stats, _ = cv2.connectedComponentsWithStats(cv2.bitwise_not(murs), 8)
    mpx = facteur / _ZOOM
    regions = []
    for i in range(1, n):
        x, y, bw, bh, aire = stats[i]
        if x <= 1 or y <= 1 or x + bw >= w - 1 or y + bh >= h - 1:
            continue
        a = aire * mpx * mpx
        if _SURF_MIN <= a <= _SURF_MAX:
            remplissage = aire / float(bw * bh) if bw * bh else 0.0
            regions.append((round(a, 2), round(remplissage, 3)))
    regions.sort(key=lambda r: r[0], reverse=True)
    return regions


def _choisir_surface(regions, surface_annoncee):
    """Choisit la surface du local parmi les régions détectées.
    - si une surface est annoncée : la région la plus proche ;
    - sinon : la plus grande région suffisamment 'pleine' (pas un anneau/douve)."""
    if not regions:
        return 0.0
    if surface_annoncee:
        return min(regions, key=lambda r: abs(r[0] - surface_annoncee))[0]
    pleines = [r for r in regions if r[1] >= 0.35]
    return (pleines[0][0] if pleines else regions[0][0])


def _ocr_cartouche(page) -> str:
    """OCR de la bande basse (cartouche) pour lire titre + surface annoncée."""
    try:
        import fitz
        import numpy as np
        import cv2
        import pytesseract
        r = page.rect
        clip = fitz.Rect(0, r.height * 0.86, r.width, r.height)
        pix = page.get_pixmap(matrix=fitz.Matrix(3, 3), clip=clip)
        img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
            pix.height, pix.width, pix.n)
        if pix.n >= 3:
            img = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
        return " ".join(pytesseract.image_to_string(
            img, lang="fra+eng").split())
    except Exception:  # noqa: BLE001
        return ""


def _niveau_depuis_titre(txt: str) -> str | None:
    t = txt.lower()
    if "sous-sol" in t or "sous sol" in t or "ss-sol" in t:
        return "Sous-sol"
    if "mezzanine" in t:
        return "Mezzanine"
    if re.search(r"\br\s*\+\s*\d", t) or "etage" in t or "étage" in t:
        return "Étage"
    if "rdc" in t or "rez" in t or "chaussee" in t or "chaussée" in t:
        return "RDC"
    return None


def _surface_annoncee(txt: str) -> float | None:
    m = re.search(r"(?:superficie|surface)[^0-9]{0,15}(\d{2,5})", txt.lower())
    if m:
        v = float(m.group(1))
        if 5 <= v <= 100000:
            return v
    return None


def _lire_hsp(doc) -> float | None:
    motif = re.compile(r"h\.?s\.?p\.?\s*[=:]?\s*(\d[.,]?\d*)\s*m", re.I)
    for page in doc:
        m = motif.search(page.get_text("text"))
        if m:
            try:
                v = float(m.group(1).replace(",", "."))
                if 2.0 <= v <= 9.0:
                    return v
            except ValueError:
                pass
    return None
