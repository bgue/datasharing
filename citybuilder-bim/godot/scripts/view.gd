extends Node3D
## Kenney camera controller, extended with a home position (site centre), storey focus height
## and zoom limits that scale with the site size.

var camera_position:Vector3
var camera_rotation:Vector3
var home_position:Vector3 = Vector3.ZERO

var zoom:float = 30.0 # 30 = Standard zoom level, in meters
var zoom_min:float = 15.0
var zoom_max:float = 80.0

@onready var camera = $Camera

func _ready():
	
	camera_rotation = rotation_degrees # Initial rotation
	default_yaw = camera_rotation.y
	camera_position = home_position
	
	pass

## Pixels of the viewport covered by HUD panels (left, top, right, bottom). The framing fits the model into the
## free area and shifts the camera frustum so the model sits in the middle of it.
var ui_insets:Vector4 = Vector4.ZERO
## Set once the player rotates the camera: framing then keeps the yaw.
var yaw_fixed:bool = false
var default_yaw:float = 45.0

## Centre the camera on the site rectangle (in grid cells) and zoom out until the whole site fits. A long thin site
## is shown from the yaw (default, +-45 degrees or +90) at which it fits the screen best.
func frame_site(rect:Rect2i, snap_now:bool = true):
	var extent:float = float(maxi(rect.size.x, rect.size.y))
	zoom_max = maxf(80.0, extent * 4.0)
	home_position = Vector3(rect.position.x + (rect.size.x - 1) * 0.5, 0, rect.position.y + (rect.size.y - 1) * 0.5)
	camera_position = home_position
	position = home_position
	var area:Rect2 = Rect2(rect.position.x - 0.5, rect.position.y - 0.5, rect.size.x, rect.size.y)
	if not yaw_fixed:
		var base_yaw:float = default_yaw
		var best_yaw:float = base_yaw
		var best_zoom:float = fit_zoom(area, 0.0, 1.08, base_yaw)
		for d in [45.0, -45.0, 90.0]:
			var z:float = fit_zoom(area, 0.0, 1.08, base_yaw + d)
			if z < best_zoom * 0.88:  # only turn the camera for a clear gain
				best_zoom = z
				best_yaw = base_yaw + d
		camera_rotation.y = best_yaw
	zoom = clampf(fit_zoom(area, 0.0), zoom_min, zoom_max)
	camera.position = Vector3(0, 0, zoom)
	if snap_now:
		snap()

## Frames a set of grid cells (Vector2i): the camera looks at their bounding box centre from a distance at which
## the box (plus `height` grid units of building above it) fills the free viewport area.
## `pad_cells` adds a margin around the box, `min_zoom` lets close-ups go below the default zoom limit.
func frame_cells(cells:Array, height:float = 2.0, pad_cells:float = 1.0, snap_now:bool = false, min_zoom:float = 4.0):
	if cells.is_empty():
		return
	var lo := Vector2(1.0e9, 1.0e9)
	var hi := Vector2(-1.0e9, -1.0e9)
	for c in cells:
		var v := Vector2(c.x, c.y)
		lo = Vector2(minf(lo.x, v.x), minf(lo.y, v.y))
		hi = Vector2(maxf(hi.x, v.x), maxf(hi.y, v.y))
	var rect := Rect2(lo.x - 0.5 - pad_cells, lo.y - 0.5 - pad_cells, hi.x - lo.x + 1.0 + pad_cells * 2.0, hi.y - lo.y + 1.0 + pad_cells * 2.0)
	var centre := rect.get_center()
	camera_position = Vector3(centre.x - 0.0, camera_position.y, centre.y - 0.0)
	zoom_min = minf(zoom_min, min_zoom)
	zoom = clampf(fit_zoom(rect, height), zoom_min, zoom_max)
	if snap_now:
		snap()

