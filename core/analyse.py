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
from . import ai_groq
from .models import RapportAnalyse

HSP_DEFAUT = 2.70


def analyser_fichier(chemin: str, hsp_m: float | None = None,
                     utiliser_ia: bool = True,
                     mapping_manuel: dict | None = None,
                     echelle_pdf: int | None = None) -> RapportAnalyse:
    """
    Analyse un fichier DWG, DXF ou PDF.
    - DWG : converti d'abord en DXF.
    - PDF : lu en vectoriel ou (si scanné) par OCR + vision. echelle_pdf = N
            pour une échelle 1:N (sinon détectée ou approximée).
    """
    ext = os.path.splitext(chemin)[1].lower()
    if ext == ".dwg":
        chemin_dxf = convertir_dwg_en_dxf(chemin)
        rapport = analyser_dxf(chemin_dxf, hsp_m=hsp_m, utiliser_ia=utiliser_ia,
                               mapping_manuel=mapping_manuel)
        rapport.fichier = chemin
        return rapport
    if ext == ".pdf":
        return analyser_pdf(chemin, hsp_m=hsp_m, mapping_manuel=mapping_manuel,
                            echelle=echelle_pdf)
    return analyser_dxf(chemin, hsp_m=hsp_m, utiliser_ia=utiliser_ia,
                        mapping_manuel=mapping_manuel)


def analyser_pdf(chemin: str, hsp_m: float | None = None,
                 mapping_manuel: dict | None = None,
                 echelle: int | None = None) -> RapportAnalyse:
    """Analyse un PDF de plan (vectoriel ou scanné)."""
    from .pdf_reader import LecturePDF
    lecture = LecturePDF(chemin, echelle=echelle)
    rapport = _analyser_lecteur(lecture, hsp_m=hsp_m, utiliser_ia=False,
                                mapping_manuel=mapping_manuel)
    # Alertes spécifiques au PDF (fiabilité, échelle)
    for a in lecture.alertes:
        rapport.alertes.insert(0, a)
    return rapport


def analyser_dxf(chemin: str, hsp_m: float | None = None,
                 utiliser_ia: bool = True,
                 mapping_manuel: dict | None = None) -> RapportAnalyse:
    """Pipeline complet d'analyse d'un fichier DXF."""
    lecture = LectureDXF(chemin)
    return _analyser_lecteur(lecture, hsp_m=hsp_m, utiliser_ia=utiliser_ia,
                             mapping_manuel=mapping_manuel)


