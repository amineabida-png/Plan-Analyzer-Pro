"""
Détecteur de pièces de Plan Analyzer Pro.
Reconstruit les contours fermés des pièces à partir des segments de murs et
cloisons (polygonisation Shapely), puis associe à chaque pièce son étiquette
texte (nom) par test point-dans-polygone.

Principe :
  1. on récupère tous les segments de murs/cloisons sous forme de LineString ;
  2. unary_union « node » les segments à leurs intersections ;
  3. polygonize reconstitue les faces fermées = les pièces ;
  4. chaque étiquette de pièce (TEXT) tombant dans une face lui donne son nom ;
  5. on calcule surface, périmètre puis les sous-surfaces (carrelage, peinture...).

Limite assumée : les contours sont reconstitués à l'AXE des murs. La surface
obtenue est donc légèrement supérieure à la surface nette (il faudrait retrancher
la demi-épaisseur des murs sur le pourtour). C'est signalé en alerte.
"""
from __future__ import annotations
import uuid

from shapely.geometry import LineString, Point
from shapely.ops import unary_union, polygonize

from .models import Ouvrage, Point as PtModel, TypeOuvrage, PieceDetectee

# Types d'ouvrages linéaires qui délimitent les pièces
_TYPES_DELIMITANTS = {
    TypeOuvrage.MUR_EXT, TypeOuvrage.MUR_INT,
    TypeOuvrage.CLOISON, TypeOuvrage.VOILE,
}


def _segments_depuis_ouvrage(o: Ouvrage) -> list[LineString]:
    """Découpe un ouvrage linéaire en segments LineString (2 points chacun)."""
    pts = [(p.x, p.y) for p in o.points]
    if len(pts) < 2:
        return []
    segs = []
    paires = list(zip(pts, pts[1:]))
    if o.ferme:
        paires.append((pts[-1], pts[0]))
    for a, b in paires:
        if a != b:
            segs.append(LineString([a, b]))
    return segs


def associer_noms(pieces: list[PieceDetectee], textes: list[dict],
                  facteur_vers_metre: float) -> None:
    """
    Associe un nom à chaque pièce à partir de TOUS les textes du plan
    (indépendamment du calque). Modifie les pièces sur place.

    Pour chaque pièce sans nom, on cherche un texte « nom » (contenant des
    lettres) dont la position tombe dans le contour de la pièce ; à défaut, le
    plus proche du centre dans un rayon raisonnable. Robuste aux textes centrés.
    """
    import re as _re
    from shapely.geometry import Polygon

    # Candidats : textes courts contenant au moins une lettre (pas les cotes/nombres)
    candidats = []
    for t in textes:
        txt = (t.get("texte") or "").strip()
        if not txt or len(txt) > 30:
            continue
        if not _re.search(r"[A-Za-zÀ-ÿ]", txt):
            continue
        x, y = t["position"]
        candidats.append((txt, x, y))
    if not candidats:
        return

    k = facteur_vers_metre
    for p in pieces:
        if p.nom and p.nom != "Pièce sans nom":
            continue
        if len(p.contour) < 3:
            continue
        try:
            poly = Polygon([(pt.x, pt.y) for pt in p.contour])
        except Exception:  # noqa: BLE001
            continue
        # 1) texte strictement dans la pièce
        trouve = None
        for (txt, x, y) in candidats:
            if poly.contains(Point(x, y)):
                trouve = txt
                break
        # 2) sinon le plus proche du centre, dans un rayon lié à la taille
        if trouve is None:
            c = poly.centroid
            rayon = (poly.area ** 0.5) * 0.6
            best = None
            for (txt, x, y) in candidats:
                d = ((x - c.x) ** 2 + (y - c.y) ** 2) ** 0.5
                if d <= rayon and (best is None or d < best[1]):
                    best = (txt, d)
            if best:
                trouve = best[0]
        if trouve:
            p.nom = trouve


