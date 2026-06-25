"""
Lecteur DXF de Plan Analyzer Pro.
Responsabilités :
  - ouvrir un fichier DXF (ezdxf, licence MIT, gratuit)
  - détecter automatiquement l'unité de dessin ($INSUNITS)
  - lister les calques
  - extraire les entités géométriques (LWPOLYLINE, LINE, INSERT, TEXT...)
    sous une forme normalisée et indépendante de la suite du pipeline.
"""
from __future__ import annotations
import ezdxf
from ezdxf.document import Drawing

from .models import Unite

# Correspondance $INSUNITS (codes DXF) -> unité + facteur de conversion vers le mètre.
# Référence : spécification DXF, groupe 70 de la variable $INSUNITS.
_INSUNITS_MAP: dict[int, tuple[Unite, float]] = {
    0: (Unite.INCONNU, 0.001),   # sans unité : on suppose mm par prudence
    1: (Unite.POUCE, 0.0254),
    2: (Unite.PIED, 0.3048),
    4: (Unite.MILLIMETRE, 0.001),
    5: (Unite.CENTIMETRE, 0.01),
    6: (Unite.METRE, 1.0),
}


class LectureDXF:
    """Charge un DXF et expose ses entités de façon structurée."""

    def __init__(self, chemin: str):
        self.chemin = chemin
        self.doc: Drawing = ezdxf.readfile(chemin)
        self.msp = self.doc.modelspace()
        self.unite, self.facteur_vers_metre = self._detecter_unite()

    def _detecter_unite(self) -> tuple[Unite, float]:
        """
        Détermine l'unité et le facteur vers le mètre.
        1. Lit $INSUNITS si présent et explicite.
        2. Sinon (unité absente/0), l'infère depuis les dimensions du dessin :
           un bâtiment fait typiquement quelques mètres à quelques dizaines de
           mètres, ce qui permet de deviner mm / cm / m.
        """
        code = int(self.doc.header.get("$INSUNITS", 0))
        if code in _INSUNITS_MAP and code != 0:
            return _INSUNITS_MAP[code]
        # Unité absente : inférence par l'étendue du dessin
        return self._inferer_unite_par_etendue()

    def _inferer_unite_par_etendue(self) -> tuple[Unite, float]:
        """Devine l'unité d'après la plus grande dimension du dessin."""
        xs, ys = [], []
        for e in self.msp.query("LWPOLYLINE"):
            for p in e.get_points("xy"):
                xs.append(p[0]); ys.append(p[1])
        for e in self.msp.query("LINE"):
            xs.extend([e.dxf.start.x, e.dxf.end.x])
            ys.extend([e.dxf.start.y, e.dxf.end.y])
        if not xs:
            return (Unite.MILLIMETRE, 0.001)  # défaut prudent
        etendue = max(max(xs) - min(xs), max(ys) - min(ys))
        # Un bâtiment réel : quelques m à quelques dizaines de m.
        if etendue > 2000:        # ex : 10000 -> mm
            return (Unite.MILLIMETRE, 0.001)
        if etendue > 200:         # ex : 1000 -> cm
            return (Unite.CENTIMETRE, 0.01)
        return (Unite.METRE, 1.0)  # ex : 10 -> m

    def calques(self) -> list[str]:
        """Renvoie la liste triée des calques présents."""
        return sorted(layer.dxf.name for layer in self.doc.layers)

    def resume_calques(self) -> list[dict]:
        """
        Résume chaque calque utilisé : nombre d'entités et types dominants
        (lignes, polylignes, blocs, textes). Sert à la classification manuelle :
        l'utilisateur voit quels calques sont volumineux et de quel type, pour
        décider rapidement lesquels sont des murs, portes, etc.
        """
        stats: dict[str, dict] = {}
        for e in self.msp:
            try:
                calque = e.dxf.layer
            except Exception:  # noqa: BLE001
                continue
            d = stats.setdefault(calque, {
                "calque": calque, "total": 0,
                "lignes": 0, "polylignes": 0, "blocs": 0, "textes": 0,
            })
            d["total"] += 1
            t = e.dxftype()
            if t in ("LINE",):
                d["lignes"] += 1
            elif t in ("LWPOLYLINE", "POLYLINE"):
                d["polylignes"] += 1
            elif t == "INSERT":
                d["blocs"] += 1
            elif t in ("TEXT", "MTEXT"):
                d["textes"] += 1
        # Tri par volume décroissant (les gros calques d'abord)
        return sorted(stats.values(), key=lambda x: x["total"], reverse=True)

    def polylignes(self) -> list[dict]:
        """
        Extrait toutes les LWPOLYLINE et POLYLINE.
        Renvoie une liste de dicts : {calque, points[(x,y)], ferme}.
        """
        result = []
        for e in self.msp.query("LWPOLYLINE"):
            pts = [(p[0], p[1]) for p in e.get_points("xy")]
            result.append({
                "calque": e.dxf.layer,
                "points": pts,
                "ferme": bool(e.closed),
            })
        for e in self.msp.query("POLYLINE"):
            pts = [(v.dxf.location.x, v.dxf.location.y) for v in e.vertices]
            result.append({
                "calque": e.dxf.layer,
                "points": pts,
                "ferme": bool(e.is_closed),
            })
        return result

    def lignes(self) -> list[dict]:
        """Extrait les segments LINE simples."""
        out = []
        for e in self.msp.query("LINE"):
            out.append({
                "calque": e.dxf.layer,
                "points": [(e.dxf.start.x, e.dxf.start.y),
                           (e.dxf.end.x, e.dxf.end.y)],
                "ferme": False,
            })
        return out

    def inserts(self) -> list[dict]:
        """Extrait les références de blocs (INSERT) : portes, fenêtres, mobilier..."""
        out = []
        for e in self.msp.query("INSERT"):
            out.append({
                "calque": e.dxf.layer,
                "nom_bloc": e.dxf.name,
                "position": (e.dxf.insert.x, e.dxf.insert.y),
            })
        return out

    def textes(self) -> list[dict]:
        """Extrait les TEXT et MTEXT (noms de pièces, cartouche, cotes...)."""
        out = []
        for e in self.msp.query("TEXT"):
            out.append({
                "calque": e.dxf.layer,
                "texte": e.dxf.text.strip(),
                "position": (e.dxf.insert.x, e.dxf.insert.y),
            })
        for e in self.msp.query("MTEXT"):
            out.append({
                "calque": e.dxf.layer,
                "texte": e.text.strip(),
                "position": (e.dxf.insert.x, e.dxf.insert.y),
            })
        return out

    def detecter_hsp(self) -> float | None:
        """
        Cherche une hauteur sous plafond écrite sur le plan.
        Reconnaît des annotations du type « HSP 2.80 », « H.S.P : 2,70 »,
        « SOUS PLAFOND 2.50 », « HT 2.70 ». Renvoie la valeur en mètres ou None.
        Un plan 2D ne contient pas toujours cette information : si absente,
        renvoie None (l'appelant utilisera une valeur par défaut à valider).
        """
        import re
        motifs = [
            r"H\.?\s*S\.?\s*P\.?\s*[:=]?\s*([0-9]+[.,][0-9]{1,2})",
            r"SOUS[\s-]*PLAFOND\s*[:=]?\s*([0-9]+[.,][0-9]{1,2})",
            r"HAUTEUR\s*(?:SOUS\s*PLAFOND)?\s*[:=]?\s*([0-9]+[.,][0-9]{1,2})",
            r"\bH\.?T\.?\s*[:=]?\s*([0-9]+[.,][0-9]{1,2})",
        ]
        for txt in self.textes():
            t = txt["texte"].upper()
            for motif in motifs:
                m = re.search(motif, t)
                if m:
                    val = float(m.group(1).replace(",", "."))
                    if 2.0 <= val <= 5.0:  # garde-fou : HSP plausible
                        return val
        return None
