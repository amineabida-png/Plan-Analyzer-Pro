"""
Orchestrateur principal de Plan Analyzer Pro (socle vectoriel).
Enchaîne : lecture DXF -> détection des ouvrages -> calcul du métré
et renvoie un RapportAnalyse complet, sérialisable en JSON.

Usage :
    python -m core.analyse samples/plan_rdc.dxf
"""
from __future__ import annotations
import os
import sys
import json

from .dxf_reader import LectureDXF
from .element_detector import DetecteurOuvrages
from .room_detector import DetecteurPieces
from .quantity_calculator import CalculateurMetre
from .dwg_converter import convertir_dwg_en_dxf
from .models import RapportAnalyse


def analyser_fichier(chemin: str, hsp_m: float = 2.70) -> RapportAnalyse:
    """
    Analyse un fichier DWG ou DXF.
    Si le fichier est un DWG, il est d'abord converti en DXF automatiquement.
    """
    ext = os.path.splitext(chemin)[1].lower()
    if ext == ".dwg":
        chemin_dxf = convertir_dwg_en_dxf(chemin)
        rapport = analyser_dxf(chemin_dxf, hsp_m=hsp_m)
        # On conserve le nom du fichier d'origine dans le rapport
        rapport.fichier = chemin
        return rapport
    return analyser_dxf(chemin, hsp_m=hsp_m)


def analyser_dxf(chemin: str, hsp_m: float = 2.70) -> RapportAnalyse:
    """Pipeline complet d'analyse d'un fichier DXF."""
    # 1. Lecture
    lecture = LectureDXF(chemin)

    # 2. Détection des ouvrages
    detecteur = DetecteurOuvrages(facteur_vers_metre=lecture.facteur_vers_metre)
    ouvrages = detecteur.detecter(
        polylignes=lecture.polylignes(),
        lignes=lecture.lignes(),
        inserts=lecture.inserts(),
        textes=lecture.textes(),
    )

    # 3. Détection des pièces (fermeture topologique des murs)
    detecteur_pieces = DetecteurPieces(
        facteur_vers_metre=lecture.facteur_vers_metre, hsp_m=hsp_m)
    pieces = detecteur_pieces.detecter(ouvrages)

    # 4. Métré
    calc = CalculateurMetre(hsp_m=hsp_m)
    metre, alertes = calc.calculer(ouvrages, pieces)

    return RapportAnalyse(
        fichier=chemin,
        unite_dessin=lecture.unite,
        facteur_vers_metre=lecture.facteur_vers_metre,
        nb_calques=len(lecture.calques()),
        calques=lecture.calques(),
        ouvrages=ouvrages,
        pieces=pieces,
        metre=metre,
        alertes=alertes,
    )


def _afficher(rapport: RapportAnalyse) -> None:
    """Affichage console lisible du rapport."""
    print("=" * 62)
    print("  Plan Analyzer Pro — RAPPORT D'ANALYSE (socle vectoriel DXF)")
    print("=" * 62)
    print(f"Fichier        : {rapport.fichier}")
    print(f"Unité détectée : {rapport.unite_dessin.value} "
          f"(x{rapport.facteur_vers_metre} -> mètre)")
    print(f"Calques ({rapport.nb_calques}) : {', '.join(rapport.calques)}")
    print(f"Ouvrages détectés : {len(rapport.ouvrages)}")
    if rapport.pieces:
        print("-" * 62)
        print(f"PIÈCES DÉTECTÉES ({len(rapport.pieces)})")
        print("-" * 62)
        print(f"{'Pièce':<22}{'Surface':>10} {'Périmètre':>11} {'Peinture':>10}")
        for p in rapport.pieces:
            print(f"{p.nom:<22}{p.surface_m2:>8.2f}m²{p.perimetre_m:>9.2f}m"
                  f"{p.surface_peinture_murs_m2:>8.2f}m²")
    print("-" * 62)
    print("MÉTRÉ")
    print("-" * 62)
    print(f"{'Poste':<42}{'Quantité':>11} {'Unité':<5}")
    print("-" * 62)
    for l in rapport.metre:
        print(f"{l.poste:<42}{l.quantite:>11.2f} {l.unite:<5}")
        if l.detail:
            print(f"    └─ {l.detail}")
    if rapport.alertes:
        print("-" * 62)
        print("ALERTES (à valider par le métreur) :")
        for a in rapport.alertes:
            print(f"  ⚠ {a}")
    print("=" * 62)


if __name__ == "__main__":
    chemin = sys.argv[1] if len(sys.argv) > 1 else "samples/plan_rdc.dxf"
    rapport = analyser_dxf(chemin)
    _afficher(rapport)

    # Export JSON
    os.makedirs("outputs", exist_ok=True)
    sortie = "outputs/rapport.json"
    with open(sortie, "w", encoding="utf-8") as f:
        json.dump(rapport.model_dump(), f, ensure_ascii=False, indent=2)
    print(f"\nRapport JSON exporté : {sortie}")
