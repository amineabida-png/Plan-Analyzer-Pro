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


def _cotes_facade(doc, cartouches: list[str]) -> list[float]:
    """Lit les cotes RÉELLES écrites sur la/les page(s) de façade (en cm) et les
    renvoie en mètres, triées. Ce sont des valeurs exactes (pas une estimation) :
    l'utilisateur s'en sert pour composer la largeur et la hauteur du rideau."""
    vals = set()
    for i in range(doc.page_count):
        c = cartouches[i].lower()
        if "facade" not in c and "façade" not in c:
            continue
        for w in doc[i].get_text("words"):
            m = re.fullmatch(r"(\d{2,4})", w[4].strip())
            if m:
                v = int(m.group(1))
                if 20 <= v <= 2000:  # 0.2 m à 20 m
                    vals.add(v)
    return sorted(round(v / 100.0, 2) for v in vals)


def _cotes_bas(page, vmin=10):
    """Chaîne de cotes horizontales du bas de la façade (valeur en cm, x), triée."""
    cotes = []
    for w in page.get_text("words"):
        m = re.fullmatch(r"(\d{2,4})", w[4].strip())
        if m:
            v = int(m.group(1))
            if vmin <= v <= 2000:
                cotes.append((v, (w[0] + w[2]) / 2, (w[1] + w[3]) / 2))
    if not cotes:
        return []
    ymax = max(c[2] for c in cotes)
    bas = [c for c in cotes if c[2] > ymax - 18]
    bas.sort(key=lambda c: c[1])
    return bas  # [(valeur_cm, x, y), ...] gauche -> droite


def _rideaux_supeco(doc, cartouches: list[str]) -> list[dict]:
    """Détecte automatiquement les rideaux de devanture SUPECO via le CADRE JAUNE.
    Robuste : tolérance de teinte, choix de la bonne page façade, correction des
    murs latéraux par la chaîne de cotes du bas. Ne lève jamais d'exception."""
    try:
        return _rideaux_supeco_impl(doc, cartouches)
    except Exception:  # noqa: BLE001
        return []


def _est_jaune(fl) -> bool:
    """Jaune SUPECO, avec tolérance de teinte (golden yellow)."""
    if not fl or len(fl) < 3:
        return False
    r, g, b = fl[0], fl[1], fl[2]
    return r > 0.7 and g > 0.55 and b < 0.55 and (r - b) > 0.35


def _rideaux_supeco_impl(doc, cartouches):
    for i in range(doc.page_count):
        c = cartouches[i].lower()
        if "facade" not in c and "façade" not in c:
            continue
        page = doc[i]
        f = _echelle(page, vmin=40)
        if not f:
            continue
        # Rectangles remplis en jaune (cadre de la devanture)
        jr = []
        for d in page.get_drawings():
            r = d.get("rect")
            if _est_jaune(d.get("fill")) and r and (r.x1 - r.x0) > 1 and (r.y1 - r.y0) > 1:
                jr.append(r)
        if len(jr) < 2:
            continue
        minx = min(r.x0 for r in jr); maxx = max(r.x1 for r in jr)
        miny = min(r.y0 for r in jr); maxy = max(r.y1 for r in jr)
        Wtot = (maxx - minx) * f
        Htot = (maxy - miny) * f
        # Garde-fou : une devanture plausible (1–30 m de large, 1–6 m de haut)
        if not (1.0 <= Wtot <= 30.0 and 1.0 <= Htot <= 6.0):
            continue
        # Bandeau = rectangle jaune le plus large (pleine largeur, en haut)
        bandeau = max(jr, key=lambda r: r.x1 - r.x0)
        h_band = (bandeau.y1 - bandeau.y0) * f
        # Poteaux = rectangles jaunes 'hauts' (hors bandeau)
        poteaux = sorted([r for r in jr if r is not bandeau
                          and (r.y1 - r.y0) * f > 1.0], key=lambda r: r.x0)
        # Ouvertures = espaces libres entre poteaux, dans l'emprise
        occ = [(r.x0, r.x1) for r in poteaux]
        ouvertures = []
        cur = minx
        for (a, b) in occ:
            if a - cur > 5:
                ouvertures.append([cur, a])
            cur = max(cur, b)
        if maxx - cur > 5:
            ouvertures.append([cur, maxx])
        if not ouvertures:
            continue
        # Correction des murs latéraux par la chaîne de cotes du bas :
        # la cote la plus à gauche / à droite, si petite (< 0.7 m), est un mur.
        bas = _cotes_bas(page)
        mur_g = mur_d = 0.0
        if bas:
            vg = bas[0][0] / 100.0
            vd = bas[-1][0] / 100.0
            if vg < 0.7:
                mur_g = vg
            if vd < 0.7:
                mur_d = vd
        tol = 8  # points
        for ouv in ouvertures:
            if abs(ouv[0] - minx) < tol and mur_g:   # touche le bord gauche
                ouv[0] += mur_g / f
            if abs(ouv[1] - maxx) < tol and mur_d:    # touche le bord droit
                ouv[1] -= mur_d / f
        # Hauteur d'ouverture = devanture - bandeau - base (~0.20 m typique)
        h_ouv = round(Htot - h_band - 0.20, 2)
        if h_ouv <= 0.5:
            h_ouv = round(Htot - h_band, 2)
        out = []
        for (a, b) in ouvertures:
            larg = round((b - a) * f, 2)
            if larg >= 0.8:  # ignore les petits interstices
                out.append({"largeur_m": larg, "hauteur_m": h_ouv,
                            "surface_m2": round(larg * h_ouv, 2)})
        if out:
            return out
    return []


