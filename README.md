# Plan Analyzer Pro

Analyse automatique de plans de construction **vectoriels (DXF)** et génération de
**métré** fiable, exposée via une API web FastAPI.
100 % open source — aucune dépendance payante.

---

## Sommaire

1. [Ce que fait l'application](#1-ce-que-fait-lapplication)
2. [Lancer en local (Windows CMD)](#2-lancer-en-local-windows-cmd)
3. [Mettre sur GitHub](#3-mettre-sur-github)
4. [Déployer sur Railway](#4-déployer-sur-railway)
5. [Structure du projet](#5-structure-du-projet)
6. [API](#6-api)

---

## 1. Ce que fait l'application

- Importe un fichier **.dxf** ou **.dwg** (DWG converti automatiquement)
- Détecte automatiquement l'**unité** du dessin et les **calques**
- Classe les **ouvrages** (murs ext., cloisons, poteaux, portes, fenêtres, pièces)
- **Détecte les pièces** par fermeture topologique des murs (Shapely) et calcule
  leurs **surfaces** : habitable, carrelage, plafond, peinture murs
- Calcule un **métré** (longueurs, surfaces, comptages, volume béton estimé)
- Chiffrage en **dirham marocain (DH)**
- Export **Excel** (feuilles Métré + Pièces, avec formules) + rapport **JSON**
- Interface web d'upload + API REST

> Étape actuelle : socle **vectoriel** (DXF + DWG). Les PDF/scans et l'IA Groq
> arrivent dans les étapes suivantes.

### Lecture des fichiers DWG

Le DWG est un format propriétaire ; sa lecture passe par une conversion en DXF
via **ODA File Converter** (gratuit). Deux cas :

**a) En local (recommandé pour le DWG)** — installez ODA File Converter une fois :
https://www.opendesign.com/guestfiles/oda_file_converter
Ensuite, glissez directement un `.dwg` dans l'application : la conversion est
automatique. Vous pouvez aussi convertir un dossier entier en lot :

```cmd
python tools\convertir_dwg.py "C:\chemin\vers\dossier_dwg"
```

(ou double-cliquez sur `tools\convertir_dwg.bat`)

**b) Sur Railway (cloud)** — l'hébergement ne dispose pas du convertisseur :
convertissez vos `.dwg` en `.dxf` en local (méthode ci-dessus), puis importez
le `.dxf`. Les `.dxf` fonctionnent partout, directement.

> L'interface affiche automatiquement si la conversion DWG est active
> (endpoint `/api/capabilities`).

---

## 2. Lancer en local (Windows CMD)

> Prérequis : **Python 3.12** installé et coché « Add to PATH ».
> Vérifier : `python --version`

Après avoir décompressé le ZIP, ouvrez **CMD** dans le dossier `plan-analyzer-pro` :

```cmd
cd chemin\vers\plan-analyzer-pro

:: 1. Créer un environnement virtuel
python -m venv .venv

:: 2. L'activer
.venv\Scripts\activate

:: 3. Installer les dépendances
pip install -r requirements.txt

:: 4. (Optionnel) régénérer le plan DXF de test
python samples\generate_sample.py

:: 5. Lancer le serveur
uvicorn main:app --reload
```

Ouvrez ensuite **http://127.0.0.1:8000** dans le navigateur, glissez le fichier
`samples\plan_rdc.dxf`, et cliquez sur **Analyser le plan**.

Pour tester le moteur seul, sans serveur :

```cmd
python -m core.analyse samples\plan_rdc.dxf
```

---

## 3. Mettre sur GitHub

> Prérequis : **Git** installé (`git --version`) et un compte GitHub.

Dans CMD, toujours dans le dossier `plan-analyzer-pro` :

```cmd
:: 1. Initialiser le dépôt
git init
git add .
git commit -m "Plan Analyzer Pro - detection des pieces"

:: 2. Créer un dépôt vide sur github.com (bouton "New repository"),
::    puis relier votre dossier (remplacez l'URL par la vôtre) :
git remote add origin https://github.com/VOTRE_COMPTE/plan-analyzer-pro.git
git branch -M main
git push -u origin main
```

> Astuce : le `.gitignore` exclut déjà `.venv`, `.env`, `__pycache__` et les sorties.

---

## 4. Déployer sur Railway

> Railway détecte automatiquement Python et lit `railway.json` / `Procfile`.
> Aucune configuration manuelle de build n'est nécessaire.

### Méthode A — via l'interface web (recommandée)

1. Allez sur **https://railway.app** et connectez-vous avec GitHub.
2. **New Project** → **Deploy from GitHub repo** → choisissez `plan-analyzer-pro`.
3. Railway installe `requirements.txt` et lance la commande de `railway.json` :
   `uvicorn main:app --host 0.0.0.0 --port $PORT`
4. Une fois déployé : onglet **Settings** → **Networking** → **Generate Domain**
   pour obtenir une URL publique (ex. `https://plan-analyzer-pro.up.railway.app`).
5. Testez `https://VOTRE-URL/health` → doit renvoyer `{"status":"ok"}`.

### Méthode B — via la CLI Railway (dans CMD)

```cmd
:: Installer la CLI (nécessite Node.js)
npm i -g @railway/cli

:: Se connecter
railway login

:: Depuis le dossier du projet, lier puis déployer
railway init
railway up
```

### Variables d'environnement

Aucune n'est obligatoire pour ce socle. La variable `PORT` est fournie
**automatiquement** par Railway. (La `GROQ_API_KEY` ne servira qu'à l'étape IA.)

---

## 5. Structure du projet

```
plan-analyzer-pro/
├── main.py                      API FastAPI (point d'entrée Railway)
├── core/                        Moteur d'analyse
│   ├── dxf_reader.py            Lecture DXF (unité, calques, géométrie)
│   ├── element_detector.py      Classification des ouvrages
│   ├── quantity_calculator.py   Calcul du métré
│   ├── export_excel.py          Export Excel
│   ├── room_detector.py         Détection des pièces (Shapely)
│   ├── dwg_converter.py         Conversion DWG -> DXF (ODA / LibreDWG)
│   ├── analyse.py               Orchestrateur du pipeline
│   └── models.py                Modèles de données typés
├── static/index.html           Interface web d'upload
├── tools/                       Convertisseur DWG en lot (local Windows)
├── samples/                     Plan DXF de test + générateur
├── requirements.txt            Dépendances Python
├── Procfile                    Commande de démarrage
├── railway.json                Config Railway
├── runtime.txt                 Version Python
├── .gitignore
└── .env.example
```

---

## 6. API

| Méthode | Route                  | Description                            |
|---------|------------------------|----------------------------------------|
| GET     | `/`                    | Interface web d'upload                 |
| GET     | `/health`              | Sonde de santé (Railway)               |
| POST    | `/api/analyze`         | Analyse un DXF/DWG → métré JSON        |
| POST    | `/api/analyze/excel`   | Analyse un DXF/DWG → fichier Excel    |
| GET     | `/api/capabilities`    | Indique si la conversion DWG est active |

Exemple (curl) :

```cmd
curl -X POST "http://127.0.0.1:8000/api/analyze?hsp=2.70" -F "fichier=@samples\plan_rdc.dxf"
```

Documentation interactive auto-générée : **http://127.0.0.1:8000/docs**

---

## Licences

Toutes les dépendances sont open source (MIT / BSD / Apache 2.0), usage commercial autorisé.
