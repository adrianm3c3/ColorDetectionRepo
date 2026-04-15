from http.server import BaseHTTPRequestHandler, HTTPServer
import threading
import json
import cv2
import numpy as np
import time
import requests
import math

# ============================================================
# OT-2 CAMERA NODE
# ============================================================
# Purpose:
# This script watches a camera feed, detects colored regions on
# the OT-2 deck, estimates which deck slot the color belongs to,
# analyzes the hardcoded tip rack in slot 1, and sends the best
# detection result to the MADSci orchestrator.
#
# In simpler terms:
# 1. Open the camera
# 2. Look for colors like red, blue, green, etc.
# 3. Figure out which OT-2 slot the color is closest to
# 4. Check the tip rack and estimate which tip is best to use
# 5. Send that information to another system over HTTP
#
# This version is especially useful for teaching because the script
# shows a complete perception pipeline:
#   Perception -> Interpretation -> Decision -> Communication
# ============================================================


# ============================================================
# CONFIGURATION
# ============================================================
# These values act like "settings" for the script.
# If students want to tune behavior, this is one of the first
# places they should look.

CLUSTER_RADIUS_PX = 120
# If multiple detections are near each other within this pixel radius,
# they can be treated as part of the same local cluster.

CLUSTER_MIN_NEIGHBORS = 1
# Currently not heavily used in scoring logic, but intended to represent
# how many nearby neighbors are needed to consider something a cluster.

ORCHESTRATOR_TRIGGER_URL = "http://localhost:2020/detection/color"
# HTTP endpoint of the MADSci orchestrator.
# The camera node sends detection results here.

HTTP_PORT = 2011
# Port used by this script's internal HTTP server.

CAMERA_INDEX = 1
# Which camera device OpenCV should open.
# 0 usually means the default webcam/camera.

SHOW_WINDOW = True
# If True, display the live annotated OpenCV window.

TRIGGER_COOLDOWN_SEC = 5.0
# Prevents sending repeated triggers too rapidly.
# After one detection is sent, the script waits this many seconds
# before sending another.

MIN_COLOR_AREA = 500
# Ignore very small color blobs/noise in the image.

MAX_SLOT_DISTANCE = 140
# If a detected color is too far from all known slot centers,
# do not assign it to a slot.

# LLaVA disabled for this version
USE_LLAVA_OBJECT_CHECK = False
# This script has a placeholder for richer labware understanding,
# but for now it is disabled and replaced with hardcoded logic.

# Hardcoded labware slots
HARDCODED_TIPRACK_SLOT = "1"
HARDCODED_DISPENSE_TRAY_SLOT = "2"
# For this teaching/demo version:
# - slot 1 is assumed to contain the tip rack
# - slot 2 is assumed to contain the dispense tray

# Slot calibration in image coordinates
# These are the approximate pixel centers of OT-2 deck slots in the camera image.
# The detection system uses these centers to map a detected object/color to a slot.
SLOT_CENTERS = {
    "11": (642, 112),
    "10": (416, 112),
    "9":  (864, 262),
    "8":  (642, 264),
    "7":  (421, 269),
    "6":  (868, 416),
    "5":  (642, 421),
    "4":  (421, 418),
    "3":  (864, 566),
    "2":  (645, 568),
    "1":  (422, 568),
}

# Approximate crop around slot 1 containing the whole tip rack.
# These dimensions define the rectangular region used to inspect the tip rack.
TIPRACK_SLOT = "1"
TIPRACK_CROP_W = 250
TIPRACK_CROP_H = 180

# 96-well tip rack layout
TIPRACK_ROWS = "ABCDEFGH"
TIPRACK_NUM_ROWS = 8
TIPRACK_NUM_COLS = 12

# Detection thresholds for deciding whether a well contains a tip.
# These are heuristic thresholds and may need tuning depending on
# camera angle, lighting, and image quality.
TIP_PRESENT_INTENSITY_THRESHOLD = 110
TIP_PRESENT_EDGE_THRESHOLD = 12.0