def _projet(doc) -> str | None:
    """Nom EXACT du projet/magasin lu sur le cartouche (case PROJET, bas-droite).
    OCR ciblé sur chaque page ; on retient le nom complet le plus FRÉQUENT
    (les lectures correctes se répètent, le bruit OCR est unique)."""
    from collections import Counter
    try:
        import fitz
        import numpy as np
        import cv2
        import pytesseract
    except Exception:  # noqa: BLE001
        return None
    cands = []
    for i in range(doc.page_count):
        try:
            page = doc[i]
            r = page.rect
            clip = fitz.Rect(r.width * 0.70, r.height * 0.90, r.width, r.height)
            pix = page.get_pixmap(matrix=fitz.Matrix(4, 4), clip=clip)
            img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
                pix.height, pix.width, pix.n)
            if pix.n >= 3:
                img = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
            t = " ".join(pytesseract.image_to_string(img, lang="fra+eng").split())
        except Exception:  # noqa: BLE001
            continue
        m = re.search(r"projet\s*:?\s*(.+)", t, re.I)
        if not m:
            continue
        rest = re.split(r"\b(date|nbr|gondoles?|superficie|surface|plan|supeco)\b",
                        m.group(1), flags=re.I)[0]
        nom = re.sub(r"[^A-Za-zÀ-ÿ0-9\- ]", " ", rest)
        nom = re.sub(r"\s+", " ", nom).strip().upper()
        if 2 < len(nom) < 40:
            cands.append(nom)
            # Arrêt anticipé : 3 lectures d'un nom complet suffisent au vote
            if len([n for n in cands if len(n.split()) > 1]) >= 3:
                break
    if not cands:
        return None
    multi = [n for n in cands if len(n.split()) > 1]
    if multi:  # nom complet le plus fréquent
        return Counter(multi).most_common(1)[0][0]
    return Counter(cands).most_common(1)[0][0]


def _gondoles(cartouches: list[str]) -> int | None:
    """Nombre de gondoles lu sur le cartouche (valeur la plus fréquente)."""
    from collections import Counter
    vals = []
    for c in cartouches:
        m = re.search(r"gondoles?\s*:?\s*(\d{1,4})", c, re.I)
        if m:
            vals.append(int(m.group(1)))
    return Counter(vals).most_common(1)[0][0] if vals else None


def analyser_pdf_plan(chemin: str, echelle: int | None = None) -> RapportAnalyse:
    """Analyse un dossier PDF de plan (surfaces, niveaux, rideaux SUPECO).
    Sécurisé : toute erreur imprévue renvoie un rapport lisible, jamais un plantage."""
    try:
        return _analyser_pdf_plan_impl(chemin, echelle)
    except Exception as e:  # noqa: BLE001
        return _rapport(
            chemin, [], [], 2.70,
            [f"Analyse incomplète ({type(e).__name__}). Le PDF est peut-être "
             "inhabituel : réessayez, ou envoyez ce plan pour diagnostic."])


