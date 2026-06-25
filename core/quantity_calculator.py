"""
Calculateur de métré de BTP QUANT AI.
Agrège les ouvrages détectés en postes quantifiés, prêts pour un DQE/devis.

Hypothèses de calcul (paramétrables) :
  - hauteur sous plafond par défaut : 2.70 m (pour surfaces de murs/cloisons)
  - section de poteau par défaut : 0.30 x 0.30 m, hauteur = HSP
  - ces hypothèses sont explicites et documentées dans les alertes du rapport,
    car ce sont les paramètres que le métreur doit valider.
"""
from __future__ import annotations
from collections import defaultdict

from .models import Ouvrage, MetreLigne, TypeOuvrage


class CalculateurMetre:
    def __init__(self, hsp_m: float = 2.70, section_poteau_m: float = 0.30):
        self.hsp = hsp_m                      # hauteur sous plafond
        self.section_poteau = section_poteau_m

    def calculer(self, ouvrages: list[Ouvrage]) -> tuple[list[MetreLigne], list[str]]:
        lignes: list[MetreLigne] = []
        alertes: list[str] = []

        # Accumulateurs par type
        long_par_type: dict[TypeOuvrage, float] = defaultdict(float)
        compte_par_type: dict[TypeOuvrage, int] = defaultdict(int)
        surface_pieces = 0.0

        for o in ouvrages:
            if o.type in (TypeOuvrage.MUR_EXT, TypeOuvrage.MUR_INT,
                          TypeOuvrage.CLOISON, TypeOuvrage.VOILE, TypeOuvrage.POUTRE):
                if o.longueur_m:
                    long_par_type[o.type] += o.longueur_m
            elif o.type == TypeOuvrage.POTEAU:
                compte_par_type[TypeOuvrage.POTEAU] += 1
            elif o.type == TypeOuvrage.PORTE:
                compte_par_type[TypeOuvrage.PORTE] += 1
            elif o.type == TypeOuvrage.FENETRE:
                compte_par_type[TypeOuvrage.FENETRE] += 1
            elif o.type == TypeOuvrage.DALLE and o.surface_m2:
                long_par_type[TypeOuvrage.DALLE] += o.surface_m2  # on stocke la surface ici

        # --- Murs extérieurs ---
        if long_par_type[TypeOuvrage.MUR_EXT] > 0:
            L = long_par_type[TypeOuvrage.MUR_EXT]
            lignes.append(MetreLigne(
                poste="Murs extérieurs - linéaire",
                type_ouvrage=TypeOuvrage.MUR_EXT,
                quantite=round(L, 2), unite="m",
                detail="Longueur d'axe cumulée",
            ))
            lignes.append(MetreLigne(
                poste="Murs extérieurs - surface (2 faces déduites)",
                type_ouvrage=TypeOuvrage.MUR_EXT,
                quantite=round(L * self.hsp, 2), unite="m2",
                detail=f"L x HSP ({self.hsp} m)",
            ))

        # --- Murs intérieurs ---
        if long_par_type[TypeOuvrage.MUR_INT] > 0:
            L = long_par_type[TypeOuvrage.MUR_INT]
            lignes.append(MetreLigne(
                poste="Murs intérieurs - linéaire",
                type_ouvrage=TypeOuvrage.MUR_INT,
                quantite=round(L, 2), unite="m",
            ))

        # --- Cloisons ---
        if long_par_type[TypeOuvrage.CLOISON] > 0:
            L = long_par_type[TypeOuvrage.CLOISON]
            lignes.append(MetreLigne(
                poste="Cloisons - linéaire",
                type_ouvrage=TypeOuvrage.CLOISON,
                quantite=round(L, 2), unite="m",
            ))
            lignes.append(MetreLigne(
                poste="Cloisons - surface",
                type_ouvrage=TypeOuvrage.CLOISON,
                quantite=round(L * self.hsp, 2), unite="m2",
                detail=f"L x HSP ({self.hsp} m)",
            ))

        # --- Poteaux : nombre + volume béton estimé ---
        n_pot = compte_par_type[TypeOuvrage.POTEAU]
        if n_pot:
            lignes.append(MetreLigne(
                poste="Poteaux - nombre",
                type_ouvrage=TypeOuvrage.POTEAU,
                quantite=n_pot, unite="U",
            ))
            vol = n_pot * (self.section_poteau ** 2) * self.hsp
            lignes.append(MetreLigne(
                poste="Poteaux - volume béton (estimé)",
                type_ouvrage=TypeOuvrage.POTEAU,
                quantite=round(vol, 3), unite="m3",
                detail=f"{n_pot} x {self.section_poteau}x{self.section_poteau}x{self.hsp} m",
            ))
            alertes.append(
                f"Volume béton poteaux basé sur section {self.section_poteau}x"
                f"{self.section_poteau} m et HSP {self.hsp} m — à valider."
            )

        # --- Portes / fenêtres ---
        if compte_par_type[TypeOuvrage.PORTE]:
            lignes.append(MetreLigne(
                poste="Portes - nombre", type_ouvrage=TypeOuvrage.PORTE,
                quantite=compte_par_type[TypeOuvrage.PORTE], unite="U",
            ))
        if compte_par_type[TypeOuvrage.FENETRE]:
            lignes.append(MetreLigne(
                poste="Fenêtres - nombre", type_ouvrage=TypeOuvrage.FENETRE,
                quantite=compte_par_type[TypeOuvrage.FENETRE], unite="U",
            ))

        # --- Dalle / surface au sol ---
        if long_par_type[TypeOuvrage.DALLE] > 0:
            lignes.append(MetreLigne(
                poste="Dalle - surface", type_ouvrage=TypeOuvrage.DALLE,
                quantite=round(long_par_type[TypeOuvrage.DALLE], 2), unite="m2",
            ))

        # --- Alerte si des ouvrages n'ont pas pu être classés ---
        n_inconnus = sum(1 for o in ouvrages if o.type == TypeOuvrage.INCONNU)
        if n_inconnus:
            alertes.append(
                f"{n_inconnus} entité(s) non classée(s) (calque non conventionnel). "
                "La couche IA pourra les reclasser ultérieurement."
            )

        return lignes, alertes
