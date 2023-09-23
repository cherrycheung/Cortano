import websockets
import asyncio
import json
import socket
import sys
import pickle
import cv2
import signal
import time
from multiprocessing import (
  Lock, Array, Value, RawArray, Process
)
from ctypes import c_int, c_double, c_bool, c_uint8, c_uint16
import numpy as np

main_loop = None
_rxtx_task = None
_rgbd_task = None
_connected = Value(c_bool, False)

_host = None
_port = None
_stream_host = None
_stream_port = None

_motor_values = Array(c_int, 10)
_sensor_values = Array(c_int, 20)
_num_sensors = Value(c_int, 0)
_last_rx_time = Value(c_double, time.time() - 30) # set to the past

_frame_shape = (360, 640)
_frame_color = None
_frame_depth = None
_frame_lock = Lock()
_frame_color_np = None
_frame_depth_np = None
_running = Value(c_bool, False)
_tx_ms_interval = .02

_rxtx_process = None
_rgbd_process = None

def get_ipv4():
  s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
  s.connect(("8.8.8.8", 80))
  addr = s.getsockname()[0]
  s.close()
  return addr

async def streamer_recv(websocket, path):
  global _running
  global _frame_color, _frame_depth, _frame_lock
  global _frame_color_np, _frame_depth_np
  _running.value = True
  if _frame_color_np is None and _frame_color is not None:
    h, w = _frame_shape
    _frame_color_np = np.frombuffer(_frame_color, np.uint8).reshape((h, w, 3))
    _frame_depth_np = np.frombuffer(_frame_depth, np.uint16).reshape((h, w))
  async for req in websocket:
    color, depth = pickle.loads(req, fix_imports=True, encoding="bytes")
    color = cv2.imdecode(color, cv2.IMREAD_COLOR)
    depth = cv2.imdecode(depth, cv2.IMREAD_UNCHANGED)
    _frame_lock.acquire()
    np.copyto(_frame_color_np, color)
    np.copyto(_frame_depth_np, depth)
    _frame_lock.release()

async def request_handler(host, port):
  async with websockets.serve(streamer_recv, host, port):
    try:
      await asyncio.Future()
    except asyncio.exceptions.CancelledError:
      print("Closing gracefully.")
      return
    except Exception as e:
      print(e)
      sys.exit(1)

async def rxtx(host='0.0.0.0', port=9999):
  global _running, _connected
  global _motor_values, _sensor_values, _num_sensors, _last_rx_time
  last_tx_time = time.time()
  _connected.value = False

  ipv4 = get_ipv4()
  start_time = time.time()

  while _running.value:
    try:
      async with websockets.connect(f"ws://{host}:{port}") as websocket:
        _connected.value = True
        sleep_time = _tx_ms_interval - (time.time() - last_tx_time)
        if sleep_time > 0: time.sleep(sleep_time) # throttle message stream
        last_tx_time = time.time()

        _motor_values.acquire()
        mvals = _motor_values[:]
        _motor_values.release()
        await websocket.send(json.dumps({ "motors": mvals, "ipv4": ipv4 }))

        msg = await websocket.recv()
        msg = json.loads(msg)
        if "sensors" in msg:
          sensors = msg["sensors"]
          _sensor_values.acquire()
          ns = _num_sensors.value = len(sensors)
          _sensor_values[:ns] = sensors
          _last_rx_time.value = last_tx_time - start_time
          _sensor_values.release()
    except asyncio.exceptions.CancelledError as e:
      _connected.value = False
    except Exception as e:
      _connected.value = False
      pass # this will allow this loop to continue running, allowing the stream to run as well

def run_async_rxtx(host, port, shost, sport, run, mvals, svals, ns):
  global _running, main_loop, _rxtx_task
  global _stream_host, _stream_port
  global _host, _port, _motor_values, _sensor_values, _num_sensors
  
  _host = host
  _port = port
  _stream_host = "0.0.0.0"
  _stream_port = sport
  _running = run
  _motor_values = mvals
  _sensor_values = svals
  _num_sensors = ns

  if main_loop is None:
    main_loop = asyncio.new_event_loop()
  _rxtx_task = main_loop.create_task(rxtx(host, port))
  
  for signo in [signal.SIGINT, signal.SIGTERM]:
    main_loop.add_signal_handler(signo, _rxtx_task.cancel)

  try:
    asyncio.set_event_loop(main_loop)
    main_loop.run_until_complete(_rxtx_task)
  except (KeyboardInterrupt,):
    _running.value = False
    main_loop.stop()
  finally:
    main_loop.run_until_complete(main_loop.shutdown_asyncgens())
    main_loop.close()

