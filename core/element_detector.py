"""
Détecteur d'ouvrages de Plan Analyzer Pro.
Transforme la géométrie brute (issue de LectureDXF) en ouvrages métier classés.

Stratégie de classification (couche 1, déterministe) :
  - on s'appuie d'abord sur le NOM DU CALQUE, qui suit des conventions BTP
    (MUR, CLOISON, POTEAU, PORTE, FENETRE...). C'est rapide et fiable.
  - la géométrie sert ensuite à départager les cas ambigus
    (ex : un petit rectangle fermé ~30x30 cm = poteau).

Plus tard, une couche 2 (IA Groq/Llama) pourra reclasser les entités
sur calque "0" ou mal nommées. Ici on pose le socle déterministe robuste.
"""
from __future__ import annotations
import re
import math
import uuid

from .models import Ouvrage, Point, TypeOuvrage

# Dictionnaire de mots-clés -> type d'ouvrage, recherchés dans le nom de calque.
# L'ordre compte : on teste du plus spécifique au plus générique.
_REGLES_CALQUE: list[tuple[str, TypeOuvrage]] = [
    (r"MUR.*EXT|EXT.*MUR|FACADE|PORTEUR", TypeOuvrage.MUR_EXT),
    (r"CLOISON|DOUBLAGE", TypeOuvrage.CLOISON),
    (r"MUR|WALL", TypeOuvrage.MUR_INT),
    (r"POTEAU|POTEAUX|COLUMN|COL\b", TypeOuvrage.POTEAU),
    (r"POUTRE|BEAM|LINTEAU", TypeOuvrage.POUTRE),
    (r"VOILE", TypeOuvrage.VOILE),
    (r"DALLE|PLANCHER|SLAB", TypeOuvrage.DALLE),
    (r"PORTE|DOOR", TypeOuvrage.PORTE),
    (r"FENETRE|FEN\b|WINDOW", TypeOuvrage.FENETRE),
    (r"PIECE|ROOM|LOCAL", TypeOuvrage.PIECE),
]


def _type_depuis_calque(calque: str,
                        mapping: dict[str, str] | None = None) -> TypeOuvrage | None:
    """
    Retourne le type d'ouvrage déduit du nom de calque.
    Priorité au mapping fourni par l'IA (plans réels), puis aux règles par mots-clés.
    """
    if mapping and calque in mapping:
        try:
            return TypeOuvrage(mapping[calque])
        except ValueError:
            pass
    nom = calque.upper()
    for motif, type_ouvrage in _REGLES_CALQUE:
        if re.search(motif, nom):
            return type_ouvrage
    return None


def _longueur_polyligne(points: list[tuple[float, float]], ferme: bool) -> float:
    """Longueur développée d'une polyligne (unités de dessin)."""
    if len(points) < 2:
        return 0.0
    total = 0.0
    paires = list(zip(points, points[1:]))
    if ferme:
        paires.append((points[-1], points[0]))
    for (x1, y1), (x2, y2) in paires:
        total += math.hypot(x2 - x1, y2 - y1)
    return total


def _aire_polygone(points: list[tuple[float, float]]) -> float:
    """Aire d'un polygone fermé par la formule du lacet (valeur absolue)."""
    if len(points) < 3:
        return 0.0
    s = 0.0
    n = len(points)
    for i in range(n):
        x1, y1 = points[i]
        x2, y2 = points[(i + 1) % n]
        s += x1 * y2 - x2 * y1
    return abs(s) / 2.0


class DetecteurOuvrages:
    """Construit la liste d'ouvrages métier à partir de la géométrie brute."""

    # Seuil : un rectangle fermé dont la diagonale fait < ce seuil (mm) et
    # qui est sur un calque POTEAU est traité comme un poteau ponctuel.
    SEUIL_POTEAU_MM = 1000  # 1 m de diagonale max

    def __init__(self, facteur_vers_metre: float,
                 mapping_calques: dict[str, str] | None = None):
        self.k = facteur_vers_metre  # conversion unités dessin -> mètres
        self.mapping = mapping_calques or {}

    def detecter(
        self,
        polylignes: list[dict],
        lignes: list[dict],
        inserts: list[dict],
        textes: list[dict],
    ) -> list[Ouvrage]:
        ouvrages: list[Ouvrage] = []

        # --- 1. Polylignes et lignes : murs, cloisons, poteaux, dalles ---
        for geo in polylignes + lignes:
            type_o = _type_depuis_calque(geo["calque"], self.mapping) or TypeOuvrage.INCONNU
            pts = geo["points"]
            ferme = geo["ferme"]
            longueur = _longueur_polyligne(pts, ferme) * self.k
            surface = _aire_polygone(pts) * (self.k ** 2) if ferme else None

            ouvrages.append(Ouvrage(
                id=str(uuid.uuid4())[:8],
                type=type_o,
                calque=geo["calque"],
                points=[Point(x=x, y=y) for (x, y) in pts],
                ferme=ferme,
                longueur_m=round(longueur, 3),
                surface_m2=round(surface, 3) if surface else None,
            ))

        # --- 2. Blocs INSERT : portes et fenêtres comptés à l'unité ---
        # IMPORTANT : on ne compte QUE les blocs dont le CALQUE est classé
        # porte/fenêtre. Sinon, sur un vrai plan, tous les blocs (mobilier,
        # symboles, cartouche…) seraient comptés à tort comme ouvertures.
        # Le secours par nom de bloc ne s'applique que si AUCUN mapping n'existe.
        a_mapping = bool(self.mapping)
        for ins in inserts:
            type_o = _type_depuis_calque(ins["calque"], self.mapping)
            if type_o not in (TypeOuvrage.PORTE, TypeOuvrage.FENETRE):
                if a_mapping:
                    # Mapping fourni : un bloc hors calque porte/fenêtre est ignoré.
                    continue
                # Pas de mapping : secours prudent par nom de bloc.
                nom = ins["nom_bloc"].upper()
                if "PORTE" in nom or "DOOR" in nom:
                    type_o = TypeOuvrage.PORTE
                elif "FENETRE" in nom or "WINDOW" in nom:
                    type_o = TypeOuvrage.FENETRE
                else:
                    continue  # bloc non pertinent : on ne le compte pas
            x, y = ins["position"]
            ouvrages.append(Ouvrage(
                id=str(uuid.uuid4())[:8],
                type=type_o,
                calque=ins["calque"],
                points=[Point(x=x, y=y)],
            ))

        # --- 3. Textes : noms de pièces ---
        for txt in textes:
            type_o = _type_depuis_calque(txt["calque"], self.mapping)
            if type_o == TypeOuvrage.PIECE:
                x, y = txt["position"]
                ouvrages.append(Ouvrage(
                    id=str(uuid.uuid4())[:8],
                    type=TypeOuvrage.PIECE,
                    calque=txt["calque"],
                    points=[Point(x=x, y=y)],
                    nom=txt["texte"],
                ))

        return ouvrages
