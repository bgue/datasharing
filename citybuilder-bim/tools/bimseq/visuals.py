"""Visual hint and stand-in mesh size helpers shared by extractor, generators and scheduler."""
from __future__ import annotations

from typing import Any, Mapping

VISUALS = frozenset([
    "slab", "wall", "column", "beam", "footing", "pile", "roof", "stair", "door", "window",
    "curtain_wall", "ceiling", "floor_finish", "duct", "pipe", "cable_tray", "equipment",
    "terminal", "tank", "earthwork", "pavement", "kerb", "barrier", "deck", "pier", "culvert",
    "sign", "generic",
])

_CLASS_VISUAL = {
    "IfcSlab": "slab", "IfcWall": "wall", "IfcWallStandardCase": "wall", "IfcColumn": "column",
    "IfcBeam": "beam", "IfcMember": "beam", "IfcFooting": "footing", "IfcPile": "pile",
    "IfcDeepFoundation": "pile", "IfcRoof": "roof", "IfcStair": "stair", "IfcStairFlight": "stair",
    "IfcRamp": "slab", "IfcRampFlight": "slab", "IfcDoor": "door", "IfcWindow": "window",
    "IfcCurtainWall": "curtain_wall", "IfcCovering": "ceiling", "IfcRailing": "barrier",
    "IfcPlate": "slab", "IfcDuctSegment": "duct", "IfcDuctFitting": "duct",
    "IfcPipeSegment": "pipe", "IfcPipeFitting": "pipe", "IfcCableCarrierSegment": "cable_tray",
    "IfcCableCarrierFitting": "cable_tray", "IfcCableSegment": "cable_tray",
    "IfcAirTerminal": "terminal", "IfcSanitaryTerminal": "terminal", "IfcLightFixture": "terminal",
    "IfcFlowTerminal": "terminal", "IfcFlowController": "equipment",
    "IfcFlowMovingDevice": "equipment", "IfcEnergyConversionDevice": "equipment",
    "IfcElectricDistributionBoard": "equipment", "IfcMedicalDevice": "equipment",
    "IfcTransformer": "equipment", "IfcFlowStorageDevice": "tank", "IfcTank": "tank",
    "IfcChimney": "column", "IfcDistributionChamberElement": "culvert",
    "IfcEarthworksCut": "earthwork", "IfcEarthworksFill": "earthwork", "IfcPavement": "pavement",
    "IfcCourse": "pavement", "IfcKerb": "kerb", "IfcBearing": "equipment", "IfcSign": "sign",
    "IfcBuildingElementProxy": "equipment", "IfcDistributionElement": "equipment",
}

# Default stand-in size [w, h, d] metres per visual when no bbox is available.
_DEFAULT_HEIGHT = {
    "slab": 0.3, "wall": 3.0, "column": 3.6, "beam": 0.5, "footing": 0.6, "pile": 6.0, "roof": 0.4,
    "stair": 3.0, "door": 2.1, "window": 1.5, "curtain_wall": 3.6, "ceiling": 0.1,
    "floor_finish": 0.05, "duct": 0.4, "pipe": 0.3, "cable_tray": 0.15, "equipment": 2.5,
    "terminal": 0.3, "tank": 6.0, "earthwork": 1.0, "pavement": 0.2, "kerb": 0.2, "barrier": 1.0,
    "deck": 0.5, "pier": 5.0, "culvert": 2.0, "sign": 3.0, "generic": 1.0,
}


def visual_for(ifc_class: str, properties: Mapping[str, Any] | None = None,
               predefined_type: str | None = None, name: str | None = None) -> str:
    """Visual hint for an IFC class, refined by predefined type, name and known properties."""
    props = properties or {}
    part = props.get("BridgePart")
    if part == "pier":
        return "pier"
    if part == "deck":
        return "deck"
    if part == "girder":
        return "beam"
    if ifc_class == "IfcBuildingElementProxy" and "culvert" in (name or "").lower():
        return "culvert"
    if ifc_class == "IfcCovering" and (predefined_type or "").upper() == "FLOORING":
        return "floor_finish"
    return _CLASS_VISUAL.get(ifc_class, "generic")


def size_hint(element: Mapping[str, Any], visual: str, cell_size_m: float,
              storey_height_m: float) -> list[float]:
    """Approximate [w, h, d] (metres) of the stand-in mesh for a light element.

    Uses the bounding box when present (model x, y-north, z-up -> w, d, h), otherwise the
    footprint of the cells with a per-visual default height.
    """
    bbox = element.get("bbox")
    if bbox and "min" in bbox and "max" in bbox:
        lo, hi = bbox["min"], bbox["max"]
        w, d, h = (max(0.05, hi[0] - lo[0]), max(0.05, hi[1] - lo[1]), max(0.05, hi[2] - lo[2]))
        return [round(w, 2), round(h, 2), round(d, 2)]
    cells = element.get("cells") or [[0, 0]]
    xs = [c[0] for c in cells]
    zs = [c[1] for c in cells]
    w = (max(xs) - min(xs) + 1) * cell_size_m
    d = (max(zs) - min(zs) + 1) * cell_size_m
    h = _DEFAULT_HEIGHT.get(visual, 1.0)
    if visual in ("column", "wall", "curtain_wall"):
        h = storey_height_m * 0.9
    if visual in ("column", "pile", "pier", "pipe", "duct", "cable_tray", "terminal", "equipment",
                  "tank", "door", "window", "sign", "barrier"):
        w, d = min(w, cell_size_m * 0.4), min(d, cell_size_m * 0.4)
    if visual == "wall":
        w, d = (w, 0.3) if w >= d else (0.3, d)
    return [round(w, 2), round(h, 2), round(d, 2)]
