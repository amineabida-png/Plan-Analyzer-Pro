"""
Dépôt de données de Plan Analyzer Pro.
Encapsule toutes les opérations sur la base : création de projets, sauvegarde
d'analyses versionnées, historique et comparaison de deux versions.
"""
from __future__ import annotations
from sqlalchemy import func
from sqlalchemy.orm import Session

from .db_models import Projet, Analyse
from .models import RapportAnalyse


def creer_projet(db: Session, nom: str, description: str = "") -> Projet:
    """Crée un nouveau projet."""
    projet = Projet(nom=nom, description=description)
    db.add(projet)
    db.commit()
    db.refresh(projet)
    return projet


def lister_projets(db: Session) -> list[Projet]:
    """Liste tous les projets, du plus récent au plus ancien."""
    return db.query(Projet).order_by(Projet.date_creation.desc()).all()


def obtenir_projet(db: Session, projet_id: int) -> Projet | None:
    return db.query(Projet).filter(Projet.id == projet_id).first()


def sauvegarder_analyse(db: Session, projet_id: int,
                        rapport: RapportAnalyse) -> Analyse:
    """
    Enregistre une analyse dans un projet en lui attribuant le prochain
    numéro de version (versionning automatique).
    """
    derniere_version = (
        db.query(func.max(Analyse.version))
        .filter(Analyse.projet_id == projet_id)
        .scalar()
    ) or 0

    surface = sum(p.surface_m2 for p in rapport.pieces)
    analyse = Analyse(
        projet_id=projet_id,
        version=derniere_version + 1,
        nom_fichier=rapport.fichier,
        unite_dessin=rapport.unite_dessin.value,
        nb_pieces=len(rapport.pieces),
        surface_habitable_m2=round(surface, 2),
        donnees=rapport.model_dump(mode="json"),
    )
    db.add(analyse)
    db.commit()
    db.refresh(analyse)
    return analyse


def lister_analyses(db: Session, projet_id: int) -> list[Analyse]:
    """Historique des analyses d'un projet, par version croissante."""
    return (
        db.query(Analyse)
        .filter(Analyse.projet_id == projet_id)
        .order_by(Analyse.version)
        .all()
    )


def obtenir_analyse(db: Session, analyse_id: int) -> Analyse | None:
    return db.query(Analyse).filter(Analyse.id == analyse_id).first()


def comparer_analyses(db: Session, analyse_a_id: int,
                      analyse_b_id: int) -> dict:
    """
    Compare deux analyses (deux versions d'un plan) poste par poste.
    Renvoie les écarts de quantité pour chaque poste du métré.
    """
    a = obtenir_analyse(db, analyse_a_id)
    b = obtenir_analyse(db, analyse_b_id)
    if not a or not b:
        return {"erreur": "Analyse(s) introuvable(s)."}

    def index_metre(analyse: Analyse) -> dict[str, float]:
        return {l["poste"]: l["quantite"]
                for l in analyse.donnees.get("metre", [])}

    ma, mb = index_metre(a), index_metre(b)
    postes = sorted(set(ma) | set(mb))
    ecarts = []
    for poste in postes:
        qa = ma.get(poste, 0.0)
        qb = mb.get(poste, 0.0)
        ecarts.append({
            "poste": poste,
            "version_a": qa,
            "version_b": qb,
            "ecart": round(qb - qa, 2),
        })
    return {
        "version_a": a.version,
        "version_b": b.version,
        "ecarts": ecarts,
    }
