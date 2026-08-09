"""Harmonic damper / crank pulley: rides the crankshaft snout.

Local frame: the axis is Z with the hub's engagement face at z = 0; the
assembly places it at the snout with rotation (90, 0, 0), so local +Z runs
out the front of the engine (global -Y). The bore slips over the crank's
28 mm snout with running clearance; the damper ring carries two V-belt
grooves and a center-bolt washer caps the snout.
"""

from build123d import *

PARAMS = {
    "diameter": {"default": 96.0, "min": 80.0, "max": 110.0, "unit": "mm",
                 "description": "Damper ring outer diameter"},
    "ring_w": {"default": 10.0, "min": 8.0, "max": 14.0, "unit": "mm",
               "description": "Damper ring width"},
    "hub_d": {"default": 44.0, "min": 36.0, "max": 52.0, "unit": "mm",
              "description": "Hub diameter"},
    "bore_d": {"default": 28.6, "min": 24.0, "max": 34.0, "unit": "mm",
               "description": "Snout bore (crank snout 28 + clearance)"},
}


def build(p):
    hub_l = 12.6
    ring0 = 5.6                        # damper ring start along the hub

    pulley = Pos(0, 0, hub_l / 2) * Cylinder(radius=p.hub_d / 2, height=hub_l)
    pulley += Pos(0, 0, ring0 + p.ring_w / 2) * Cylinder(
        radius=p.diameter / 2, height=p.ring_w)
    # inertia-ring web relief
    relief = (Cylinder(radius=p.diameter / 2 - 8, height=3)
              - Cylinder(radius=p.hub_d / 2 + 6, height=4))
    pulley -= Pos(0, 0, ring0 + p.ring_w - 1.0) * relief

    # two V-belt grooves in the rim
    for gz in (ring0 + 0.3 * p.ring_w, ring0 + 0.7 * p.ring_w):
        groove = (Cylinder(radius=p.diameter / 2 + 1, height=2.4)
                  - Cylinder(radius=p.diameter / 2 - 3, height=2.6))
        pulley -= Pos(0, 0, gz) * groove

    # snout bore and the center-bolt washer capping it
    pulley -= Pos(0, 0, 2.0) * Cylinder(radius=p.bore_d / 2, height=12)
    pulley += Pos(0, 0, hub_l - 2.5) * Cylinder(radius=12, height=5)
    pulley += Pos(0, 0, hub_l + 2.5) * Cylinder(radius=7, height=6,
                                                rotation=(0, 0, 30))

    return pulley