def run_async_rgbd(host, port, run, fshape, fcolor, fdepth, flock):
  global _running, main_loop, _rgbd_task
  global _stream_host, _stream_port
  global _frame_shape, _frame_color, _frame_depth, _frame_lock

  _stream_host = "0.0.0.0"
  _stream_port = port
  _running = run
  _frame_shape = fshape
  _frame_color = fcolor
  _frame_depth = fdepth
  _frame_lock = flock

  if main_loop is None:
    main_loop = asyncio.new_event_loop()
  _rgbd_task = main_loop.create_task(request_handler(_stream_host, _stream_port))
  
  for signo in [signal.SIGINT, signal.SIGTERM]:
    main_loop.add_signal_handler(signo, _rgbd_task.cancel)

  try:
    asyncio.set_event_loop(main_loop)
    main_loop.run_until_complete(_rgbd_task)
  except (KeyboardInterrupt,):
    _running.value = False
    main_loop.stop()
  finally:
    main_loop.run_until_complete(main_loop.shutdown_asyncgens())
    main_loop.close()

def start(host="0.0.0.0", port=9999, frame_shape=(360, 640)):
  global _running
  global _motor_values, _sensor_values, _num_sensors, _last_rx_time
  global _host, _port, _frame_shape, _frame_color, _frame_depth, _frame_lock
  global _rxtx_process, _rgbd_process

  _host = host
  _port = port
  _running = Value(c_bool, True)
  _stream_host = "0.0.0.0"
  _stream_port = _port + 1
  _frame_shape = frame_shape
  _frame_color = RawArray(c_uint8, _frame_shape[0] * _frame_shape[1] * 3)
  _frame_depth = RawArray(c_uint16, _frame_shape[0] * _frame_shape[1])
  _frame_lock = Lock()

  _rxtx_process = Process(target=run_async_rxtx, args=(
    _host, _port, _stream_host, _stream_port,
    _running, _motor_values, _sensor_values, _num_sensors))
  _rxtx_process.start()

  _rgbd_process = Process(target=run_async_rgbd, args=(
    _stream_host, _stream_port, _running,
    _frame_shape, _frame_color, _frame_depth, _frame_lock))
  _rgbd_process.start()

def stop():
  global _running, _rxtx_process, _rgbd_process
  _running.acquire()
  running = _running.value
  _running.value = False
  _running.release()
  if running:
    if sys.platform.startswith('win') and _rxtx_process:
      _rxtx_process.terminate()
      _rgbd_process.terminate()
      time.sleep(0.3)
      _rxtx_process.kill()
      _rgbd_process.kill()
      _rxtx_process = None
      _rgbd_process = None
      time.sleep(0.3)
    else:
      time.sleep(0.5) # cant kill the process because linux is strange

def sig_handler(signum, frame):
  if signum == signal.SIGINT or signum == signal.SIGTERM:
    stop()
    sys.exit(0)

signal.signal(signal.SIGINT, sig_handler)
signal.signal(signal.SIGTERM, sig_handler)

def get_frame():
  global _frame_lock, _frame_color, _frame_depth
  global _frame_color_np, _frame_depth_np
  if _frame_color_np is None and _frame_color is not None:
    h, w = _frame_shape
    _frame_color_np = np.frombuffer(_frame_color, np.uint8).reshape((h, w, 3))
    _frame_depth_np = np.frombuffer(_frame_depth, np.uint16).reshape((h, w))
  _frame_lock.acquire()
  color = np.copy(_frame_color_np)
  depth = np.copy(_frame_depth_np)
  _frame_lock.release()
  return color, depth

def read():
  global _sensor_values, _num_sensors, _last_rx_time
  _sensor_values.acquire()
  ns = _num_sensors.value
  sensors = [] if ns == 0 else _sensor_values[:ns]
  rxt = _last_rx_time.value
  _sensor_values.release()
  return sensors # time, values

def write(motor_values):
  global _motor_values
  assert(len(motor_values) == 10)
  motor_values = list(motor_values)
  _motor_values.acquire()
  _motor_values[:] = motor_values
  _motor_values.release()
