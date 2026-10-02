class_name SiteTile
extends Resource
## A placeable site-logistics tile (replaces the Kenney `Structure` resource).
## Either `model` (a Kenney glb PackedScene) or a code-built box (`box_size`) is used.

@export var tile_id: String = ""
@export var display_name: String = ""
@export var model: PackedScene = null
@export var box_size: Vector3 = Vector3.ZERO
@export var box_color: Color = Color.WHITE
@export var box_alpha: float = 1.0
## Optional extra variants (e.g. road corner / split) keyed by variant name.
@export var variant_models: Dictionary = {}
