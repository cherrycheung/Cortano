from cortano import RemoteInterface

if __name__ == "__main__":
  robot = RemoteInterface("192.168.1.1")
  while True:
    robot.update() # must never be forgotten
    color, depth, sensors = robot.read()
    print(sensors)

    forward = robot.keys["w"] - robot.keys["s"]
    robot.motor[0] =  forward * 127
    robot.motor[9] = -forward * 127