def _analyser_pdf_plan_impl(chemin: str, echelle: int | None = None) -> RapportAnalyse:
    import fitz

    try:
        doc = fitz.open(chemin)
    except Exception:  # noqa: BLE001
        return _rapport(chemin, [], [], 2.70,
                        ["PDF illisible : fichier corrompu ou protégé."])
    hsp = _lire_hsp(doc)

    # 1) Lire d'abord la surface annoncée et les cartouches de toutes les pages
    cartouches = []
    for i in range(doc.page_count):
        try:
            cartouches.append(_ocr_cartouche(doc[i]))
        except Exception:  # noqa: BLE001
            cartouches.append("")
    surface_annoncee = None
    for c in cartouches:
        surface_annoncee = surface_annoncee or _surface_annoncee(c)

    # 2) Analyser chaque page : niveau, propreté (existant), échelle, régions.
    #    Une page corrompue est ignorée sans interrompre l'analyse.
    pages_info = []
    for i in range(doc.page_count):
        try:
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
        except Exception:  # noqa: BLE001
            continue

    # 3) Niveaux PRÉSENTS d'après les titres (indépendant de toute mesure).
    ordre = ["Sous-sol", "RDC", "Mezzanine", "Étage", "Niveau principal"]
    niveaux_presents = []
    for p in pages_info:
        if p["niveau"] and p["niveau"] not in niveaux_presents:
            niveaux_presents.append(p["niveau"])

    # Pages exploitables pour la MESURE de surface (échelle + région trouvées)
    plans = [p for p in pages_info if p["niveau"] and p["surface"] >= _SURF_MIN]
    if not plans:  # repli : pas de niveau lu mais une surface -> niveau principal
        plans = [p for p in pages_info if p["surface"] >= 20.0]
        for p in plans:
            p["niveau"] = p["niveau"] or "Niveau principal"
            if p["niveau"] not in niveaux_presents:
                niveaux_presents.append(p["niveau"])

    # 4) Surface mesurée par niveau : meilleure page (existant + proche annoncée)
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

    # Liste ordonnée de tous les niveaux à afficher (présents, mesurés ou non)
    tous_niveaux = sorted(set(niveaux_presents) | set(niveaux.keys()),
                          key=lambda n: ordre.index(n) if n in ordre else 99)

    pieces: list[PieceDetectee] = []
    lignes: list[MetreLigne] = []
    alertes: list[str] = []

    if not tous_niveaux:
        alertes.append("Aucun plan de niveau détecté. Le reste (rideaux, projet) "
                       "est fourni ci-dessous s'il est disponible.")

    total = 0.0
    for niv in tous_niveaux:
        info = niveaux.get(niv)
        if info:  # niveau MESURÉ
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
        else:  # niveau PRÉSENT mais non mesurable (pas de cotes exploitables)
            pieces.append(PieceDetectee(
                id=str(uuid.uuid4())[:8], nom=f"{niv} — surface utile",
                surface_m2=0.0, perimetre_m=0.0, surface_plafond_m2=0.0,
                surface_carrelage_m2=0.0, surface_peinture_murs_m2=0.0, contour=[]))

    mesures = [n for n in tous_niveaux if n in niveaux]
    if len(mesures) > 1:
        lignes.append(MetreLigne(
            poste="Surface utile TOTALE (tous niveaux)",
            type_ouvrage=TypeOuvrage.DALLE, quantite=round(total, 2), unite="m2",
            detail=f"{len(mesures)} niveaux mesurés"))

    # Niveaux présents / manquants
    if tous_niveaux:
        alertes.append("Niveaux présents : " + ", ".join(tous_niveaux) + ".")
        non_mesures = [n for n in tous_niveaux if n not in niveaux]
        if non_mesures:
            alertes.append("Surface non mesurée pour : " + ", ".join(non_mesures)
                           + " (plan sans cotes exploitables). "
                           "Voir la surface annoncée ci-dessous.")
        for attendu in ("mezzanine", "sous-sol"):
            if not any(attendu in n.lower() for n in tous_niveaux):
                alertes.append(f"Aucun plan « {attendu} » trouvé dans le PDF.")

    # Surface annoncée sur le plan (toujours reportée si lue)
    if surface_annoncee:
        if niveaux:
            s_princ = max(info["surface"] for info in niveaux.values())
            ecart = abs(s_princ - surface_annoncee) / surface_annoncee * 100
            msg = (f"Surface annoncée sur le plan : {surface_annoncee} m² | "
                   f"calculée : {round(s_princ,1)} m² (écart {ecart:.0f} %).")
            if ecart > 15:
                msg += " ⚠ Écart important : vérifiez l'échelle et le plan retenu."
        else:
            msg = (f"Surface annoncée sur le plan : {surface_annoncee} m² "
                   "(reprise du cartouche ; non mesurée faute de cotes).")
        alertes.append(msg)

    # Rideau métallique : détection AUTO via le cadre jaune SUPECO + cotes réelles
    cotes = _cotes_facade(doc, cartouches)
    rideaux = _rideaux_supeco(doc, cartouches)
    if rideaux:
        tot = sum(r["surface_m2"] for r in rideaux)
        alertes.append(
            f"Rideau métallique : {len(rideaux)} rideau(x) détecté(s) "
            f"automatiquement (devanture jaune), total ≈ {round(tot,2)} m². "
            "Pré-remplis ci-dessous — vérifiez/ajustez si besoin.")
    elif cotes:
        alertes.append(
            "Rideau métallique : devanture jaune non détectée. Composez les "
            "dimensions à partir des cotes réelles de la façade ci-dessous.")
    else:
        alertes.append(
            "Rideau métallique : ni devanture jaune ni cotes de façade trouvées. "
            "Saisissez la largeur et la hauteur à la main.")

    alertes.append(
        "PDF multi-pages : surface utile mesurée sur le plan le plus propre de "
        "chaque niveau, échelle calibrée sur les cotes. À vérifier d'un coup d'œil.")
    if hsp:
        alertes.insert(0, f"Hauteur sous plafond lue sur le plan : {hsp} m.")

    return _rapport(chemin, pieces, lignes, hsp or 2.70, alertes,
                    hsp_detectee=bool(hsp), cotes=cotes, rideaux=rideaux,
                    projet=_projet(doc), gondoles=_gondoles(cartouches),
                    niveaux_presents=tous_niveaux, surface_annoncee=surface_annoncee)


