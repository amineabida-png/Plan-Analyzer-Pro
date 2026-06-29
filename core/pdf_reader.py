"""
Lecteur PDF de Plan Analyzer Pro.

Deux types de PDF, gérés automatiquement :

1. PDF VECTORIEL (exporté depuis AutoCAD/Revit) : contient de vraies lignes et
   du vrai texte. On les extrait avec PyMuPDF -> fiabilité correcte (on réutilise
   ensuite tout le moteur de détection des pièces).

2. PDF SCANNÉ (image d'un plan papier) : que des pixels. On rasterise la page,
   on détecte les traits avec OpenCV (Hough) et le texte avec l'OCR Tesseract.
   -> Résultat APPROXIMATIF, à valider à l'œil.

Limite fondamentale du PDF : il ne contient PAS l'échelle réelle du dessin
(contrairement au DXF qui a ses unités). On déduit donc le facteur de conversion
depuis l'échelle : soit fournie par l'utilisateur, soit lue sur le plan
(« ECHELLE 1:50 »), soit supposée par défaut avec un avertissement.

Expose la même interface que LectureDXF pour réutiliser le pipeline :
  calques(), resume_calques(), detecter_hsp(), polylignes(), lignes(),
  inserts(), textes(), .unite, .facteur_vers_metre, .chemin
"""
from __future__ import annotations
import re

from .models import Unite

# 1 point PDF = 1/72 pouce = 0.352777... mm sur le papier.
_MM_PAR_POINT = 25.4 / 72.0
# Échelle supposée si rien n'est détecté ni fourni (plan d'architecte courant).
_ECHELLE_DEFAUT = 50


