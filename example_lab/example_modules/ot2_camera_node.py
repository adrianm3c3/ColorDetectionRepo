from http.server import BaseHTTPRequestHandler, HTTPServer
import threading
import json
import cv2
import numpy as np
import time
import requests

try:
    from ultralytics import YOLO
except ImportError:
    YOLO = None


HTTP_HOST = "0.0.0.0"
HTTP_PORT = 2011

CAMERA_INDEX = 1


MADSCI_TRIGGER_URL = "http://127.0.0.1:2020/events/vision"


YOLO_MODEL_NAME = "yolo11n.pt"


ENABLE_OBJECT_DETECTION = True

YOLO_EVERY_N_FRAMES = 3
YOLO_CONFIDENCE = 0.45
MIN_COLOR_AREA = 500


EVENT_COOLDOWN_SECONDS = 2.0
ENABLE_COLOR_DETECTION = True


class SharedState:
    def __init__(self):
        self.running = True
        self.camera_ok = False
        self.frame_size = None
        self.last_error = None

        self.last_detection = {
            "timestamp": None,
            "colors_detected": [],
            "color_count": 0,
            "objects_detected": [],
            "object_count": 0
        }

        self.color_boxes = []
        self.object_boxes = []

        self.last_trigger_response = {
            "madsci": None
        }
        self.last_trigger_time = None

        self.frame_counter = 0


state = SharedState()

