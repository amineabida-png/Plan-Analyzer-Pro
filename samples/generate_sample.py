"""
Générateur d'un plan DXF de test réaliste pour BTP QUANT AI.
Crée un petit bâtiment R+0 d'environ 10 x 8 m avec :
 - murs extérieurs et cloisons
 - poteaux
 - portes et fenêtres (blocs)
 - étiquettes de pièces (texte)
Unités : millimètres (convention DAO la plus courante).
"""
import ezdxf

# $INSUNITS = 4  -> millimètres
doc = ezdxf.new("R2010", setup=True)
doc.header["$INSUNITS"] = 4  # millimetres
msp = doc.modelspace()

# --- Calques normalisés (convention BTP) ---
layers = {
    "MUR_EXT":   {"color": 1},   # murs porteurs extérieurs
    "CLOISON":   {"color": 3},   # cloisons intérieures
    "POTEAU":    {"color": 2},   # poteaux béton
    "PORTE":     {"color": 5},   # portes
    "FENETRE":   {"color": 4},   # fenêtres
    "PIECE_TXT": {"color": 7},   # noms de pièces
    "COTATION":  {"color": 8},   # cotes / texte
}
for name, prop in layers.items():
    doc.layers.add(name, color=prop["color"])

# Dimensions du bâtiment (en mm)
W, H = 10000, 8000      # 10 m x 8 m hors-tout (axe murs)
EP_EXT = 200            # épaisseur mur extérieur 20 cm
EP_CLOISON = 100        # épaisseur cloison 10 cm

# --- Murs extérieurs : on les représente par leur AXE (polyligne fermée) ---
# La plupart des plans tracent l'axe ou le nu ; ici on prend l'axe.
ext = msp.add_lwpolyline(
    [(0, 0), (W, 0), (W, H), (0, H)],
    close=True,
    dxfattribs={"layer": "MUR_EXT"},
)

# --- Cloison intérieure qui sépare le séjour de la chambre (verticale à x=6000) ---
msp.add_lwpolyline(
    [(6000, 0), (6000, 5000)],
    dxfattribs={"layer": "CLOISON"},
)
# Cloison horizontale séparant la cuisine (à y=5000, de x=6000 à x=10000)
msp.add_lwpolyline(
    [(6000, 5000), (W, 5000)],
    dxfattribs={"layer": "CLOISON"},
)

# --- Poteaux béton 30x30 aux 4 angles (représentés par des rectangles fermés) ---
def add_poteau(cx, cy, size=300):
    h = size / 2
    msp.add_lwpolyline(
        [(cx - h, cy - h), (cx + h, cy - h), (cx + h, cy + h), (cx - h, cy + h)],
        close=True,
        dxfattribs={"layer": "POTEAU"},
    )

for (px, py) in [(0, 0), (W, 0), (W, H), (0, H), (6000, 5000)]:
    add_poteau(px, py)

# --- Portes (blocs INSERT). On crée un bloc "PORTE" simple. ---
if "PORTE" not in doc.blocks:
    blk = doc.blocks.new(name="BLOC_PORTE")
    blk.add_line((0, 0), (900, 0))           # largeur de passage 90 cm
    blk.add_arc((0, 0), 900, 0, 90)          # battant
# Insertions de portes
porte_positions = [(1000, 0), (6000, 2000), (7500, 5000)]
for (x, y) in porte_positions:
    msp.add_blockref("BLOC_PORTE", (x, y), dxfattribs={"layer": "PORTE"})

# --- Fenêtres (blocs INSERT) ---
if "BLOC_FENETRE" not in doc.blocks:
    blkf = doc.blocks.new(name="BLOC_FENETRE")
    blkf.add_lwpolyline([(0, 0), (1200, 0), (1200, 200), (0, 200)], close=True)
fenetre_positions = [(2000, 0), (4000, 0), (10000, 2000), (10000, 6000), (3000, 8000)]
for (x, y) in fenetre_positions:
    msp.add_blockref("BLOC_FENETRE", (x, y), dxfattribs={"layer": "FENETRE"})

# --- Étiquettes de pièces (texte) avec surface indicative ---
pieces = [
    ("SEJOUR", 3000, 2500),
    ("CHAMBRE", 8000, 2500),
    ("CUISINE", 8000, 6500),
    ("SALLE DE BAIN", 3000, 6500),
]
for (nom, x, y) in pieces:
    msp.add_text(
        nom,
        dxfattribs={"layer": "PIECE_TXT", "height": 250},
    ).set_placement((x, y))

# --- Cartouche minimal : échelle + titre (texte) ---
msp.add_text(
    "PLAN RDC - ECHELLE 1:50 - UNITE: mm",
    dxfattribs={"layer": "COTATION", "height": 300},
).set_placement((0, -1000))

doc.saveas("/home/claude/btp_quant_ai/samples/plan_rdc.dxf")
print("Plan DXF généré : samples/plan_rdc.dxf")