# HSV color ranges used for color detection.
# OpenCV often performs color detection in HSV instead of RGB/BGR
# because hue-based separation is easier.
COLOR_RANGES = {
    "red": [
        (np.array([0, 120, 70]), np.array([10, 255, 255])),
        (np.array([170, 120, 70]), np.array([180, 255, 255])),
    ],
    "blue": [
        (np.array([100, 120, 70]), np.array([130, 255, 255])),
    ],
    "green": [
        (np.array([40, 70, 70]), np.array([85, 255, 255])),
    ],
    "yellow": [
        (np.array([20, 120, 120]), np.array([35, 255, 255])),
    ],
    "orange": [
        (np.array([10, 120, 120]), np.array([20, 255, 255])),
    ],
    "purple": [
        (np.array([130, 80, 80]), np.array([160, 255, 255])),
    ],
}


# ============================================================
# SHARED STATE
# ============================================================
# This class stores information that different parts of the script
# need to access, such as:
# - current camera status
# - latest detections
# - tip rack analysis
# - last error
#
# Since the camera loop and HTTP server run concurrently, a shared
# object is used to hold the latest results.

class SharedState:
    def __init__(self):
        self.running = True
        self.camera_ok = False
        self.frame_size = None
        self.last_error = None

        self.last_detection = None
        self.boxes = []

        self.last_trigger_response = {
            "orchestrator": None
        }
        self.last_trigger_time = None

        self.last_color_detections = []
        self.last_confirmed_detection = None

        self.last_tiprack_analysis = None
        self.last_tip_occupancy = {}
        self.last_first_available_tip = None


# Global shared state object used across the whole script
state = SharedState()


# ============================================================
# HTTP SERVER HANDLER
# ============================================================
# This small HTTP server lets other services inspect the node.
# For example:
# - /health tells whether the camera node is alive
# - /state returns the latest internal state and detections

