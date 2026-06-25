"""
Convertisseur DWG -> DXF en lot (à lancer en local sur Windows).

Usage :
    python tools/convertir_dwg.py "C:\\chemin\\vers\\dossier_dwg"

Convertit tous les .dwg d'un dossier en .dxf, prêts à importer dans
Plan Analyzer Pro. Nécessite « ODA File Converter » installé (gratuit) :
    https://www.opendesign.com/guestfiles/oda_file_converter

Les .dxf sont écrits dans un sous-dossier « _dxf ».
"""
import os
import sys


def main():
    if len(sys.argv) < 2:
        print("Usage : python tools/convertir_dwg.py <dossier_contenant_les_dwg>")
        sys.exit(1)

    dossier = sys.argv[1]
    if not os.path.isdir(dossier):
        print(f"Dossier introuvable : {dossier}")
        sys.exit(1)

    try:
        from ezdxf.addons import odafc
    except ImportError:
        print("ezdxf n'est pas installé. Lancez d'abord : pip install -r requirements.txt")
        sys.exit(1)

    if not odafc.is_installed():
        print("ODA File Converter introuvable.")
        print("Téléchargez-le (gratuit) : "
              "https://www.opendesign.com/guestfiles/oda_file_converter")
        sys.exit(1)

    sortie = os.path.join(dossier, "_dxf")
    os.makedirs(sortie, exist_ok=True)

    dwgs = [f for f in os.listdir(dossier) if f.lower().endswith(".dwg")]
    if not dwgs:
        print("Aucun fichier .dwg trouvé dans ce dossier.")
        return

    print(f"{len(dwgs)} fichier(s) DWG à convertir...\n")
    ok, ko = 0, 0
    for nom in dwgs:
        src = os.path.join(dossier, nom)
        dst = os.path.join(sortie, os.path.splitext(nom)[0] + ".dxf")
        try:
            odafc.convert(src, dst, version="R2018")
            print(f"  OK  {nom}  ->  _dxf\\{os.path.basename(dst)}")
            ok += 1
        except Exception as e:  # noqa: BLE001
            print(f"  ERREUR  {nom} : {e}")
            ko += 1

    print(f"\nTerminé : {ok} converti(s), {ko} échec(s).")
    print(f"Les DXF sont dans : {sortie}")


if __name__ == "__main__":
    main()
