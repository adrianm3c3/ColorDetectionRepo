import cv2
import numpy as np
import requests
import json
import time
import tempfile
import os

# ---- CONFIG ----
ROBOT_IP = "169.254.19.251"
CAM_INDEX = 0
TARGET_COLOR = "red"
MOVE_HEIGHT = 50
CAMERA_RES = (640, 480)
DEBUG_POINT = [150, 150, 50]   # move here if no color found

# ---- CAMERA CAPTURE ----
cap = cv2.VideoCapture(CAM_INDEX)
cap.set(cv2.CAP_PROP_FRAME_WIDTH, CAMERA_RES[0])
cap.set(cv2.CAP_PROP_FRAME_HEIGHT, CAMERA_RES[1])
time.sleep(1)

print("[INFO] Capturing frame...")
ret, frame = cap.read()
cap.release()
if not ret:
    raise RuntimeError("❌ Could not capture frame")

# ---- COLOR DETECTION ----
hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)

if TARGET_COLOR == "red":
    lower1, upper1 = np.array([0,70,50]), np.array([15,255,255])
    lower2, upper2 = np.array([165,70,50]), np.array([180,255,255])
    mask = cv2.inRange(hsv, lower1, upper1) | cv2.inRange(hsv, lower2, upper2)
elif TARGET_COLOR == "green":
    mask = cv2.inRange(hsv, np.array([35,50,50]), np.array([85,255,255]))
elif TARGET_COLOR == "blue":
    mask = cv2.inRange(hsv, np.array([90,50,50]), np.array([140,255,255]))
else:
    raise ValueError("Unsupported TARGET_COLOR")

M = cv2.moments(mask)
if M["m00"] > 0:
    cx, cy = int(M["m10"]/M["m00"]), int(M["m01"]/M["m00"])
    print(f"✅ Detected {TARGET_COLOR} at pixel ({cx}, {cy})")

    # --- PIXEL TO DECK COORD MAPPING ---
    scale = 0.5
    offset_x, offset_y = 200, 150
    x = offset_x + (cx - CAMERA_RES[0]/2) * scale
    y = offset_y - (cy - CAMERA_RES[1]/2) * scale
else:
    print("⚠️ No color detected. Using debug fallback.")
    x, y, _ = DEBUG_POINT

z = MOVE_HEIGHT
print(f"→ Target deck coords: ({x:.1f}, {y:.1f}, {z})")

# ---- GENERATE PYTHON PROTOCOL ----
protocol_code = f"""from opentrons import protocol_api

metadata = {{'apiLevel': '2.15'}}

def run(protocol: protocol_api.ProtocolContext):
    pipette = protocol.load_instrument('p300_single', 'right')
    pipette.home()
    pipette.move_to(protocol.deck.position_for('A1').move(protocol_api.types.Point({x:.1f}, {y:.1f}, {z:.1f})))
"""

tmp_py = tempfile.NamedTemporaryFile(delete=False, suffix=".py")
tmp_py.write(protocol_code.encode("utf-8"))
tmp_py.close()
print(f"[INFO] Generated protocol file at {tmp_py.name}")

# ---- UPLOAD PROTOCOL ----
headers = {"Opentrons-Version": "2"}
with open(tmp_py.name, "rb") as f:
    files = {'files': (os.path.basename(tmp_py.name), f, 'text/x-python')}
    resp = requests.post(
        f"http://{ROBOT_IP}:31950/protocols",
        headers=headers,
        files=files
    )
    print(f"[UPLOAD] {resp.status_code}: {resp.text}")

if resp.status_code != 201:
    raise RuntimeError("❌ Failed to upload protocol")

protocol_id = resp.json()["data"]["id"]

# ---- START RUN ----
run_headers = {
    "Content-Type": "application/json",
    "Opentrons-Version": "2"
}
run_resp = requests.post(
    f"http://{ROBOT_IP}:31950/runs",
    headers=run_headers,
    json={"data": {"protocolId": protocol_id}}
)
print(f"[RUN] {run_resp.status_code}: {run_resp.text}")

if run_resp.status_code != 201:
    raise RuntimeError("❌ Failed to start run")

print("✅ Protocol uploaded and running on OT-2!")
