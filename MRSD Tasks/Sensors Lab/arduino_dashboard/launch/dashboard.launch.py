from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node

def generate_launch_description():
    # Declare launch arguments
    port_arg = DeclareLaunchArgument(
        'port',
        default_value='/dev/ttyACM1',
        description='Serial port for Arduino'
    )
    
    baudrate_arg = DeclareLaunchArgument(
        'baudrate',
        default_value='115200',
        description='Serial baudrate'
    )
    
    simulation_arg = DeclareLaunchArgument(
        'simulation',
        default_value='false',
        description='Run in simulation mode without Arduino'
    )
    
    return LaunchDescription([
        port_arg,
        baudrate_arg,
        simulation_arg,
        
        # Serial communication node
        Node(
            package='arduino_dashboard',
            executable='serial_node',
            name='arduino_serial_node',
            parameters=[
                {'port': LaunchConfiguration('port')},
                {'baudrate': 115200},
                {'simulation': LaunchConfiguration('simulation')}
            ],
            output='screen'
        ),
        
        # RQt with the dashboard plugin
        Node(
            package='rqt_gui',
            executable='rqt_gui',
            name='rqt_gui',
            arguments=['--standalone', 'arduino_dashboard'],
            output='screen'
        ),
    ])
