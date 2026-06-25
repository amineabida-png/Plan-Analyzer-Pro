"""
Générateur d'un plan DXF de test réaliste pour Plan Analyzer Pro.
Bâtiment R+0 de 10 x 8 m avec 4 pièces entièrement cloisonnées :
  - SEJOUR        (bas-gauche)  6 x 5 = 30 m²
  - SALLE DE BAIN (haut-gauche) 6 x 3 = 18 m²
  - CHAMBRE       (bas-droite)  4 x 4 = 16 m²
  - CUISINE       (haut-droite) 4 x 4 = 16 m²
Total = 80 m² (= emprise 10 x 8). Unités : millimètres.
"""
import ezdxf

doc = ezdxf.new("R2010", setup=True)
doc.header["$INSUNITS"] = 4  # millimetres
msp = doc.modelspace()

layers = {
    "MUR_EXT": 1, "CLOISON": 3, "POTEAU": 2, "PORTE": 5,
    "FENETRE": 4, "PIECE_TXT": 7, "COTATION": 8,
}
for name, color in layers.items():
    doc.layers.add(name, color=color)

W, H = 10000, 8000

# Murs extérieurs (axe, polyligne fermée)
msp.add_lwpolyline([(0, 0), (W, 0), (W, H), (0, H)], close=True,
                   dxfattribs={"layer": "MUR_EXT"})

# Cloisons découpant 4 pièces fermées
msp.add_lwpolyline([(6000, 0), (6000, H)], dxfattribs={"layer": "CLOISON"})
msp.add_lwpolyline([(0, 5000), (6000, 5000)], dxfattribs={"layer": "CLOISON"})
msp.add_lwpolyline([(6000, 4000), (W, 4000)], dxfattribs={"layer": "CLOISON"})

def add_poteau(cx, cy, size=300):
    h = size / 2
    msp.add_lwpolyline(
        [(cx - h, cy - h), (cx + h, cy - h), (cx + h, cy + h), (cx - h, cy + h)],
        close=True, dxfattribs={"layer": "POTEAU"})

for (px, py) in [(0, 0), (W, 0), (W, H), (0, H), (6000, 0), (6000, H)]:
    add_poteau(px, py)

if "BLOC_PORTE" not in doc.blocks:
    blk = doc.blocks.new(name="BLOC_PORTE")
    blk.add_line((0, 0), (900, 0))
    blk.add_arc((0, 0), 900, 0, 90)
for (x, y) in [(2000, 5000), (6000, 2000), (6000, 6000), (3000, 0)]:
    msp.add_blockref("BLOC_PORTE", (x, y), dxfattribs={"layer": "PORTE"})

if "BLOC_FENETRE" not in doc.blocks:
    blkf = doc.blocks.new(name="BLOC_FENETRE")
    blkf.add_lwpolyline([(0, 0), (1200, 0), (1200, 200), (0, 200)], close=True)
for (x, y) in [(2000, 0), (8000, 0), (W, 2000), (W, 6000), (3000, H), (8000, H)]:
    msp.add_blockref("BLOC_FENETRE", (x, y), dxfattribs={"layer": "FENETRE"})

pieces = [
    ("SEJOUR", 3000, 2500),
    ("SALLE DE BAIN", 3000, 6500),
    ("CHAMBRE", 8000, 2000),
    ("CUISINE", 8000, 6000),
]
for (nom, x, y) in pieces:
    msp.add_text(nom, dxfattribs={"layer": "PIECE_TXT", "height": 250}
                 ).set_placement((x, y))

msp.add_text("PLAN RDC - ECHELLE 1:50 - UNITE: mm",
             dxfattribs={"layer": "COTATION", "height": 300}
             ).set_placement((0, -1000))

doc.saveas("samples/plan_rdc.dxf")
print("Plan DXF généré : samples/plan_rdc.dxf (4 pièces, 80 m²)")
