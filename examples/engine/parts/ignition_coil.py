"""Coil-on-plug ignition coil: seats on a head's spark-plug tube.

Local frame: the mounting face is z = 0 (it sits 0.5 mm above the tube rim)
with the coil body extending +Z and the connector lug reaching sideways along
+Y. Four instances dress the engine, one per plug tube, each canted to its
bank angle.
"""

from build123d import *

PARAMS = {
    "body_d": {"default": 30.0, "min": 26.0, "max": 34.0, "unit": "mm",
               "description": "Coil body diameter over the plug tube"},
    "cap_d": {"default": 34.0, "min": 28.0, "max": 38.0, "unit": "mm",
              "description": "Top cap diameter"},
    "height": {"default": 22.0, "min": 18.0, "max": 30.0, "unit": "mm",
               "description": "Body height under the cap"},
    "connector_l": {"default": 20.0, "min": 14.0, "max": 26.0, "unit": "mm",
                    "description": "Connector lug length"},
}


def build(p):
    coil = Pos(0, 0, p.height / 2) * Cylinder(radius=p.body_d / 2,
                                              height=p.height)
    coil += Pos(0, 0, p.height + 5) * Cylinder(radius=p.cap_d / 2, height=10)
    # connector lug with a small locking-tab ridge
    ly = p.body_d / 2 + p.connector_l / 2 - 2
    coil += Pos(0, ly, p.height + 4) * Box(16, p.connector_l + 4, 12)
    coil += Pos(0, ly + 2, p.height + 11) * Box(8, p.connector_l - 4, 3)
    # hold-down ear with its bolt hole
    coil += Pos(0, -p.body_d / 2 - 6, 2.5) * Box(14, 16, 5)
    coil -= Pos(0, -p.body_d / 2 - 9, 2.5) * Cylinder(radius=3, height=8)
    top = coil.edges().filter_by(GeomType.CIRCLE).group_by(Axis.Z)[-1]
    return chamfer(top, length=1.0)