class LecturePDF:
    """Charge un PDF de plan (vectoriel ou scanné) et expose sa géométrie."""

    def __init__(self, chemin: str, echelle: int | None = None):
        import fitz  # PyMuPDF

        self.chemin = chemin
        self.unite = Unite.INCONNU
        self.alertes: list[str] = []
        self._polylignes: list[dict] = []
        self._lignes: list[dict] = []
        self._textes: list[dict] = []

        doc = fitz.open(chemin)
        if doc.page_count == 0:
            doc.close()
            raise ValueError("PDF vide.")
        page = doc[0]  # on analyse la première page (le plan principal)
        if doc.page_count > 1:
            self.alertes.append(
                f"PDF de {doc.page_count} pages : seule la 1re page est analysée.")

        vectoriel = self._est_vectoriel(page)
        if vectoriel:
            self._lire_vectoriel(page)
            self.alertes.append(
                "PDF vectoriel détecté : géométrie extraite directement "
                "(fiabilité correcte).")
        else:
            self._lire_scanne(page, fitz)
            self.alertes.append(
                "PDF scanné détecté : murs reconstruits par vision + OCR. "
                "Résultat APPROXIMATIF — à vérifier soigneusement.")

        # Échelle -> facteur de conversion points -> mètres
        n = echelle or self._detecter_echelle()
        if echelle:
            self.alertes.append(f"Échelle 1:{n} (fournie).")
        elif n != _ECHELLE_DEFAUT or self._echelle_lue:
            self.alertes.append(f"Échelle 1:{n} lue sur le plan.")
        else:
            self.alertes.append(
                f"Échelle non trouvée : 1:{n} supposée. Si les surfaces semblent "
                "fausses, indiquez l'échelle réelle du plan et relancez.")
        # mm réel par point = mm papier/point × N ; en mètres : /1000
        self.facteur_vers_metre = _MM_PAR_POINT * n / 1000.0
        doc.close()

    # ---------- détection du type ----------
    @staticmethod
    def _est_vectoriel(page) -> bool:
        """Vectoriel si la page contient de vrais tracés ; scanné si c'est
        surtout une image avec très peu de vecteurs."""
        try:
            n_traces = sum(len(d.get("items", [])) for d in page.get_drawings())
        except Exception:  # noqa: BLE001
            n_traces = 0
        n_images = len(page.get_images())
        # Aucune image : si le moindre tracé existe, c'est vectoriel.
        if n_images == 0:
            return n_traces > 0
        # Avec image(s) : vectoriel seulement s'il y a beaucoup de tracés
        # (cas d'un plan vectoriel contenant un logo/cartouche en image).
        return n_traces >= 30

    # ---------- lecture vectorielle ----------
    def _lire_vectoriel(self, page) -> None:
        self._echelle_lue = False
        for d in page.get_drawings():
            for it in d.get("items", []):
                kind = it[0]
                try:
                    if kind == "l":  # ligne : (p1, p2)
                        p1, p2 = it[1], it[2]
                        self._lignes.append({
                            "calque": "0",
                            "points": [(p1.x, p1.y), (p2.x, p2.y)],
                            "ferme": False,
                        })
                    elif kind == "re":  # rectangle
                        r = it[1]
                        self._polylignes.append({
                            "calque": "0",
                            "points": [(r.x0, r.y0), (r.x1, r.y0),
                                       (r.x1, r.y1), (r.x0, r.y1)],
                            "ferme": True,
                        })
                    elif kind == "qu":  # quadrilatère
                        q = it[1]
                        self._polylignes.append({
                            "calque": "0",
                            "points": [(q.ul.x, q.ul.y), (q.ur.x, q.ur.y),
                                       (q.lr.x, q.lr.y), (q.ll.x, q.ll.y)],
                            "ferme": True,
                        })
                    # 'c' (courbes de Bézier) ignorées : rarement des murs.
                except Exception:  # noqa: BLE001
                    continue
        # Textes (vrais caractères)
        for w in page.get_text("words"):
            x0, y0, x1, y1, mot = w[0], w[1], w[2], w[3], w[4]
            txt = (mot or "").strip()
            if txt:
                self._textes.append({
                    "calque": "0",
                    "texte": txt,
                    "position": ((x0 + x1) / 2, (y0 + y1) / 2),
                })

    # ---------- lecture scannée (OCR + vision) ----------
    def _lire_scanne(self, page, fitz) -> None:
        self._echelle_lue = False
        import numpy as np
        import cv2

        zoom = 2.0  # 144 dpi : compromis netteté/performance
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom))
        img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
            pix.height, pix.width, pix.n)
        if pix.n == 4:
            img = cv2.cvtColor(img, cv2.COLOR_RGBA2BGR)
        elif pix.n == 1:
            img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
        gris = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        h, w = gris.shape

        # Murs = pixels sombres. Binarisation inverse : murs -> blanc.
        _, murs = cv2.threshold(gris, 0, 255,
                                cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
        # Fermer les petits trous (portes, légers manques de trait)
        noyau = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
        murs = cv2.morphologyEx(murs, cv2.MORPH_CLOSE, noyau, iterations=2)

        # Régions fermées = composantes du complément (zones NON-mur)
        non_mur = cv2.bitwise_not(murs)
        n, labels, stats, _ = cv2.connectedComponentsWithStats(non_mur, 8)
        aire_totale = h * w
        for i in range(1, n):  # 0 = fond
            x, y, bw, bh, aire = (stats[i][0], stats[i][1], stats[i][2],
                                  stats[i][3], stats[i][4])
            # Écarter : composante touchant le bord (extérieur), trop petite ou
            # quasi toute la page.
            if x <= 1 or y <= 1 or x + bw >= w - 1 or y + bh >= h - 1:
                continue
            if aire < (aire_totale * 0.0008) or aire > (aire_totale * 0.6):
                continue
            masque = (labels == i).astype(np.uint8) * 255
            contours, _ = cv2.findContours(masque, cv2.RETR_EXTERNAL,
                                           cv2.CHAIN_APPROX_SIMPLE)
            if not contours:
                continue
            c = max(contours, key=cv2.contourArea)
            approx = cv2.approxPolyDP(c, 0.01 * cv2.arcLength(c, True), True)
            if len(approx) < 3:
                continue
            pts = [(float(p[0][0]) / zoom, float(p[0][1]) / zoom) for p in approx]
            self._polylignes.append({"calque": "0", "points": pts, "ferme": True})

        # OCR du texte (noms de pièces, échelle)
        try:
            import pytesseract
            data = pytesseract.image_to_data(
                gris, lang="fra+eng", output_type=pytesseract.Output.DICT)
            for i, mot in enumerate(data["text"]):
                txt = (mot or "").strip()
                try:
                    conf = int(data["conf"][i])
                except (ValueError, KeyError):
                    conf = -1
                if txt and conf >= 40:
                    cx = (data["left"][i] + data["width"][i] / 2) / zoom
                    cy = (data["top"][i] + data["height"][i] / 2) / zoom
                    self._textes.append({
                        "calque": "0", "texte": txt, "position": (cx, cy)})
        except Exception:  # noqa: BLE001
            self.alertes.append(
                "OCR indisponible : noms de pièces non lus sur ce PDF scanné.")

    # ---------- échelle ----------
    def _detecter_echelle(self) -> int:
        """Cherche une échelle '1:N' ou '1/N' dans les textes lus."""
        motif = re.compile(r"1\s*[:/]\s*(\d{1,4})")
        for t in self._textes:
            m = motif.search(t["texte"].replace(" ", ""))
            if m:
                n = int(m.group(1))
                if 5 <= n <= 2000:
                    self._echelle_lue = True
                    return n
        self._echelle_lue = False
        return _ECHELLE_DEFAUT

    # ---------- interface commune (comme LectureDXF) ----------
    def calques(self) -> list[str]:
        return []

    def resume_calques(self) -> list[dict]:
        return []

    def detecter_hsp(self):
        return None  # le PDF ne porte pas d'info fiable de hauteur sous plafond

    def polylignes(self) -> list[dict]:
        return self._polylignes

    def lignes(self) -> list[dict]:
        return self._lignes

    def inserts(self) -> list[dict]:
        return []

    def textes(self) -> list[dict]:
        return self._textes
