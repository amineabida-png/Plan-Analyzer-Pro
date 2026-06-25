"""
Couche IA de Plan Analyzer Pro, propulsée par Groq (offre gratuite).

Rôle de l'IA (Llama via Groq) :
  - répondre en langage naturel aux questions sur le métré
    (« quel est le volume de béton ? », « calcule uniquement les murs »…) ;
  - vérifier la cohérence et suggérer des matériaux ;
  - aider à interpréter les ouvrages.

L'IA ne mesure pas elle-même : elle raisonne sur les données chiffrées déjà
extraites par le moteur géométrique, qu'on lui fournit dans le contexte. Cela
garantit des réponses ancrées sur des quantités réelles, pas inventées.

Configuration : variable d'environnement GROQ_API_KEY (clé gratuite sur
https://console.groq.com). Sans clé, l'IA renvoie un message d'invitation à la
configurer, sans planter l'application.
"""
from __future__ import annotations
import os
import json

from .models import RapportAnalyse

# Modèle Groq par défaut (configurable : les noms évoluent côté Groq).
GROQ_MODEL = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")

_SYSTEM = (
    "Tu es un assistant métreur expert en BTP, travaillant au Maroc (devise : "
    "dirham marocain, DH). Tu réponds de façon concise et professionnelle, en "
    "français. Tu t'appuies UNIQUEMENT sur les données de métré fournies dans le "
    "contexte : ne jamais inventer de quantité. Si une information manque, dis-le "
    "clairement. Quand on te demande un calcul, utilise les chiffres du contexte."
)


def ia_disponible() -> bool:
    """Indique si une clé Groq est configurée."""
    return bool(os.environ.get("GROQ_API_KEY"))


def _contexte_metre(rapport: RapportAnalyse) -> str:
    """Construit un résumé textuel compact du rapport pour le prompt."""
    lignes = ["MÉTRÉ (postes chiffrés) :"]
    for l in rapport.metre:
        detail = f" — {l.detail}" if l.detail else ""
        lignes.append(f"  - {l.poste} : {l.quantite} {l.unite}{detail}")
    if rapport.pieces:
        lignes.append("PIÈCES :")
        for p in rapport.pieces:
            lignes.append(
                f"  - {p.nom} : {p.surface_m2} m² (périmètre {p.perimetre_m} m, "
                f"peinture murs {p.surface_peinture_murs_m2} m²)")
    lignes.append(f"Unité du dessin : {rapport.unite_dessin.value}")
    if rapport.alertes:
        lignes.append("ALERTES : " + " | ".join(rapport.alertes))
    return "\n".join(lignes)


def repondre(question: str, rapport: RapportAnalyse) -> dict:
    """
    Répond à une question de l'utilisateur en s'appuyant sur le métré.
    Renvoie {"reponse": str, "ok": bool}.
    """
    if not ia_disponible():
        return {
            "ok": False,
            "reponse": (
                "L'IA n'est pas encore configurée. Ajoutez une clé GROQ_API_KEY "
                "(gratuite sur console.groq.com) dans les variables d'environnement "
                "pour activer l'assistant conversationnel."
            ),
        }

    try:
        from groq import Groq
        client = Groq(api_key=os.environ["GROQ_API_KEY"])
        contexte = _contexte_metre(rapport)
        completion = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {"role": "system", "content": _SYSTEM},
                {"role": "user",
                 "content": f"Contexte du plan analysé :\n{contexte}\n\n"
                            f"Question : {question}"},
            ],
            temperature=0.2,
            max_tokens=800,
        )
        return {"ok": True, "reponse": completion.choices[0].message.content.strip()}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "reponse": f"Erreur de l'IA : {e}"}


def analyser_coherence(rapport: RapportAnalyse) -> dict:
    """
    Demande à l'IA une vérification de cohérence et des suggestions de matériaux.
    Renvoie {"reponse": str, "ok": bool}.
    """
    return repondre(
        "Vérifie la cohérence de ce métré, signale toute anomalie éventuelle, "
        "et propose les matériaux probables par poste (gros œuvre, second œuvre). "
        "Sois bref et structuré.",
        rapport,
    )
