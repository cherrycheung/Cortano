# Cortano
Remote Interface for Jetson Nano + Vex Cortex, which connects to the robot. The partner component that should be installed first on the robot's Jetson Nano can be found at [CortexNanoBridge](https://github.com/timrobot/CortexNanoBridge).

### Getting Started
1. Get your Jetson's IP address. This can be done by typing the following into a new terminal:

```bash
ifconfig
```

Your IP Address should be the IPv4 address (ie. 192.168.1.100) under wlan0.

2. Install this repository to to your laptop/desktop.

```bash
python3 -m pip install .
```

3. You can now run an example program to read sensor data and camera frames from the robot

```python
from cortano import RemoteInterface

if __name__ == "__main__":
  robot = RemoteInterface("192.168.1.100") # remember to put your ip here
  while robot.running():
    color, depth, sensors, info = robot.read()
```

To control a robot, set the motor values (0-10) to anywhere between [-1, 1]

```python
from cortano import RemoteInterface

if __name__ == "__main__":
  robot = RemoteInterface("192.168.1.100")
  while robot.running():
    forward = robot.keys["w"] - robot.keys["s"]
    robot.motor[0] = forward * 0.5
    robot.motor[9] = forward * -0.5
```

The info struct contains additional information:
|  |  |
|--|--|
| battery | 0.0 - 14.0V or 0-100%
| cam2 | second camera's color frame (optional) |
| time | timestamp on robot when data was sent |

### API

| Method | Description | Return |
|-|-|-|
| `RemoteInterface(host, port)` | Constructor. Initialize socket connection to robot using `host` and `port=(default: 9999)` | `RemoteInterface` |
| `running()` | Inspect whether or not a robot is still running | `bool` |
| `read()` | Get sensor values, camera frames, and power level from the robot | <ul><li>color: `uint8[360, 640, 3]`</li><li>depth: `uint16[360, 640]`</li><li>sensors: `float[20]`</li><li>info: `{`...`}`</li></ul> |
| `motor[port]` | Set a motor port value between `[-1, 1]` | `ref(float)` |
| `keys[keyname]` | Given a `keyname:char`, returns if pressed down or not | 1 if pressed, 0 otherwise |