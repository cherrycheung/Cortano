import websockets
import asyncio
import json
import sys
import pickle
import cv2
import signal
import socket
import time
from multiprocessing import (
  Lock, Array, Value, RawArray, Process
)
from ctypes import c_int, c_float, c_bool, c_uint8, c_uint16, c_char
import numpy as np
import logging
from datetime import datetime
import qoi

####
# The network communication stack consists of 4 additional processes separate from the main process
# 1. rxtx
# 2. color (recv req only, jpg 90%)
# 3. depth (recv req only, qoi)
# 4. color2 (optional, but useful) (recv req only, jpg 90%)
# rxtx uses two async workers - one to handle motor tx, and the other to handle sensor rx
####

# shared variables
frame_shape = (360, 640)
tx_interval = 1 / 60

class RemoteByteBuf:
  def __init__(self, size=None):
    self.lock = Lock()
    self.size = size
    self.buf = None if (size is None) else RawArray(c_uint8, int(np.prod(self.size)))
    self.len = Value(c_int, 0)
    self.en = Value(c_bool, False)

    self.encode = lambda frame: frame
    self.decode = lambda frame: frame

  def sync_process(self, lock, buf, len, en):
    self.lock = lock
    self.buf = buf
    self.len = len
    self.en = en

  def numpy(self, dtype=np.uint8):
    return np.frombuffer(self.buf, dtype=dtype)
  
  def enabled(self):
    return self.en.value
  
  def copyfrom(self, data):
    data = data.flatten().view(np.uint8) if isinstance(data, np.ndarray) else np.frombuffer(data, np.uint8)
    self.lock.acquire()
    if len(data) == np.prod(self.size):
      np.copyto(self.numpy(), data)
    else:
      np.copyto(self.numpy()[:len(data)], data)
    self.len.value = len(data)
    self.en.value = True
    self.lock.release()

  def asbytes(self):
    self.lock.acquire()
    if self.en.value == True:
      if self.len.value == self.size:
        bytestr = self.numpy().tobytes()
      else:
        bytestr = self.numpy()[:self.len.value].tobytes()
      self.en.value = False
    else:
      bytestr = None
    self.lock.release()
    return bytestr

motor_values = Array(c_float, 10)
sensor_values = Array(c_float, 20) # [sensor1, sensor2, ...]
sensor_length = Value(c_int, 0)
voltage_level = Value(c_float, 0)

main_loop = None
_running = Value(c_bool, True)
last_rx_time = Array(c_char, 100)
last_rx_time.value = datetime.isoformat(datetime.now()).encode()

rxtx_task = None
recv_task1 = None
recv_task2 = None
recv_task3 = None

def get_ipv4():
  s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
  s.connect(("8.8.8.8", 80))
  addr = s.getsockname()[0]
  s.close()
  return addr

def decode_color(frame):
  frame = pickle.loads(frame, fix_imports=True, encoding="bytes")
  color = cv2.imdecode(frame[1], cv2.IMREAD_COLOR)
  return color

def decode_depth(frame):
  depth = qoi.decode(frame)
  depth = depth.reshape((180, 640)).view(np.uint16)
  # depth = cv2.resize(depth, (640, 360), interpolation=cv2.INTER_AREA)
  depth = np.tile(depth.reshape((180, 1, 320, 1)), (1, 2, 1, 2)).reshape((360, 640))
  return depth

