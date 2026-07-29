"""ROS2 .msg definition texts for mcap-ros2-support dynamic CDR encoding."""

from __future__ import annotations

BUILTIN_TIME = """\
int32 sec
uint32 nanosec
"""

STD_HEADER = """\
uint32 seq
builtin_interfaces/Time stamp
string frame_id
================================================================================
MSG: builtin_interfaces/Time
int32 sec
uint32 nanosec
"""

SENSOR_COMPRESSED_IMAGE = """\
std_msgs/Header header
string format
uint8[] data
================================================================================
MSG: std_msgs/Header
uint32 seq
builtin_interfaces/Time stamp
string frame_id
================================================================================
MSG: builtin_interfaces/Time
int32 sec
uint32 nanosec
"""

SENSOR_CAMERA_INFO = """\
std_msgs/Header header
uint32 height
uint32 width
string distortion_model
float64[] d
float64[9] k
float64[9] r
float64[12] p
uint32 binning_x
uint32 binning_y
sensor_msgs/RegionOfInterest roi
================================================================================
MSG: std_msgs/Header
uint32 seq
builtin_interfaces/Time stamp
string frame_id
================================================================================
MSG: builtin_interfaces/Time
int32 sec
uint32 nanosec
================================================================================
MSG: sensor_msgs/RegionOfInterest
uint32 x_offset
uint32 y_offset
uint32 height
uint32 width
bool do_rectify
"""

SENSOR_IMU = """\
std_msgs/Header header
geometry_msgs/Quaternion orientation
float64[9] orientation_covariance
geometry_msgs/Vector3 angular_velocity
float64[9] angular_velocity_covariance
geometry_msgs/Vector3 linear_acceleration
float64[9] linear_acceleration_covariance
================================================================================
MSG: std_msgs/Header
uint32 seq
builtin_interfaces/Time stamp
string frame_id
================================================================================
MSG: builtin_interfaces/Time
int32 sec
uint32 nanosec
================================================================================
MSG: geometry_msgs/Quaternion
float64 x
float64 y
float64 z
float64 w
================================================================================
MSG: geometry_msgs/Vector3
float64 x
float64 y
float64 z
"""

STD_FLOAT64_MULTI_ARRAY = """\
std_msgs/MultiArrayLayout layout
float64[] data
================================================================================
MSG: std_msgs/MultiArrayLayout
std_msgs/MultiArrayDimension[] dim
uint32 data_offset
================================================================================
MSG: std_msgs/MultiArrayDimension
string label
uint32 size
uint32 stride
"""

STD_STRING = """\
string data
"""

TF2_TF_MESSAGE = """\
geometry_msgs/TransformStamped[] transforms
================================================================================
MSG: geometry_msgs/TransformStamped
std_msgs/Header header
string child_frame_id
geometry_msgs/Transform transform
================================================================================
MSG: std_msgs/Header
uint32 seq
builtin_interfaces/Time stamp
string frame_id
================================================================================
MSG: builtin_interfaces/Time
int32 sec
uint32 nanosec
================================================================================
MSG: geometry_msgs/Transform
geometry_msgs/Vector3 translation
geometry_msgs/Quaternion rotation
================================================================================
MSG: geometry_msgs/Vector3
float64 x
float64 y
float64 z
================================================================================
MSG: geometry_msgs/Quaternion
float64 x
float64 y
float64 z
float64 w
"""

SCHEMAS: dict[str, str] = {
    "std_msgs/msg/Header": STD_HEADER,
    "sensor_msgs/msg/CompressedImage": SENSOR_COMPRESSED_IMAGE,
    "sensor_msgs/msg/CameraInfo": SENSOR_CAMERA_INFO,
    "sensor_msgs/msg/Imu": SENSOR_IMU,
    "std_msgs/msg/Float64MultiArray": STD_FLOAT64_MULTI_ARRAY,
    "std_msgs/msg/String": STD_STRING,
    "tf2_msgs/msg/TFMessage": TF2_TF_MESSAGE,
}