class Handler(BaseHTTPRequestHandler):
    def _send_json(self, data, status=200):
        self.send_response(status)
        self.send_header("Content-type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def do_GET(self):
        if self.path == "/health":
            self._send_json({
                "status": "ok",
                "service": "ot2_camera_multicolor_yolo_node",
                "camera_ok": state.camera_ok,
                "object_detection_enabled": ENABLE_OBJECT_DETECTION,
                "yolo_loaded": YOLO is not None
            })

        elif self.path == "/state":
            self._send_json({
                "running": state.running,
                "camera_ok": state.camera_ok,
                "frame_size": state.frame_size,
                "last_detection": state.last_detection,
                "color_boxes": state.color_boxes,
                "object_boxes": state.object_boxes,
                "last_error": state.last_error,
                "last_trigger_response": state.last_trigger_response,
                "last_trigger_time": state.last_trigger_time,
                "frame_counter": state.frame_counter
            })

        else:
            self._send_json({"error": "not found"}, status=404)

    def log_message(self, format, *args):
        return


#notify orchestrator

def notify_madsci(event_type, colors, color_boxes, objects, object_boxes):
    payload = {
        "event": event_type,
        "timestamp": time.time(),
        "source": "ot2_camera_multicolor_yolo_node",
        "colors_detected": colors,
        "color_boxes": color_boxes,
        "objects_detected": objects,
        "object_boxes": object_boxes
    }

    try:
        r = requests.post(MADSCI_TRIGGER_URL, json=payload, timeout=10)
        result = {
            "ok": True,
            "status_code": r.status_code,
            "body": r.text
        }
        print("MADSCI ->", r.status_code, r.text)
    except Exception as e:
        result = {
            "ok": False,
            "error": str(e)
        }
        print("MADSCI notify failed:", e)

    state.last_trigger_response = {
        "madsci": result
    }
    state.last_trigger_time = time.time()


#color detection helpers

def get_color_ranges():
    """
    HSV ranges for:
    orange, red, yellow, purple, blue, green

    These are starter thresholds.
    You will likely need to tune them for your camera, lighting, and surface color.
    """

    return {
        "red": [
            (np.array([0, 120, 70]),   np.array([10, 255, 255])),
            (np.array([170, 120, 70]), np.array([180, 255, 255]))
        ],
        "orange": [
            (np.array([10, 120, 100]), np.array([22, 255, 255]))
        ],
        "yellow": [
            (np.array([22, 120, 120]), np.array([35, 255, 255]))
        ],
        "green": [
            (np.array([40, 70, 70]),   np.array([85, 255, 255]))
        ],
        "blue": [
            (np.array([95, 120, 70]),  np.array([130, 255, 255]))
        ],
        "purple": [
            (np.array([130, 70, 70]),  np.array([160, 255, 255]))
        ]
    }


DRAW_COLORS_BGR = {
    "red": (0, 0, 255),
    "orange": (0, 165, 255),
    "yellow": (0, 255, 255),
    "green": (0, 255, 0),
    "blue": (255, 0, 0),
    "purple": (255, 0, 255)
}


def build_mask_for_color(hsv, ranges):
    mask_total = None
    for lower, upper in ranges:
        mask = cv2.inRange(hsv, lower, upper)
        if mask_total is None:
            mask_total = mask
        else:
            mask_total = cv2.bitwise_or(mask_total, mask)

    kernel = np.ones((5, 5), np.uint8)
    mask_total = cv2.morphologyEx(mask_total, cv2.MORPH_OPEN, kernel)
    mask_total = cv2.morphologyEx(mask_total, cv2.MORPH_DILATE, kernel)
    return mask_total


def detect_colors(frame):
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)

    color_ranges = get_color_ranges()
    color_boxes = []
    colors_detected = []

    for color_name, ranges in color_ranges.items():
        mask = build_mask_for_color(hsv, ranges)
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        for cnt in contours:
            area = cv2.contourArea(cnt)
            if area < MIN_COLOR_AREA:
                continue

            x, y, w, h = cv2.boundingRect(cnt)

            entry = {
                "color": color_name,
                "x": int(x),
                "y": int(y),
                "w": int(w),
                "h": int(h),
                "area": float(area)
            }
            color_boxes.append(entry)
            colors_detected.append(color_name)

            draw_color = DRAW_COLORS_BGR.get(color_name, (255, 255, 255))
            cv2.rectangle(frame, (x, y), (x + w, y + h), draw_color, 2)
            label = f"{color_name.upper()} area={int(area)}"
            cv2.putText(
                frame,
                label,
                (x, max(20, y - 10)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.55,
                draw_color,
                2
            )

    colors_detected = sorted(list(set(colors_detected)))
    return colors_detected, color_boxes

#objection detection helpers

def load_yolo_model():
    if not ENABLE_OBJECT_DETECTION:
        print("Object detection disabled")
        return None

    if YOLO is None:
        print("Ultralytics is not installed. Object detection disabled.")
        return None

    try:
        model = YOLO(YOLO_MODEL_NAME)
        print(f"Loaded YOLO model: {YOLO_MODEL_NAME}")
        return model
    except Exception as e:
        state.last_error = f"Failed to load YOLO model: {e}"
        print(state.last_error)
        return None


def detect_objects_yolo(model, frame):
    if model is None:
        return [], []

    object_boxes = []
    objects_detected = []

    try:
        results = model.predict(
            source=frame,
            conf=YOLO_CONFIDENCE,
            verbose=False
        )

        if not results:
            return [], []

        result = results[0]
        names = result.names

        if result.boxes is None:
            return [], []

        boxes_xyxy = result.boxes.xyxy.cpu().numpy() if hasattr(result.boxes.xyxy, "cpu") else result.boxes.xyxy
        confs = result.boxes.conf.cpu().numpy() if hasattr(result.boxes.conf, "cpu") else result.boxes.conf
        clss = result.boxes.cls.cpu().numpy() if hasattr(result.boxes.cls, "cpu") else result.boxes.cls

        for box, conf, cls_idx in zip(boxes_xyxy, confs, clss):
            x1, y1, x2, y2 = box
            cls_idx = int(cls_idx)
            label = names.get(cls_idx, str(cls_idx)) if isinstance(names, dict) else names[cls_idx]

            entry = {
                "label": str(label),
                "confidence": float(conf),
                "x1": int(x1),
                "y1": int(y1),
                "x2": int(x2),
                "y2": int(y2),
                "w": int(x2 - x1),
                "h": int(y2 - y1)
            }

            object_boxes.append(entry)
            objects_detected.append(str(label))

            cv2.rectangle(frame, (int(x1), int(y1)), (int(x2), int(y2)), (255, 255, 255), 2)
            text = f"{label} {conf:.2f}"
            cv2.putText(
                frame,
                text,
                (int(x1), max(20, int(y1) - 10)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.55,
                (255, 255, 255),
                2
            )

    except Exception as e:
        state.last_error = f"YOLO inference error: {e}"

    objects_detected = sorted(list(set(objects_detected)))
    return objects_detected, object_boxes


#main camera loop

def camera_loop(camera_index=CAMERA_INDEX):
    cap = cv2.VideoCapture(camera_index)

    if not cap.isOpened():
        state.camera_ok = False
        state.last_error = f"Could not open camera index {camera_index}"
        print(state.last_error)
        return

    model = load_yolo_model()

    state.camera_ok = True
    print(f"Camera opened on index {camera_index}")

    last_event_sent_at = 0.0
    cached_objects_detected = []
    cached_object_boxes = []

    while state.running:
        ret, frame = cap.read()

        if not ret:
            state.camera_ok = False
            state.last_error = "Failed to read frame from camera"
            print(state.last_error)
            time.sleep(0.1)
            continue

        state.camera_ok = True
        state.last_error = None
        state.frame_counter += 1

        state.frame_size = {
            "width": int(frame.shape[1]),
            "height": int(frame.shape[0])
        }

        # --- Color detection ---
        colors_detected = []
        color_boxes = []
        if ENABLE_COLOR_DETECTION:
            colors_detected, color_boxes = detect_colors(frame)

        # --- Object detection ---
        if ENABLE_OBJECT_DETECTION and (state.frame_counter % YOLO_EVERY_N_FRAMES == 0):
            objects_detected, object_boxes = detect_objects_yolo(model, frame)
            cached_objects_detected = objects_detected
            cached_object_boxes = object_boxes
        else:
            objects_detected = cached_objects_detected
            object_boxes = cached_object_boxes

        state.color_boxes = color_boxes
        state.object_boxes = object_boxes
        state.last_detection = {
            "timestamp": time.time(),
            "colors_detected": colors_detected,
            "color_count": len(color_boxes),
            "objects_detected": objects_detected,
            "object_count": len(object_boxes)
        }

        # decide whether to notify orchestrator
        should_notify = (len(colors_detected) > 0) or (len(objects_detected) > 0)

        if should_notify:
            now = time.time()
            if now - last_event_sent_at >= EVENT_COOLDOWN_SECONDS:
                notify_madsci(
                    event_type="vision_detected",
                    colors=colors_detected,
                    color_boxes=color_boxes,
                    objects=objects_detected,
                    object_boxes=object_boxes
                )
                last_event_sent_at = now

        cv2.imshow("OT2 Camera Node - Colors + YOLO Objects", frame)

        key = cv2.waitKey(1) & 0xFF
        if key == ord("q"):
            state.running = False
            break

    cap.release()
    cv2.destroyAllWindows()
    print("Camera loop stopped")


#http server

def run_http_server():
    server = HTTPServer((HTTP_HOST, HTTP_PORT), Handler)
    print(f"ot2_camera_multicolor_yolo_node listening on port {HTTP_PORT}")
    server.serve_forever()


#entry

if __name__ == "__main__":
    camera_thread = threading.Thread(target=camera_loop, daemon=True)
    camera_thread.start()

    try:
        run_http_server()
    except KeyboardInterrupt:
        print("Shutting down...")
        state.running = False