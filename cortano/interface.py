import cv2
import numpy as np
import customtkinter as ctk
from multiprocessing import (
  Process,
  Lock,
  Array,
  RawArray,
  Value
)
from ctypes import c_uint8
from PIL import Image, ImageTk
import logging
import lan
import sys
import time

def _depth2rgb(depth):
  return cv2.applyColorMap(np.sqrt(depth).astype(np.uint8), cv2.COLORMAP_HSV)

def _ctk_interface(key_values, color_buf, depth_buf, color2_buf):
  ctk.set_appearance_mode("Dark")
  ctk.set_default_color_theme("blue")
  app = ctk.CTk()
  app.geometry("1600x900")

  h, w = lan.frame_shape
  color_np = np.frombuffer(color_buf, np.uint8).reshape((h, w, 3))
  depth_np = np.frombuffer(depth_buf, np.uint16).reshape((h, w))
  color2_np = np.frombuffer(color2_buf, np.uint8).reshape((h, w, 3))

  depth_image = Image.fromarray(_depth2rgb(depth_np))
  color_image = Image.fromarray(color_np)
  color2_image = Image.fromarray(color2_np)
  color_photo = ImageTk.PhotoImage(image=color_image)
  depth_photo = ImageTk.PhotoImage(image=depth_image)
  color2_photo = ImageTk.PhotoImage(image=color2_image)

  color_canvas = ctk.CTkCanvas(app, width=lan.frame_shape[1], height=lan.frame_shape[0])
  color_canvas.place(x=20, y=20)
  canvas_color_object = color_canvas.create_image(0, 0, anchor=ctk.NW, image=color_photo)
  depth_canvas = ctk.CTkCanvas(app, width=lan.frame_shape[1], height=lan.frame_shape[0])
  depth_canvas.place(x=680, y=20)
  canvas_depth_object = depth_canvas.create_image(0, 0, anchor=ctk.NW, image=depth_photo)
  color2_canvas = ctk.CTkCanvas(app, width=lan.frame_shape[1], height=lan.frame_shape[0])
  color2_canvas.place(x=20, y=400)
  canvas_color2_object = color2_canvas.create_image(0, 0, anchor=ctk.NW, image=color2_photo)

  def animate():
    app.after(8, animate)

    depth_image = Image.fromarray(_depth2rgb(depth_np))
    color_image = Image.frombuffer('RGB', (w, h), color_buf, 'raw')
    c2 = lan.cam2_enable.value
    if c2:
      color2_image = Image.frombuffer('RGB', (w, h), color2_buf, 'raw')

    color_photo.paste(color_image)
    depth_photo.paste(depth_image)
    if c2:
      color2_photo.paste(color2_image)

    color_canvas.itemconfig(canvas_color_object, image=color_photo)
    depth_canvas.itemconfig(canvas_depth_object, image=depth_photo)
    if c2:
      color2_canvas.itemconfig(canvas_color2_object, image=color2_photo)

  keycodes = {}
  keyrelease = {}

  def on_key_press(event):
    charcode = ord(event.char) if event.char else None
    if charcode and charcode > 0 and charcode < 128:
      keycodes[event.keycode] = charcode
      keyrelease[event.keycode] = time.time()
      key_values[charcode] = 1

  def on_key_release(event):
    charcode = None
    if event.keycode in keycodes: charcode = keycodes[event.keycode]
    if charcode and charcode > 0 and charcode < 128:
      def release_check():
        if time.time() - keyrelease[event.keycode] > 0.099:
          key_values[charcode] = 0
      keyrelease[event.keycode] = time.time()
      app.after(100, release_check)

  app.bind("<KeyPress>", on_key_press)
  app.bind("<KeyRelease>", on_key_release)
  app.after(8, animate)
  app.mainloop()
  lan.stop()

class RemoteInterface:
  def __init__(self, host="0.0.0.0", port=9999):
    """Remote Interface showing the data coming in from the robot

    Args:
        host (str, optional): host ip of the robot. Defaults to "0.0.0.0".
    """
    lan.start(host, port)
    self.keyboard_buf = RawArray(c_uint8, 128)
    self.ui_task = Process(target=_ctk_interface, args=(self.keyboard_buf, lan.color_buf, lan.depth_buf, lan.color2_buf))
    self.ui_task.start()

  def __del__(self):
    lan.stop()
    if self.ui_task:
      self.ui_task.kill()
    # sys.exit(1)

  @property
  def keys(self):
    return {
      chr(x): self.keyboard_buf[x] for x in range(128)
    }

  def disp(self, frame):
    """Set an optional output frame

    Args:
        frame (np.ndarray): frame sized (360, 640, 3) that can be displayed in real time
    """
    self.free_frame = frame

  @property
  def motor(self):
    return lan.motor_values
  
  def read(self):
    """Read sensor values from the robot, including color and depth

    Returns:
        (np.ndarray, np.ndarray, np.ndarray, dict): color, depth, other sensor values
    """
    return lan.read()
  
  def running(self):
    _running = lan.running()
    if not _running:
      lan.stop()
      if self.ui_task:
        self.ui_task.kill()
        self.ui_task = None
    return _running
  
if __name__ == "__main__":
  robot = RemoteInterface()
  while robot.running():
    forward = robot.keys["w"]
    robot.motor[0] = forward * 0.25
    robot.motor[9] = forward * 0.25