import requests
import time

ROBOT_IP = "169.254.19.251"
BASE = f"http://{ROBOT_IP}:31950"

HEADERS = {
    "opentrons-version": "*"
}

PROTOCOL_TEXT = b"""
from opentrons import protocol_api
from opentrons.types import Point

metadata = {"apiLevel": "2.15"}

def run(protocol: protocol_api.ProtocolContext):
    protocol.home()

    pipette = protocol.load_instrument("p300_single_gen2", "right")

    # --- Adjust where on deck slot 2 (mm) ---
    X_OFFSET = 15    # +right / -left
    Y_OFFSET = -10   # +back / -front
    Z_HOVER  = 30    # hover height above deck

    slot2 = protocol.deck.position_for("2").move(Point(X_OFFSET, Y_OFFSET, Z_HOVER))
    pipette.move_to(slot2)
    protocol.delay(seconds=1)

    # Slot 10 center hover (no offset)
    slot10 = protocol.deck.position_for("10").move(Point(0, 0, Z_HOVER))
    pipette.move_to(slot10)
    protocol.delay(seconds=1)

    protocol.home()
"""



def check(r: requests.Response):
    if not r.ok:
        print("[HTTP ERROR]", r.status_code)
        print(r.text)
        r.raise_for_status()
    return r.json() if r.text else {}

def main():
    # 1) Upload protocol
    files = {
        "files": ("proof.py", PROTOCOL_TEXT, "text/x-python")
    }

    proto = check(
        requests.post(
            f"{BASE}/protocols",
            headers=HEADERS,
            files=files,
            timeout=30,
        )
    )
    protocol_id = proto["data"]["id"]
    print(f"[OK] Protocol uploaded: {protocol_id}")

    # 2) Create run
    run = check(
        requests.post(
            f"{BASE}/runs",
            headers=HEADERS,
            json={"data": {"protocolId": protocol_id}},
            timeout=30,
        )
    )
    run_id = run["data"]["id"]
    print(f"[OK] Run created: {run_id}")

    # 3) Play run
    check(
        requests.post(
            f"{BASE}/runs/{run_id}/actions",
            headers=HEADERS,
            json={"data": {"actionType": "play"}},
            timeout=30,
        )
    )
    print("[OK] Run started (robot should home)")

    time.sleep(2)

    status = check(
        requests.get(
            f"{BASE}/runs/{run_id}",
            headers=HEADERS,
            timeout=30,
        )
    )
    print("[OK] Run status:", status["data"]["status"])

if __name__ == "__main__":
    main()
