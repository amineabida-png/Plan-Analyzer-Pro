"""
Modèles de données de Plan Analyzer Pro.
Tout est fortement typé (Pydantic) pour garantir la cohérence entre
la lecture DXF, la détection des ouvrages et le calcul du métré.
"""
from __future__ import annotations
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field


class Unite(str, Enum):
    """Unités de dessin reconnues dans l'en-tête DXF ($INSUNITS)."""
    MILLIMETRE = "mm"
    CENTIMETRE = "cm"
    METRE = "m"
    POUCE = "in"
    PIED = "ft"
    INCONNU = "?"


class TypeOuvrage(str, Enum):
    """Catégories d'ouvrages détectables sur un plan vectoriel."""
    MUR_EXT = "mur_exterieur"
    MUR_INT = "mur_interieur"
    CLOISON = "cloison"
    POTEAU = "poteau"
    POUTRE = "poutre"
    VOILE = "voile"
    DALLE = "dalle"
    PORTE = "porte"
    FENETRE = "fenetre"
    PIECE = "piece"
    INCONNU = "inconnu"


class Point(BaseModel):
    x: float
    y: float


class Ouvrage(BaseModel):
    """Un élément de construction détecté, avec sa géométrie et ses métadonnées."""
    id: str
    type: TypeOuvrage
    calque: str = Field(..., description="Calque DXF d'origine")
    points: list[Point] = Field(default_factory=list)
    ferme: bool = False
    # Grandeurs géométriques calculées (toujours exprimées en mètres / m² une fois converties)
    longueur_m: Optional[float] = None
    surface_m2: Optional[float] = None
    nom: Optional[str] = None  # ex: nom de pièce issu de l'OCR/texte


class PieceDetectee(BaseModel):
    """Une pièce reconstituée par fermeture topologique des murs."""
    id: str
    nom: str
    surface_m2: float
    perimetre_m: float
    surface_plafond_m2: float
    surface_carrelage_m2: float
    surface_peinture_murs_m2: float
    contour: list["Point"] = Field(default_factory=list)


class MetreLigne(BaseModel):
    """Une ligne du métré : un poste quantifié."""
    poste: str
    type_ouvrage: TypeOuvrage
    quantite: float
    unite: str  # "m", "m2", "m3", "U"
    detail: Optional[str] = None


class RapportAnalyse(BaseModel):
    """Résultat complet de l'analyse d'un fichier."""
    fichier: str
    unite_dessin: Unite
    facteur_vers_metre: float
    nb_calques: int
    calques: list[str]
    ouvrages: list[Ouvrage]
    pieces: list[PieceDetectee] = Field(default_factory=list)
    hsp_m: float = 2.70
    hsp_detectee: bool = False  # True si lue sur le plan, False si hypothèse
    mapping_ia: dict = Field(default_factory=dict)  # calques classés par l'IA
    metre: list[MetreLigne]
    alertes: list[str] = Field(default_factory=list)
