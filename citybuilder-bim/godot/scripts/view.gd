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
	camera_position = home_position
	
	pass

## Centre the camera on a site of the given size (in grid cells).
func frame_site(width_cells:int, depth_cells:int):
	var extent:float = float(maxi(width_cells, depth_cells))
	home_position = Vector3((width_cells - 1) * 0.5, 0, (depth_cells - 1) * 0.5)
	camera_position = home_position
	position = home_position
	zoom_max = maxf(80.0, extent * 4.0)
	zoom = clampf(extent * 2.2, 30.0, zoom_max)
	camera.position = Vector3(0, 0, zoom)

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