# ---------------------------------------------------------------- helpers
_MM = 25.4 / 72.0  # mm papier par point


def _rapport(chemin, pieces, lignes, hsp, alertes, hsp_detectee=False,
             cotes=None, rideaux=None, projet=None, gondoles=None,
             niveaux_presents=None, surface_annoncee=None):
    return RapportAnalyse(
        fichier=chemin, unite_dessin=Unite.INCONNU, facteur_vers_metre=0.0,
        nb_calques=0, calques=[], calques_detail=[], ouvrages=[],
        pieces=pieces, hsp_m=(hsp if hsp else 2.70), hsp_detectee=hsp_detectee,
        mapping_ia={}, metre=lignes, alertes=alertes, cotes_facade=cotes or [],
        rideaux_proposes=rideaux or [], projet=projet, nb_gondoles=gondoles,
        niveaux_presents=niveaux_presents or [], surface_annoncee=surface_annoncee)


def _echelle(page, vmin: int = 100) -> float | None:
    """Facteur m/point calibré sur les grandes cotes (≥ vmin cm).
    Robuste : appariement cote<->segment, puis rejet des aberrations (on ne
    garde que les candidats proches de la médiane). Ne lève jamais d'exception."""
    try:
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
        med = statistics.median(cands)
        # Rejet des aberrations : garder les candidats à ±30 % de la médiane
        proches = [c for c in cands if 0.7 * med <= c <= 1.3 * med]
        if len(proches) >= 3:
            med = statistics.median(proches)
        return med / 100.0  # cm/point -> m/point
    except Exception:  # noqa: BLE001
        return None


def _surface_principale(page, facteur: float):
    """Régions fermées plausibles (m²) avec leur taux de remplissage du bbox.
    Renvoie une liste [(aire_m2, taux_remplissage)] triée par aire décroissante.
    Le taux de remplissage distingue une pièce pleine (~0.6) d'un anneau/douve
    autour du bâtiment (faible). Ne lève jamais d'exception."""
    try:
        return _surface_principale_impl(page, facteur)
    except Exception:  # noqa: BLE001
        return []


def _surface_principale_impl(page, facteur: float):
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
