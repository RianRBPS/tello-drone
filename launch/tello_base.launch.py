"""
Phase 1-2 launch: driver + camera_info_publisher + rtabmap visual odometry + RViz.
Usage:
    ros2 launch tello_base.launch.py
    ros2 launch tello_base.launch.py rviz:=false   # headless
"""
import os
from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.conditions import IfCondition
from launch.substitutions import LaunchConfiguration, PathJoinSubstitution
from launch_ros.actions import Node


def generate_launch_description():
    repo_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    calibration_file = os.path.join(repo_dir, 'config', 'tello_calibration.yaml')

    rviz_arg = DeclareLaunchArgument('rviz', default_value='true',
                                     description='Launch RViz')

    tello_driver = Node(
        package='tello_driver',
        executable='tello_driver_main',
        name='tello_driver',
        output='screen',
    )

    camera_info = Node(
        package='camera_info_publisher',
        executable='camera_info_publisher',
        name='camera_info_publisher',
        parameters=[{'calibration_file': calibration_file}],
        output='screen',
    )

    rtabmap = Node(
        package='rtabmap_slam',
        executable='rtabmap',
        name='rtabmap',
        output='screen',
        parameters=[{
            'frame_id': 'base_link',
            'subscribe_rgb': True,
            'subscribe_depth': False,
            'subscribe_rgbd': False,
            'subscribe_scan': False,
            'visual_odometry': True,
            'Vis/MaxFeatures': '500',
            'Kp/MaxFeatures': '500',
            'Mem/STMSize': '30',
        }],
        remappings=[
            ('rgb/image', '/image_raw'),
            ('rgb/camera_info', '/camera_info'),
        ],
    )

    rviz = Node(
        package='rviz2',
        executable='rviz2',
        name='rviz2',
        condition=IfCondition(LaunchConfiguration('rviz')),
        output='screen',
    )

    return LaunchDescription([
        rviz_arg,
        tello_driver,
        camera_info,
        rtabmap,
        rviz,
    ])