class DetecteurPieces:
    """Reconstruit les pièces et calcule leurs surfaces."""

    def __init__(self, facteur_vers_metre: float, hsp_m: float = 2.70,
                 surface_min_m2: float = 1.0, surface_max_m2: float = 500.0):
        self.k = facteur_vers_metre
        self.hsp = hsp_m
        # Garde-fous : une vraie pièce fait au moins ~1 m² et rarement > 500 m².
        # Élimine les micro-faces (slivers) et l'enveloppe entière du bâtiment.
        self.surface_min = surface_min_m2
        self.surface_max = surface_max_m2

    def detecter(self, ouvrages: list[Ouvrage]) -> list[PieceDetectee]:
        # 1. Rassembler les segments délimitants
        segments: list[LineString] = []
        for o in ouvrages:
            if o.type in _TYPES_DELIMITANTS:
                segments.extend(_segments_depuis_ouvrage(o))
        if not segments:
            return []

        # 2 & 3. Noder puis polygoniser
        merged = unary_union(segments)
        faces = list(polygonize(merged))
        if not faces:
            return []

        # Étiquettes de pièces disponibles (TEXT sur calque pièce)
        labels = [(o.nom, o.points[0].x, o.points[0].y)
                  for o in ouvrages
                  if o.type == TypeOuvrage.PIECE and o.nom and o.points]

        pieces: list[PieceDetectee] = []
        for face in faces:
            # 5. Surfaces (conversion en mètres)
            surface = face.area * (self.k ** 2)
            # Garde-fou : ignorer les fausses pièces (slivers ou enveloppe globale)
            if surface < self.surface_min or surface > self.surface_max:
                continue
            perimetre = face.length * self.k

            # 4. Associer un nom par point-dans-polygone
            nom = None
            for (lname, lx, ly) in labels:
                if face.contains(Point(lx, ly)):
                    nom = lname
                    break

            pieces.append(PieceDetectee(
                id=str(uuid.uuid4())[:8],
                nom=nom or "Pièce sans nom",
                surface_m2=round(surface, 2),
                perimetre_m=round(perimetre, 2),
                surface_plafond_m2=round(surface, 2),
                surface_carrelage_m2=round(surface, 2),
                surface_peinture_murs_m2=round(perimetre * self.hsp, 2),
                contour=[PtModel(x=x, y=y) for (x, y) in face.exterior.coords],
            ))

        # Tri par surface décroissante pour la lisibilité
        pieces.sort(key=lambda p: p.surface_m2, reverse=True)
        return pieces

    def detecter_auto(self, polylignes: list[dict], lignes: list[dict],
                      textes: list[dict],
                      longueur_min_segment_m: float = 0.25) -> list[PieceDetectee]:
        """
        Détection AUTOMATIQUE des pièces, SANS classification de calques.
        Principe : toute la géométrie du plan est utilisée. On garde les segments
        assez longs (les murs ; on écarte les micro-segments de mobilier, symboles
        et textes), on polygonise, et on conserve les faces dont la surface est
        plausible pour une pièce. Ça marche quel que soit le nom des calques.

        Avantage : aucun clic, aucun mapping. Limite : sur les plans très chargés,
        quelques faux positifs/négatifs possibles — à vérifier d'un coup d'œil.
        """
        # 1. Rassembler TOUS les segments (toutes sources confondues)
        segments: list[LineString] = []
        seuil_unites = longueur_min_segment_m / self.k  # min en unités de dessin
        for geo in polylignes + lignes:
            pts = geo["points"]
            ferme = geo.get("ferme", False)
            if len(pts) < 2:
                continue
            paires = list(zip(pts, pts[1:]))
            if ferme:
                paires.append((pts[-1], pts[0]))
            for a, b in paires:
                # Garder les segments assez longs (murs), écarter le bruit
                dx, dy = b[0] - a[0], b[1] - a[1]
                if (dx * dx + dy * dy) ** 0.5 >= seuil_unites and a != b:
                    segments.append(LineString([a, b]))
        if len(segments) < 3:
            return []

        # 2. Noder + polygoniser tout
        try:
            merged = unary_union(segments)
            faces = list(polygonize(merged))
        except Exception:  # noqa: BLE001
            return []
        if not faces:
            return []

        # Étiquettes candidates = textes courts contenant des lettres (noms de pièces).
        # On écarte les nombres seuls, cotes et longs textes (cartouche).
        import re as _re
        labels = []
        for t in textes:
            txt = (t.get("texte") or "").strip()
            if not txt or len(txt) > 30:
                continue
            # Doit contenir au moins une lettre (un nom de pièce, pas une cote)
            if not _re.search(r"[A-Za-zÀ-ÿ]", txt):
                continue
            x, y = t["position"]
            labels.append((txt, x, y))

        pieces: list[PieceDetectee] = []
        for face in faces:
            surface = face.area * (self.k ** 2)
            if surface < self.surface_min or surface > self.surface_max:
                continue
            perimetre = face.length * self.k
            if perimetre <= 0:
                continue
            compacite = (4 * 3.14159 * face.area) / (face.length ** 2)
            if compacite < 0.08:  # face très allongée = probablement pas une pièce
                continue

            # Nom : 1) un label strictement dans la pièce, 2) sinon le plus proche du centre
            nom = None
            for (lname, lx, ly) in labels:
                if face.contains(Point(lx, ly)):
                    nom = lname
                    break
            if nom is None and labels:
                c = face.centroid
                # rayon de recherche lié à la taille de la pièce
                rayon = (face.area ** 0.5) * 0.6
                meilleure = None
                for (lname, lx, ly) in labels:
                    d = ((lx - c.x) ** 2 + (ly - c.y) ** 2) ** 0.5
                    if d <= rayon and (meilleure is None or d < meilleure[1]):
                        meilleure = (lname, d)
                if meilleure:
                    nom = meilleure[0]

            pieces.append(PieceDetectee(
                id=str(uuid.uuid4())[:8],
                nom=nom or "Pièce sans nom",
                surface_m2=round(surface, 2),
                perimetre_m=round(perimetre, 2),
                surface_plafond_m2=round(surface, 2),
                surface_carrelage_m2=round(surface, 2),
                surface_peinture_murs_m2=round(perimetre * self.hsp, 2),
                contour=[PtModel(x=x, y=y) for (x, y) in face.exterior.coords],
            ))

        pieces.sort(key=lambda p: p.surface_m2, reverse=True)
        return pieces