## Camera distance at which `rect` (grid cells, x/z ground plane, `height` units tall) fits into the free viewport
## area for the camera's current target rotation. Exact perspective fit over the 8 corners of the box.
func fit_zoom(rect:Rect2, height:float, margin:float = 1.08, yaw_deg:float = NAN) -> float:
	var vp:Vector2 = get_viewport().get_visible_rect().size
	var free:Vector2 = Vector2(maxf(vp.x - ui_insets.x - ui_insets.z, 64.0), maxf(vp.y - ui_insets.y - ui_insets.w, 64.0))
	var tan_v:float = tan(deg_to_rad(camera.fov * 0.5))
	var tan_h:float = tan_v * (vp.x / maxf(vp.y, 1.0))
	# the fit area is a fraction of the full frustum
	tan_v *= free.y / maxf(vp.y, 1.0)
	tan_h *= free.x / maxf(vp.x, 1.0)
	var centre:Vector2 = rect.get_center()
	var yaw:float = camera_rotation.y if is_nan(yaw_deg) else yaw_deg
	var basis := Basis.from_euler(Vector3(deg_to_rad(camera_rotation.x), deg_to_rad(yaw), deg_to_rad(camera_rotation.z)))
	var inv:Basis = basis.inverse()
	var need:float = 0.0
	for ix in 2:
		for iz in 2:
			for iy in 2:
				var p := Vector3((rect.position.x if ix == 0 else rect.end.x) - centre.x, height * iy, (rect.position.y if iz == 0 else rect.end.y) - centre.y)
				var q:Vector3 = inv * p
				# camera sits at +z looking down -z: a point with camera z = q.z is (d - q.z) in front of it
				need = maxf(need, q.z + absf(q.x) / tan_h)
				need = maxf(need, q.z + absf(q.y) / tan_v)
	return need * margin

## Jumps straight to the target pose (the _process lerps are skipped), for screenshots and first frames.
func snap():
	position = camera_position
	rotation_degrees = camera_rotation
	camera.position = Vector3(0, 0, zoom)
	apply_insets()

## Sets the HUD insets (px) and re-centres the frustum.
func set_insets(left:float, top:float, right:float, bottom:float):
	ui_insets = Vector4(left, top, right, bottom)
	apply_insets()

## Shifts the camera frustum so that the target sits in the middle of the free (un-covered) viewport area.
func apply_insets():
	var vp:Vector2 = get_viewport().get_visible_rect().size
	if vp.x <= 0.0 or vp.y <= 0.0:
		return
	var dist:float = absf(camera.position.z)
	var tan_v:float = tan(deg_to_rad(camera.fov * 0.5))
	var tan_h:float = tan_v * (vp.x / vp.y)
	camera.v_offset = ((ui_insets.y - ui_insets.w) / vp.y) * dist * tan_v
	camera.h_offset = -((ui_insets.x - ui_insets.z) / vp.x) * dist * tan_h

## Storey focus: raises the camera's ground plane (grid units).
func set_focus_height(y:float):
	camera_position.y = y
	home_position.y = y

func _process(delta):
	
	# Set position and rotation to targets
	
	position = position.lerp(camera_position, delta * 8)
	rotation_degrees = rotation_degrees.lerp(camera_rotation, delta * 6)
	
	# Smoothly update zoom
	
	camera.position = camera.position.lerp(Vector3(0, 0, zoom), delta * 8)
	if ui_insets != Vector4.ZERO:
		apply_insets() # the frustum shift scales with the zoom
	
	handle_input(delta)

# Handle input

func handle_input(_delta):
	
	# Rotation
	
	var input := Vector3.ZERO
	
	input.x = Input.get_axis("camera_left", "camera_right")
	input.z = Input.get_axis("camera_forward", "camera_back")
	
	input = input.rotated(Vector3.UP, rotation.y).normalized()
	
	camera_position += input * (zoom / 120.0)
	
	# Zoom in/out
	
	if Input.is_action_just_released("zoom_in"):
		zoom = max(zoom_min, zoom - maxf(2.0, zoom * 0.12))
		
	if Input.is_action_just_released("zoom_out"):
		zoom = min(zoom_max, zoom + maxf(2.0, zoom * 0.12))
	
	# Back to center
	
	if Input.is_action_pressed("camera_center"):
		camera_position = home_position

func _input(event):
	
	# Rotate camera using mouse (hold 'middle' mouse button)
	
	if event is InputEventMouseMotion:
		if Input.is_action_pressed("camera_rotate"):
			camera_rotation += Vector3(0, -event.relative.x / 10, 0)
			yaw_fixed = true