color_buf = RemoteByteBuf((frame_shape[0], frame_shape[1], 3))
color_buf.decode = decode_color
depth_buf = RemoteByteBuf((frame_shape[0], frame_shape[1] // 2, 4))
depth_buf.decode = decode_depth
color2_buf = RemoteByteBuf((frame_shape[0], frame_shape[1], 3))
color2_buf.decode = decode_color
msg_buf = None

async def stream_recv(websocket, path):
  _running.value = True
  async for req in websocket:
    try:
      frame = msg_buf.decode(req)
      msg_buf.copyfrom(frame)
    except Exception as e:
      print(e)

async def stream_handler(host, port):
  async with websockets.serve(stream_recv, host, port):
    try:
      await asyncio.Future()
    except asyncio.exceptions.CancelledError:
      logging.info("Closing gracefully.")
      return
    except Exception as e:
      logging.error(e)
      sys.exit(1)

def streamer_worker(port, run, buf):
  global main_loop, _running, msg_buf
  msg_buf = buf

  _running = run
  main_loop = asyncio.new_event_loop()
  stream_task = main_loop.create_task(stream_handler("0.0.0.0", port))

  try:
    asyncio.set_event_loop(main_loop)
    main_loop.run_until_complete(stream_task)
  except (KeyboardInterrupt,):
    _running.value = False
    main_loop.stop()
  finally:
    main_loop.run_until_complete(main_loop.shutdown_asyncgens())
    main_loop.close()

async def sender(websocket):
  last_tx_time = None
  ipv4 = get_ipv4()

  while _running.value:
    try:
      curr_time = time.time()
      dt = tx_interval if last_tx_time is None else (curr_time - last_tx_time)
      if dt < tx_interval:
        await asyncio.sleep(tx_interval - dt) # throttle to prevent overload
        last_tx_time = time.time()
      else:
        last_tx_time = curr_time

      motor_values.acquire()
      motors = motor_values[:]
      motor_values.release()

      motors = [int(x * 1000) for x in np.array(motors, np.float32).clip(-1, 1)]
      msg = json.dumps({"motors": motors, "ipv4": ipv4}).encode("utf-8")
      await websocket.send(msg)
    except websockets.ConnectionClosed:
      # logging.warning(datetime.isoformat(datetime.now()) + " Connection closed.")
      last_tx_time = None
      await asyncio.sleep(1) # just wait forever...

async def receiver(websocket):
  while _running.value:
    try:
      msg = await websocket.recv()
      msg = json.loads(msg)

      sensor_values.acquire()
      nsensors = sensor_length.value = len(msg["sensors"])
      sensor_values[:nsensors] = [float(x) for x in msg["sensors"]]
      voltage_level.value = float(msg["voltage"]) / 1e3
      sensor_values.release()
    except ValueError:
      logging.error("Invalid data received:", msg)
    except websockets.ConnectionClosed:
      # logging.warning(datetime.isoformat(datetime.now()) + " Connection closed.")
      motor_values.acquire()
      motor_values[:] = [0] * 10
      motor_values.release()
      await asyncio.sleep(1)

async def handle_rxtx(host, port):
  async with websockets.connect("ws://" + host + ":" + str(port)) as websocket:
    recv_task = main_loop.create_task(receiver(websocket))
    send_task = main_loop.create_task(sender(websocket))
    await asyncio.gather(recv_task, send_task)

def rxtx_worker(host, port, run, mvals, svals, slen, vlvl):
  global main_loop, _running
  global motor_values, sensor_values, sensor_length, voltage_level
  motor_values = mvals
  sensor_values = svals
  sensor_length = slen
  voltage_level = vlvl

  _running = run
  main_loop = asyncio.new_event_loop()
  rxtx_task = main_loop.create_task(handle_rxtx(host, port))
  try:
    asyncio.set_event_loop(main_loop)
    main_loop.run_until_complete(rxtx_task)
  except (KeyboardInterrupt,):
    _running.value = False
    main_loop.stop()
  finally:
    main_loop.run_until_complete(main_loop.shutdown_asyncgens())
    main_loop.close()

def start(host="0.0.0.0", port=9999):
  global color_buf, depth_buf, color2_buf
  global rxtx_task, recv_task1, recv_task2, recv_task3

  # remove temporarily during process creation
  cbuf = color_buf
  dbuf = depth_buf
  cbuf2 = color2_buf
  color_buf = None
  depth_buf = None
  color2_buf = None

  recv_task1 = Process(target=streamer_worker, args=(
    port-1, _running, cbuf))
  recv_task2 = Process(target=streamer_worker, args=(
    port-2, _running, dbuf))
  recv_task3 = Process(target=streamer_worker, args=(
    port-3, _running, cbuf2))

  recv_task1.start()
  recv_task2.start()
  recv_task3.start()

  rxtx_task = Process(target=rxtx_worker, args=(
    host, port, _running, motor_values, sensor_values, sensor_length, voltage_level))
  rxtx_task.start()

  color_buf = cbuf
  depth_buf = dbuf
  color2_buf = cbuf2

def stop():
  global rxtx_task, recv_task1, recv_task2, recv_task3
  was_running = _running.value
  _running.value = False
  if was_running:
    if sys.platform.startswith('win'):
      for task in (rxtx_task, recv_task1, recv_task2, recv_task3):
        task.terminate()
      for task in (rxtx_task, recv_task1, recv_task2, recv_task3):
        task.join()
      rxtx_task = None
      recv_task1 = None
      recv_task2 = None
      recv_task3 = None
      time.sleep(0.3)
      sys.exit(0)
    else:
      for task in (rxtx_task, recv_task1, recv_task2, recv_task3):
        task.kill()
      rxtx_task = None
      recv_task1 = None
      recv_task2 = None
      recv_task3 = None
      time.sleep(0.5) # cant kill the process because linux is weird
      sys.exit(0)

# def sig_handler(signum, frame):
#   if signum == signal.SIGINT or signum == signal.SIGTERM:
#     stop()
#     sys.exit(0)

# signal.signal(signal.SIGINT, sig_handler)
# signal.signal(signal.SIGTERM, sig_handler)

def read():
  """Return sensors values, voltage (V) and time when the data comes in

  Returns:
      Tuple[np.ndarray, np.ndarray, List[int], Dict[float, datetime, np.ndarray]]: color, depth, sensors, { voltage, time, cam2 }
  """
  h, w = frame_shape
  color = color_buf.numpy().reshape((h, w, 3))
  depth = depth_buf.numpy(np.uint16).reshape((h, w))
  color2 = color2_buf.numpy().reshape((h, w, 3))

  sensor_values.acquire()
  ns = sensor_length.value
  sensors = [] if ns == 0 else sensor_values[:ns]
  voltage = voltage_level.value
  sensor_values.release()

  last_rx_time.acquire()
  rxtime = last_rx_time.value.decode()
  if len(rxtime) > 0:
    rxtime = datetime.fromisoformat(rxtime)
  else:
    rxtime = None
  last_rx_time.release()

  return color, depth, sensors, { "voltage": voltage, "time": rxtime, "cam2": color2 }

def write(values):
  """Send motor values to remote location

  Args:
      values (List[float]): motor values
  """
  assert(len(values) == 10)
  values = [float(x) for x in values]
  motor_values.acquire()
  motor_values[:] = values
  motor_values.release()

def running():
  return _running.value