import json
import requests
from pathlib import Path


ROBOT_IP = "169.254.19.251"
BASE = f"http://{ROBOT_IP}:31950"
HEADERS = {"opentrons-version": "*"}

# ---------------- Vision layer (stub for now) ----------------
def detect_color(path="vision_result.json"):
    p = Path(path)
    if not p.exists():
        raise RuntimeError("vision_result.json not found")

    with p.open() as f:
        data = json.load(f)

    color = data.get("color")
    score = data.get("score", 0.0)

    print(f"[VISION] color={color}, score={score}")

    if color not in {"red", "green", "blue"}:
        raise RuntimeError("No valid color detected")

    return color


# ---------------- Robot control layer ----------------
PROTOCOL_MAP = {
    "red":   "protocol_red.py",
    "blue":  "protocol_blue.py",
    "green": "protocol_green.py",
}

def api(method, path, **kwargs):
    r = requests.request(
        method,
        BASE + path,
        headers=HEADERS,
        timeout=30,
        **kwargs,
    )
    if not r.ok:
        print(r.status_code, r.text)
        r.raise_for_status()
    return r.json() if r.text else {}

def run_protocol(protocol_path: Path):
    proto_bytes = protocol_path.read_bytes()

    # Upload protocol
    proto = api(
        "POST",
        "/protocols",
        files={"files": (protocol_path.name, proto_bytes, "text/x-python")},
    )
    protocol_id = proto["data"]["id"]
    print(f"[OK] Uploaded {protocol_path.name}")

    # Create run
    run = api(
        "POST",
        "/runs",
        json={"data": {"protocolId": protocol_id}},
    )
    run_id = run["data"]["id"]
    print(f"[OK] Run created: {run_id}")

    # Play run
    api(
        "POST",
        f"/runs/{run_id}/actions",
        json={"data": {"actionType": "play"}},
    )
    print("[OK] Run started")

def main():
    color = detect_color()
    print(f"[INFO] Detected color: {color}")

    if color not in PROTOCOL_MAP:
        raise ValueError(f"No protocol mapped for color '{color}'")

    protocol_file = Path(PROTOCOL_MAP[color])
    run_protocol(protocol_file)

if __name__ == "__main__":
    main()