def _analyser_lecteur(lecture, hsp_m: float | None = None,
                      utiliser_ia: bool = True,
                      mapping_manuel: dict | None = None) -> RapportAnalyse:
    """
    Moteur d'analyse partagé : fonctionne avec tout lecteur exposant la même
    interface (LectureDXF, LecturePDF). Détecte pièces + surfaces, et le métré
    complet en mode affiné.
    """
    # 1. Lecture
    chemin = getattr(lecture, "chemin", "plan")
    calques = lecture.calques()
    calques_detail = lecture.resume_calques()

    # 1b. Hauteur sous plafond : fournie > détectée sur le plan > défaut
    hsp_detectee = False
    if hsp_m is None:
        hsp_lue = lecture.detecter_hsp()
        if hsp_lue is not None:
            hsp_m = hsp_lue
            hsp_detectee = True
        else:
            hsp_m = HSP_DEFAUT

    # 2. Mode de fonctionnement :
    #    - AUTO (pas de mapping manuel) : on n'utilise PAS la classification par
    #      noms pour le métré (elle est trop peu fiable et produit des chiffres
    #      aberrants). On se limite à ce qui est fiable : pièces + surfaces.
    #      L'IA sert seulement à pré-remplir le tableau optionnel.
    #    - AFFINÉ (mapping manuel fourni) : l'utilisateur a classé les calques,
    #      on calcule alors le métré complet (murs, portes, fenêtres, poteaux).
    mode_affine = bool(mapping_manuel)
    mapping = {}            # mapping réellement utilisé pour le métré
    suggestions_ia = {}     # suggestions IA pour le tableau (n'affectent pas le métré)
    message_ia = ""
    if mode_affine:
        mapping = {k: v for k, v in mapping_manuel.items() if v and v != "autre"}
        message_ia = f"{len(mapping)} calque(s) classé(s) manuellement."
    elif utiliser_ia and ai_groq.ia_disponible():
        res = ai_groq.mapper_calques(calques)
        suggestions_ia = res.get("mapping", {})  # juste des suggestions

    polylignes = lecture.polylignes()
    lignes_geo = lecture.lignes()
    textes_geo = lecture.textes()

    # 3. Détection des ouvrages : UNIQUEMENT en mode affiné (avec mapping validé).
    #    En mode auto, on ne classe pas d'ouvrages (évite portes/murs fantômes).
    ouvrages = []
    if mode_affine:
        detecteur = DetecteurOuvrages(
            facteur_vers_metre=lecture.facteur_vers_metre, mapping_calques=mapping)
        ouvrages = detecteur.detecter(
            polylignes=polylignes, lignes=lignes_geo,
            inserts=lecture.inserts(), textes=textes_geo,
        )

    # 4. Détection des pièces
    detecteur_pieces = DetecteurPieces(
        facteur_vers_metre=lecture.facteur_vers_metre, hsp_m=hsp_m)
    # 4a. En mode affiné, via les murs classés ; sinon repli auto plus bas
    pieces = detecteur_pieces.detecter(ouvrages) if mode_affine else []
    methode_pieces = "calques"
    # 4b. Détection AUTOMATIQUE par géométrie (mode auto, ou si rien trouvé)
    if not pieces:
        pieces = detecteur_pieces.detecter_auto(polylignes, lignes_geo, textes_geo)
        methode_pieces = "auto-géométrie"
    # 4c. Associer les noms de pièces à partir de TOUS les textes (tous calques)
    from .room_detector import associer_noms
    associer_noms(pieces, textes_geo, lecture.facteur_vers_metre)

    # 5. Métré
    calc = CalculateurMetre(hsp_m=hsp_m)
    metre, alertes = calc.calculer(ouvrages, pieces)

    # Alertes sur le HSP et l'IA
    if hsp_detectee:
        alertes.insert(0, f"Hauteur sous plafond lue sur le plan : {hsp_m} m.")
    else:
        alertes.insert(0, f"Hauteur sous plafond non trouvée sur le plan : "
                          f"valeur par défaut {hsp_m} m utilisée — à ajuster si besoin.")
    if message_ia:
        alertes.append(message_ia)
    if pieces:
        if methode_pieces == "auto-géométrie":
            alertes.insert(0,
                f"{len(pieces)} pièce(s) détectée(s) AUTOMATIQUEMENT par géométrie "
                "(sans classification). Vérifiez d'un coup d'œil.")
        else:
            alertes.append(f"{len(pieces)} pièce(s) détectée(s) via les calques classés.")
    else:
        alertes.append(
            "Aucune pièce détectée : plan au tracé inhabituel. Essayez de classer "
            "les calques 'mur' dans le tableau.")
    if not mode_affine:
        alertes.append(
            "Mode automatique : seules les SURFACES sont affichées (fiables). "
            "Pour obtenir les longueurs de murs, portes, fenêtres et poteaux, "
            "classez les calques correspondants via « Affiner » puis recalculez.")

    return RapportAnalyse(
        fichier=chemin,
        unite_dessin=lecture.unite,
        facteur_vers_metre=lecture.facteur_vers_metre,
        nb_calques=len(calques),
        calques=calques,
        calques_detail=calques_detail,
        ouvrages=ouvrages,
        pieces=pieces,
        hsp_m=hsp_m,
        hsp_detectee=hsp_detectee,
        mapping_ia=(mapping if mode_affine else suggestions_ia),
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
