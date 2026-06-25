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


# Catégories cibles pour le mapping de calques
_CATEGORIES = [
    "mur_exterieur", "mur_interieur", "cloison", "poteau", "poutre",
    "voile", "dalle", "porte", "fenetre", "piece", "autre",
]


def mapper_calques(calques: list[str]) -> dict:
    """
    Utilise l'IA pour associer chaque NOM DE CALQUE d'un vrai plan à une
    catégorie d'ouvrage. Indispensable car les plans réels n'utilisent pas de
    conventions de nommage standard.

    Renvoie {"ok": bool, "mapping": {nom_calque: categorie}, "message": str}.
    Sans clé Groq, renvoie ok=False et un mapping vide (le moteur retombe alors
    sur ses règles par mots-clés).
    """
    if not ia_disponible():
        return {"ok": False, "mapping": {}, "message": "IA non configurée."}

    try:
        import json as _json
        from groq import Groq
        client = Groq(api_key=os.environ["GROQ_API_KEY"])
        liste = "\n".join(f"- {c}" for c in calques)
        prompt = (
            "Voici les noms de calques d'un plan de bâtiment (DXF). Pour chacun, "
            "donne la catégorie d'ouvrage la plus probable parmi exactement : "
            f"{', '.join(_CATEGORIES)}.\n"
            "Réponds UNIQUEMENT par un objet JSON {nom_calque: categorie}, sans "
            "texte autour.\n\nCalques :\n" + liste
        )
        completion = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {"role": "system",
                 "content": "Tu es un expert BTP qui classe des calques de plans "
                            "DAO. Tu réponds uniquement en JSON valide."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.0,
            max_tokens=1500,
            response_format={"type": "json_object"},
        )
        brut = completion.choices[0].message.content.strip()
        mapping = _json.loads(brut)
        # Ne garde que les catégories valides
        mapping = {k: v for k, v in mapping.items()
                   if v in _CATEGORIES and v != "autre"}
        return {"ok": True, "mapping": mapping,
                "message": f"{len(mapping)} calque(s) classé(s) par l'IA."}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "mapping": {}, "message": f"Erreur IA : {e}"}
