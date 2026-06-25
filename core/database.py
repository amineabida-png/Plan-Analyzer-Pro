"""
Couche base de données de Plan Analyzer Pro.
Compatible :
  - SQLite en local (par défaut, zéro configuration) ;
  - PostgreSQL en production (Railway fournit DATABASE_URL automatiquement).

La variable d'environnement DATABASE_URL choisit le moteur. Railway donne parfois
une URL en « postgres:// » : SQLAlchemy 2.x exige « postgresql:// », on corrige.
"""
from __future__ import annotations
import os

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

# URL de connexion : PostgreSQL si fournie, sinon SQLite local.
DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///plan_analyzer.db")

# Correctif Railway : postgres:// -> postgresql://
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

# SQLite a besoin d'une option spécifique pour le multithread (FastAPI).
connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}

engine = create_engine(DATABASE_URL, connect_args=connect_args, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def init_db() -> None:
    """Crée les tables si elles n'existent pas (appelé au démarrage)."""
    from . import db_models  # noqa: F401  (enregistre les modèles)
    Base.metadata.create_all(bind=engine)


def get_session():
    """Fournit une session de base de données (à fermer après usage)."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
