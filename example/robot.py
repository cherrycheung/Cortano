from cortano import RemoteInterface

if __name__ == "__main__":
  robot = RemoteInterface("192.168.1.100")
  while robot.running():
    color, depth, sensors, _ = robot.read()
    # print(info['voltage'], sensors, info['time'])

    forward = robot.keys["w"] - robot.keys["s"]
    robot.motor[0] =  forward * 127
    robot.motor[9] = -forward * 127