class Handler(BaseHTTPRequestHandler):
    def _send_json(self, data, status=200):
        """Helper function to send JSON responses to clients."""
        self.send_response(status)
        self.send_header("Content-type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def do_GET(self):
        """Handle incoming HTTP GET requests."""
        if self.path == "/health":
            # Basic health endpoint used to confirm the service is up
            self._send_json({
                "status": "ok",
                "service": "ot2_camera_node",
                "camera_ok": state.camera_ok,
                "use_llava_object_check": USE_LLAVA_OBJECT_CHECK,
                "hardcoded_tiprack_slot": HARDCODED_TIPRACK_SLOT,
                "hardcoded_dispense_tray_slot": HARDCODED_DISPENSE_TRAY_SLOT
            })

        elif self.path == "/state":
            # Detailed endpoint showing recent detections and analysis
            self._send_json({
                "running": state.running,
                "camera_ok": state.camera_ok,
                "frame_size": state.frame_size,
                "last_detection": state.last_detection,
                "boxes": state.boxes,
                "last_error": state.last_error,
                "last_trigger_response": state.last_trigger_response,
                "last_trigger_time": state.last_trigger_time,
                "last_color_detections": state.last_color_detections,
                "last_confirmed_detection": state.last_confirmed_detection,
                "last_tiprack_analysis": state.last_tiprack_analysis,
                "last_tip_occupancy": state.last_tip_occupancy,
                "last_first_available_tip": state.last_first_available_tip,
                "use_llava_object_check": USE_LLAVA_OBJECT_CHECK,
                "hardcoded_tiprack_slot": HARDCODED_TIPRACK_SLOT,
                "hardcoded_dispense_tray_slot": HARDCODED_DISPENSE_TRAY_SLOT
            })
        else:
            # Return 404 if some other path is requested
            self._send_json({"error": "not found"}, status=404)

    def log_message(self, format, *args):
        # Disable default noisy HTTP logging
        return


# ============================================================
# HELPER FUNCTIONS
# ============================================================
# These utility functions support the main pipeline.

def infer_slot_from_point(cx, cy):
    """
    Given a point (cx, cy) in image coordinates, find the nearest OT-2 slot.

    Returns:
        best_slot: the slot name as a string, such as "1" or "11"
        best_dist: distance in pixels from the point to that slot center

    If the point is too far from every slot center, return None.
    """
    best_slot = None
    best_dist = float("inf")

    for slot, (sx, sy) in SLOT_CENTERS.items():
        d = math.hypot(cx - sx, cy - sy)
        if d < best_dist:
            best_dist = d
            best_slot = slot

    if best_slot is None:
        return None, None

    if best_dist > MAX_SLOT_DISTANCE:
        return None, best_dist

    return best_slot, best_dist


def get_hardcoded_labware_label(slot):
    """
    Since LLaVA is disabled, this function uses simple hardcoded assumptions:
    - slot 1 -> tip rack
    - slot 2 -> dispense tray
    - all others -> neither

    This is useful for testing and teaching the data flow before adding
    more advanced vision-language reasoning.
    """
    if slot == HARDCODED_TIPRACK_SLOT:
        return {
            "ok": True,
            "llava_label": "tip rack",
            "llava_confidence": "hardcoded",
            "raw_response": "hardcoded tip rack slot"
        }

    if slot == HARDCODED_DISPENSE_TRAY_SLOT:
        return {
            "ok": True,
            "llava_label": "dispense tray",
            "llava_confidence": "hardcoded",
            "raw_response": "hardcoded dispense tray slot"
        }

    return {
        "ok": True,
        "llava_label": "neither",
        "llava_confidence": "hardcoded",
        "raw_response": "hardcoded neither"
    }


def classify_labware(frame, x, y, w, h, slot, color_name):
    """
    Placeholder for future labware classification logic.

    Right now, the function simply uses the hardcoded slot-based labeling.
    In a future version, this could run:
    - LLaVA
    - YOLO
    - another classifier
    """
    return get_hardcoded_labware_label(slot)


def notify_orchestrator(payload):
    """
    Send the chosen detection result to the orchestrator using HTTP POST.

    The payload contains information such as:
    - detected color
    - deck slot
    - bounding box
    - chosen tip well
    """
    try:
        r = requests.post(ORCHESTRATOR_TRIGGER_URL, json=payload, timeout=3)
        result = {
            "ok": True,
            "status_code": r.status_code,
            "body": r.text
        }
        print("[TRIGGER] Orchestrator ->", r.status_code, r.text)
        print("[TRIGGER] Payload:", json.dumps(payload, indent=2))
    except Exception as e:
        result = {
            "ok": False,
            "error": str(e)
        }
        print("[TRIGGER] Orchestrator notify failed:", e)

    state.last_trigger_response["orchestrator"] = result
    state.last_trigger_time = time.time()


# ============================================================
# TIP RACK ANALYSIS
# ============================================================
# This section estimates which wells in the hardcoded tip rack
# still contain tips.
#
# Important teaching note:
# This is not using an official OT-2 sensor.
# It is using a vision-based heuristic:
# - brighter cells may indicate a tip
# - stronger structure/edges may indicate a tip
#
# So this is an example of image-based inference, not direct sensing.

def well_to_row_col(well: str):
    """
    Convert a well name like 'A1' or 'H12' into numeric row/column indices.
    Example:
        A1 -> (0, 0)
        B3 -> (1, 2)
    """
    row = TIPRACK_ROWS.index(well[0])
    col = int(well[1:]) - 1
    return row, col


def choose_cluster_center_tip(occupancy):
    """
    Choose an occupied tip that is most central within the cluster
    of occupied wells.

    Why do this?
    Instead of simply choosing the first tip in row-major order,
    this tries to choose a tip from the middle of the occupied region.

    Scoring:
    - more occupied neighbors is better
    - smaller average distance to neighbors is better
    - closer to rack center is used as a tiebreaker
    """
    occupied_wells = [well for well, has_tip in occupancy.items() if has_tip]
    if not occupied_wells:
        return None

    rack_center_row = (TIPRACK_NUM_ROWS - 1) / 2.0
    rack_center_col = (TIPRACK_NUM_COLS - 1) / 2.0

    best_well = None
    best_score = None

    for well in occupied_wells:
        row, col = well_to_row_col(well)

        neighbor_distances = []
        for other in occupied_wells:
            if other == well:
                continue

            other_row, other_col = well_to_row_col(other)
            dist = math.hypot(row - other_row, col - other_col)

            # Only count nearby occupied wells as local neighbors
            if dist <= 2.0:
                neighbor_distances.append(dist)

        neighbor_count = len(neighbor_distances)
        avg_neighbor_dist = (
            sum(neighbor_distances) / neighbor_count
            if neighbor_count > 0 else float("inf")
        )

        dist_to_rack_center = math.hypot(row - rack_center_row, col - rack_center_col)

        score = (
            neighbor_count,         # more neighbors is better
            -avg_neighbor_dist,     # tighter grouping is better
            -dist_to_rack_center    # closer to center is better
        )

        if best_score is None or score > best_score:
            best_score = score
            best_well = well

    return best_well


def get_tiprack_crop(frame):
    """
    Extract a fixed rectangular crop around slot 1.

    Assumption:
    Slot 1 contains the tip rack.

    This is a simple but useful approach for teaching because it avoids
    having to detect the whole rack dynamically first.
    """
    sx, sy = SLOT_CENTERS[TIPRACK_SLOT]
    x1 = max(0, int(sx - TIPRACK_CROP_W // 2))
    y1 = max(0, int(sy - TIPRACK_CROP_H // 2))
    x2 = min(frame.shape[1], int(sx + TIPRACK_CROP_W // 2))
    y2 = min(frame.shape[0], int(sy + TIPRACK_CROP_H // 2))

    crop = frame[y1:y2, x1:x2]
    return crop, (x1, y1, x2 - x1, y2 - y1)


def analyze_tip_cell(cell_bgr):
    """
    Decide whether a single tip-rack cell appears occupied.

    Heuristic idea:
    - a present tip may look brighter
    - a present tip may produce stronger visible structure/edges
    - an empty hole may look darker and less structured

    Returns:
        has_tip: True/False
        metrics: dictionary of debug values
    """
    if cell_bgr.size == 0:
        return False, {
            "mean_intensity": 0.0,
            "edge_strength": 0.0
        }

    gray = cv2.cvtColor(cell_bgr, cv2.COLOR_BGR2GRAY)

    h, w = gray.shape[:2]
    margin_x = max(1, int(w * 0.2))
    margin_y = max(1, int(h * 0.2))
    center = gray[margin_y:h - margin_y, margin_x:w - margin_x]

    if center.size == 0:
        center = gray

    mean_intensity = float(np.mean(center))

    edges = cv2.Canny(center, 50, 150)
    edge_strength = float(np.mean(edges > 0) * 100.0)

    has_tip = (
        mean_intensity >= TIP_PRESENT_INTENSITY_THRESHOLD
        or edge_strength >= TIP_PRESENT_EDGE_THRESHOLD
    )

    return has_tip, {
        "mean_intensity": round(mean_intensity, 2),
        "edge_strength": round(edge_strength, 2)
    }


def analyze_tiprack(frame):
    """
    Analyze the hardcoded tip rack in slot 1.

    Steps:
    1. Crop the tip rack region
    2. Divide it into an 8x12 grid
    3. Inspect each cell
    4. Estimate which wells still contain tips
    5. Choose the best tip according to the cluster-based selection rule
    """
    crop, rack_bbox = get_tiprack_crop(frame)
    x, y, w, h = rack_bbox

    if crop.size == 0 or w <= 0 or h <= 0:
        return {
            "ok": False,
            "rack_bbox": rack_bbox,
            "occupancy": {},
            "first_available_tip": None,
            "debug_cells": {}
        }

    cell_w = w / TIPRACK_NUM_COLS
    cell_h = h / TIPRACK_NUM_ROWS

    occupancy = {}
    debug_cells = {}

    for r in range(TIPRACK_NUM_ROWS):
        row_letter = TIPRACK_ROWS[r]

        for c in range(TIPRACK_NUM_COLS):
            well = f"{row_letter}{c + 1}"

            x1 = int(c * cell_w)
            y1 = int(r * cell_h)
            x2 = int((c + 1) * cell_w)
            y2 = int((r + 1) * cell_h)

            cell = crop[y1:y2, x1:x2]
            has_tip, metrics = analyze_tip_cell(cell)

            occupancy[well] = has_tip
            debug_cells[well] = metrics

    first_available_tip = choose_first_available_tip(occupancy)

    return {
        "ok": True,
        "rack_bbox": rack_bbox,
        "occupancy": occupancy,
        "first_available_tip": first_available_tip,
        "debug_cells": debug_cells
    }


def choose_first_available_tip(occupancy):
    """
    Legacy function name kept for compatibility.

    Important:
    Despite the name, this no longer returns the first tip in simple order.
    It now returns the tip that is most central in the occupied cluster.
    """
    return choose_cluster_center_tip(occupancy)


# ============================================================
# COLOR DETECTION
# ============================================================
# This section performs color-based perception on the full frame.

def build_mask_for_color(hsv, color_name):
    """
    Build a binary mask for a given color.

    Steps:
    1. Use the configured HSV ranges for that color
    2. Combine multiple ranges if needed (example: red wraps around HSV hue)
    3. Clean the mask with morphological operations
    """
    ranges = COLOR_RANGES[color_name]
    mask = None

    for lower, upper in ranges:
        part = cv2.inRange(hsv, lower, upper)
        mask = part if mask is None else (mask + part)

    kernel = np.ones((5, 5), np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
    mask = cv2.morphologyEx(mask, cv2.MORPH_DILATE, kernel)
    return mask


def detect_colored_slots(frame):
    """
    Detect colored regions in the frame and map them to OT-2 slots.

    For each detected contour:
    - measure area
    - compute bounding box and center
    - infer nearest slot
    - assign a simple labware label using hardcoded logic

    Returns:
        a list of detection dictionaries
    """
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    detections = []

    for color_name in COLOR_RANGES.keys():
        mask = build_mask_for_color(hsv, color_name)
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        for cnt in contours:
            area = cv2.contourArea(cnt)
            if area < MIN_COLOR_AREA:
                continue

            x, y, w, h = cv2.boundingRect(cnt)
            cx = int(x + w / 2)
            cy = int(y + h / 2)

            slot, dist = infer_slot_from_point(cx, cy)
            labware_result = classify_labware(frame, x, y, w, h, slot, color_name)

            detections.append({
                "color": color_name,
                "x": int(x),
                "y": int(y),
                "w": int(w),
                "h": int(h),
                "area": float(area),
                "center_x": cx,
                "center_y": cy,
                "slot": slot,
                "slot_distance": None if dist is None else round(dist, 2),
                "llava_label": labware_result["llava_label"],
                "llava_confidence": labware_result["llava_confidence"],
                "llava_ok": labware_result["ok"],
                "llava_raw": labware_result["raw_response"]
            })

    return detections


def choose_best_detection(detections):
    """
    Choose the single best detection from all candidates.

    Why is this needed?
    A frame may contain several blobs, and the script needs one main result
    to send to the orchestrator.

    Scoring idea:
    - assigned slot is better than no slot
    - tip rack label is preferred most
    - dispense tray label is next
    - more nearby detections is better
    - tighter local cluster is better
    - larger area is used as a tiebreaker
    """
    if not detections:
        return None

    for d in detections:
        neighbors = []
        for other in detections:
            if d is other:
                continue

            dist = math.hypot(
                d["center_x"] - other["center_x"],
                d["center_y"] - other["center_y"]
            )

            if dist <= CLUSTER_RADIUS_PX:
                neighbors.append(dist)

        d["cluster_neighbor_count"] = len(neighbors)
        d["cluster_avg_distance"] = sum(neighbors) / len(neighbors) if neighbors else float("inf")

    def score(d):
        slot_score = 1 if d["slot"] is not None else 0

        labware_score = 0
        if d["llava_label"] == "tip rack":
            labware_score = 3
        elif d["llava_label"] == "dispense tray":
            labware_score = 2

        cluster_neighbor_count = d.get("cluster_neighbor_count", 0)
        cluster_avg_distance = d.get("cluster_avg_distance", float("inf"))

        return (
            slot_score,
            labware_score,
            cluster_neighbor_count,
            -cluster_avg_distance,
            d["area"]
        )

    return max(detections, key=score)


# ============================================================
# VISUAL DEBUG / OVERLAY DRAWING
# ============================================================
# These functions draw annotations onto the OpenCV display window
# so students can see what the algorithm is doing in real time.

def draw_tiprack_grid(frame, tiprack_analysis):
    """
    Draw the tip rack bounding box and the 8x12 well grid.

    Color meaning:
    - green = tip present
    - red = no tip
    - yellow = chosen best tip
    """
    if not tiprack_analysis or not tiprack_analysis.get("ok"):
        return

    x, y, w, h = tiprack_analysis["rack_bbox"]
    occupancy = tiprack_analysis["occupancy"]
    first_tip = tiprack_analysis["first_available_tip"]

    cv2.rectangle(frame, (x, y), (x + w, y + h), (255, 255, 0), 2)

    cell_w = w / TIPRACK_NUM_COLS
    cell_h = h / TIPRACK_NUM_ROWS

    for r in range(TIPRACK_NUM_ROWS):
        row_letter = TIPRACK_ROWS[r]
        for c in range(TIPRACK_NUM_COLS):
            well = f"{row_letter}{c + 1}"

            x1 = int(x + c * cell_w)
            y1 = int(y + r * cell_h)
            x2 = int(x + (c + 1) * cell_w)
            y2 = int(y + (r + 1) * cell_h)

            has_tip = occupancy.get(well, False)

            color = (0, 255, 0) if has_tip else (0, 0, 255)
            thickness = 1

            if well == first_tip:
                color = (0, 255, 255)
                thickness = 2

            cv2.rectangle(frame, (x1, y1), (x2, y2), color, thickness)

            if c == 0:
                cv2.putText(
                    frame,
                    row_letter,
                    (x1 - 18, y1 + int(cell_h * 0.65)),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.4,
                    (255, 255, 255),
                    1
                )

            if r == 0:
                cv2.putText(
                    frame,
                    str(c + 1),
                    (x1 + 2, y1 - 5),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.35,
                    (255, 255, 255),
                    1
                )

    if first_tip:
        cv2.putText(
            frame,
            f"First available tip: {first_tip}",
            (x, max(20, y - 10)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            (0, 255, 255),
            2
        )
    else:
        cv2.putText(
            frame,
            "No available tip detected",
            (x, max(20, y - 10)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            (0, 0, 255),
            2
        )


def draw_debug(frame, detections, best_detection, tiprack_analysis):
    """
    Draw all detections plus the chosen best detection.
    This helps explain the decision-making process visually.
    """
    for d in detections:
        x, y, w, h = d["x"], d["y"], d["w"], d["h"]
        color_text = d["color"]
        slot_text = d["slot"] if d["slot"] is not None else "none"

        cv2.rectangle(frame, (x, y), (x + w, y + h), (0, 220, 0), 2)
        cv2.putText(
            frame,
            f"{color_text} slot={slot_text}",
            (x, max(20, y - 12)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            (0, 220, 0),
            2
        )

        cv2.putText(
            frame,
            f"Labware: {d['llava_label']} ({d['llava_confidence']})",
            (x, min(frame.shape[0] - 10, y + h + 20)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            (255, 0, 0),
            2
        )

    if best_detection:
        x, y, w, h = best_detection["x"], best_detection["y"], best_detection["w"], best_detection["h"]
        cv2.rectangle(frame, (x, y), (x + w, y + h), (0, 0, 255), 3)
        cv2.putText(
            frame,
            f"BEST: {best_detection['color']} slot={best_detection['slot']} {best_detection['llava_label']}",
            (x, max(25, y - 28)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.65,
            (0, 0, 255),
            2
        )

    draw_tiprack_grid(frame, tiprack_analysis)


# ============================================================
# MAIN CAMERA PROCESSING LOOP
# ============================================================
# This is the heart of the script.
#
# Repeats continuously:
# 1. Read camera frame
# 2. Detect colored regions
# 3. Analyze tip rack
# 4. Store latest state
# 5. Possibly trigger the orchestrator
# 6. Draw debug overlays

def camera_loop(camera_index=0):
    cap = cv2.VideoCapture(camera_index)

    # Try to set a known capture resolution
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*'MJPG'))

    print(
        "[CAMERA] Resolution set to:",
        cap.get(cv2.CAP_PROP_FRAME_WIDTH),
        "x",
        cap.get(cv2.CAP_PROP_FRAME_HEIGHT)
    )

    if not cap.isOpened():
        state.camera_ok = False
        state.last_error = f"Could not open camera index {camera_index}"
        print("[CAMERA]", state.last_error)
        return

    state.camera_ok = True
    print(f"[CAMERA] Camera opened on index {camera_index}")

    last_trigger_ts = 0.0

    while state.running:
        ret, frame = cap.read()

        if not ret:
            state.camera_ok = False
            state.last_error = "Failed to read frame from camera"
            print("[CAMERA]", state.last_error)
            time.sleep(0.1)
            continue

        state.camera_ok = True
        state.frame_size = {
            "width": int(frame.shape[1]),
            "height": int(frame.shape[0])
        }

        try:
            # Step 1: detect all colored candidate regions
            detections = detect_colored_slots(frame)

            # Step 2: choose the best detection to represent the frame
            best_detection = choose_best_detection(detections)

            # Step 3: analyze tip rack in slot 1
            tiprack_analysis = analyze_tiprack(frame)
            first_tip = tiprack_analysis["first_available_tip"]

            # Step 4: save results into shared state for HTTP inspection
            state.last_color_detections = detections
            state.last_confirmed_detection = best_detection
            state.last_tiprack_analysis = {
                "ok": tiprack_analysis["ok"],
                "rack_bbox": tiprack_analysis["rack_bbox"],
                "first_available_tip": first_tip
            }
            state.last_tip_occupancy = tiprack_analysis["occupancy"]
            state.last_first_available_tip = first_tip

            state.boxes = [
                {
                    "x": d["x"],
                    "y": d["y"],
                    "w": d["w"],
                    "h": d["h"],
                    "area": d["area"],
                    "color": d["color"],
                    "slot": d["slot"],
                    "slot_distance": d["slot_distance"],
                    "center_x": d["center_x"],
                    "center_y": d["center_y"],
                    "llava_label": d["llava_label"],
                    "llava_confidence": d["llava_confidence"]
                }
                for d in detections
            ]

            state.last_detection = {
                "timestamp": time.time(),
                "detected": len(detections) > 0,
                "count": len(detections),
                "best_slot": best_detection["slot"] if best_detection else None,
                "best_color": best_detection["color"] if best_detection else None,
                "best_llava_label": best_detection["llava_label"] if best_detection else None,
                "first_available_tip": first_tip
            }

            now = time.time()
            can_trigger = (now - last_trigger_ts) >= TRIGGER_COOLDOWN_SEC

            # Step 5: only notify orchestrator if:
            # - there is a best detection
            # - it maps to a valid slot
            # - cooldown has expired
            if best_detection and best_detection["slot"] is not None and can_trigger:
                payload = {
                    "event": "deck_color_detected",
                    "color": best_detection["color"],
                    "slot": best_detection["slot"],
                    "slot_distance": best_detection["slot_distance"],
                    "center": {
                        "x": best_detection["center_x"],
                        "y": best_detection["center_y"]
                    },
                    "bbox": {
                        "x": best_detection["x"],
                        "y": best_detection["y"],
                        "w": best_detection["w"],
                        "h": best_detection["h"]
                    },
                    "llava_label": best_detection["llava_label"],
                    "llava_confidence": best_detection["llava_confidence"],
                    "frame_size": state.frame_size,
                    "timestamp": time.time(),
                    "use_llava_object_check": USE_LLAVA_OBJECT_CHECK,
                    "hardcoded_tiprack_slot": HARDCODED_TIPRACK_SLOT,
                    "hardcoded_dispense_tray_slot": HARDCODED_DISPENSE_TRAY_SLOT,
                    "tip_well": first_tip,
                    "tiprack_slot": TIPRACK_SLOT
                }

                print(
                    f"[CONFIRMED] color={best_detection['color']} "
                    f"slot={best_detection['slot']} "
                    f"labware={best_detection['llava_label']} "
                    f"tip_well={first_tip}"
                )

                notify_orchestrator(payload)
                last_trigger_ts = now

            # Step 6: show visual debug output if enabled
            if SHOW_WINDOW:
                draw_debug(frame, detections, best_detection, tiprack_analysis)
                cv2.imshow("OT2 Camera Node - Color + Slot + Tip Selection", frame)

                key = cv2.waitKey(1) & 0xFF
                if key == ord("q"):
                    state.running = False
                    break

        except Exception as e:
            state.last_error = str(e)
            print("[ERROR]", e)
            time.sleep(0.1)

    cap.release()
    cv2.destroyAllWindows()
    print("[CAMERA] Camera loop stopped")


# ============================================================
# HTTP SERVER STARTUP
# ============================================================
# This runs the small monitoring API so other systems can query
# this camera node.

def run_http_server():
    server = HTTPServer(("0.0.0.0", HTTP_PORT), Handler)
    print(f"[HTTP] ot2_camera_node listening on port {HTTP_PORT}")
    server.serve_forever()


# ============================================================
# PROGRAM ENTRY POINT
# ============================================================
# This is where execution starts when the script is run directly.
#
# Design choice:
# - camera loop runs in a background thread
# - HTTP server runs in the main thread
#
# This allows:
# - continuous perception in parallel
# - external status requests at the same time

if __name__ == "__main__":
    try:
        camera_thread = threading.Thread(
            target=camera_loop,
            args=(CAMERA_INDEX,),
            daemon=True
        )
        camera_thread.start()

        run_http_server()

    except KeyboardInterrupt:
        print("[MAIN] Shutting down...")
        state.running = False
    except Exception as e:
        print("[MAIN] Fatal error:", e)
        state.last_error = str(e)
        state.running = False