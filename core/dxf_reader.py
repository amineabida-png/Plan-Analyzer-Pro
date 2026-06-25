"""
Lecteur DXF de BTP QUANT AI.
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
        """Lit $INSUNITS pour déduire l'unité et le facteur vers le mètre."""
        code = int(self.doc.header.get("$INSUNITS", 0))
        return _INSUNITS_MAP.get(code, (Unite.INCONNU, 0.001))

    def calques(self) -> list[str]:
        """Renvoie la liste triée des calques présents."""
        return sorted(layer.dxf.name for layer in self.doc.layers)

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
