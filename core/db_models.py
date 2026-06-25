"""
Modèles de base de données de Plan Analyzer Pro.

Schéma :
  Projet  1 ── n  Analyse
  - un Projet regroupe plusieurs Analyses (l'historique des versions du métré)
  - chaque Analyse porte un numéro de version croissant au sein du projet
  - les données complètes du rapport sont stockées en JSON pour rejouer/comparer
"""
from __future__ import annotations
from datetime import datetime

from sqlalchemy import (
    Column, Integer, String, DateTime, Float, ForeignKey, Text, JSON
)
from sqlalchemy.orm import relationship

from .database import Base


class Projet(Base):
    __tablename__ = "projets"

    id = Column(Integer, primary_key=True, index=True)
    nom = Column(String(200), nullable=False, index=True)
    description = Column(Text, default="")
    date_creation = Column(DateTime, default=datetime.utcnow)

    analyses = relationship(
        "Analyse", back_populates="projet",
        cascade="all, delete-orphan", order_by="Analyse.version",
    )


class Analyse(Base):
    __tablename__ = "analyses"

    id = Column(Integer, primary_key=True, index=True)
    projet_id = Column(Integer, ForeignKey("projets.id"), nullable=False, index=True)
    version = Column(Integer, nullable=False, default=1)

    nom_fichier = Column(String(300), default="")
    unite_dessin = Column(String(10), default="")
    nb_pieces = Column(Integer, default=0)
    surface_habitable_m2 = Column(Float, default=0.0)
    date_creation = Column(DateTime, default=datetime.utcnow)

    # Rapport complet (RapportAnalyse) sérialisé : permet de tout rejouer/comparer.
    donnees = Column(JSON, nullable=False, default=dict)

    projet = relationship("Projet", back_populates="analyses")
