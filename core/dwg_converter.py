"""
Convertisseur DWG -> DXF de Plan Analyzer Pro.

Le format DWG est propriétaire : il n'existe pas de lecteur natif fiable en
Python pur. On délègue donc la conversion à un outil externe gratuit, détecté
automatiquement, dans cet ordre de priorité :

  1. ODA File Converter (gratuit, le plus fiable) — piloté via l'addon
     ezdxf.addons.odafc. C'est la voie recommandée.
  2. dwg2dxf (LibreDWG, 100 % libre) s'il est présent dans le PATH.

Si aucun n'est disponible, on lève une erreur claire et actionnable plutôt que
d'échouer silencieusement. L'utilisateur installe ODA une seule fois (deux clics
sous Windows) et, ensuite, glisser un DWG suffit : la conversion est automatique.
"""
from __future__ import annotations
import os
import shutil
import subprocess
import tempfile


class ConversionDWGError(Exception):
    """Levée quand aucun convertisseur DWG n'est disponible ou en cas d'échec."""


def _convertisseur_disponible() -> str | None:
    """Renvoie le nom du convertisseur détecté ('oda', 'libredwg') ou None."""
    try:
        from ezdxf.addons import odafc
        if odafc.is_installed():
            return "oda"
    except Exception:  # noqa: BLE001
        pass
    if shutil.which("dwg2dxf"):
        return "libredwg"
    return None


def conversion_disponible() -> bool:
    """Indique si une conversion DWG est possible sur cette machine."""
    return _convertisseur_disponible() is not None


def convertir_dwg_en_dxf(chemin_dwg: str) -> str:
    """
    Convertit un fichier DWG en DXF et renvoie le chemin du DXF produit.
    Lève ConversionDWGError si aucun convertisseur n'est installé.
    """
    if not os.path.isfile(chemin_dwg):
        raise ConversionDWGError(f"Fichier introuvable : {chemin_dwg}")

    moteur = _convertisseur_disponible()
    if moteur is None:
        raise ConversionDWGError(
            "Aucun convertisseur DWG détecté sur cette machine.\n"
            "Installez « ODA File Converter » (gratuit) :\n"
            "  https://www.opendesign.com/guestfiles/oda_file_converter\n"
            "Une fois installé, relancez l'analyse : la conversion sera automatique.\n"
            "Sur l'hébergement cloud (Railway), convertissez le DWG en DXF en local "
            "puis importez le DXF."
        )

    dossier_sortie = tempfile.mkdtemp()
    chemin_dxf = os.path.join(
        dossier_sortie,
        os.path.splitext(os.path.basename(chemin_dwg))[0] + ".dxf",
    )

    if moteur == "oda":
        from ezdxf.addons import odafc
        try:
            # Convertit en DXF R2018 (large compatibilité)
            odafc.convert(chemin_dwg, chemin_dxf, version="R2018")
        except Exception as e:  # noqa: BLE001
            raise ConversionDWGError(f"Échec de la conversion ODA : {e}") from e

    elif moteur == "libredwg":
        try:
            subprocess.run(
                ["dwg2dxf", "-o", chemin_dxf, chemin_dwg],
                check=True, capture_output=True, timeout=120,
            )
        except subprocess.CalledProcessError as e:
            raise ConversionDWGError(
                f"Échec de la conversion LibreDWG : {e.stderr.decode(errors='ignore')}"
            ) from e

    if not os.path.isfile(chemin_dxf):
        raise ConversionDWGError("La conversion n'a produit aucun fichier DXF.")
    return chemin_dxf
