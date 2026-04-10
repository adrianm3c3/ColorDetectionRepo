from fastapi import FastAPI
import requests
import os

app = FastAPI()

OT2_SIM_BASE = "http://localhost:31950"
RED_PROTOCOL_PATH = "./protocols/red_protocol.py"

state = {
    "last_run": None,
    "last_error": None
}

def upload_protocol(protocol_path: str):
    with open(protocol_path, "rb") as f:
        r = requests.post(
            f"{OT2_SIM_BASE}/protocols",
            files={"files": (os.path.basename(protocol_path), f, "text/x-python")},
            timeout=10
        )
    r.raise_for_status()
    return r.json()["data"]["id"]

def create_run(protocol_id: str):
    r = requests.post(
        f"{OT2_SIM_BASE}/runs",
        json={"data": {"protocolId": protocol_id}},
        timeout=10
    )
    r.raise_for_status()
    return r.json()["data"]["id"]

def play_run(run_id: str):
    r = requests.post(
        f"{OT2_SIM_BASE}/runs/{run_id}/actions",
        json={"data": {"actionType": "play"}},
        timeout=10
    )
    r.raise_for_status()
    return r.json()

@app.get("/health")
def health():
    return {"status": "ok", "service": "ot2_node"}

@app.post("/run/red")
def run_red():
    try:
        protocol_id = upload_protocol(RED_PROTOCOL_PATH)
        run_id = create_run(protocol_id)
        play_response = play_run(run_id)

        state["last_run"] = {
            "protocol_id": protocol_id,
            "run_id": run_id,
            "result": play_response
        }
        state["last_error"] = None

        return {
            "status": "ok",
            "protocol_id": protocol_id,
            "run_id": run_id,
            "result": play_response
        }
    except Exception as e:
        state["last_error"] = str(e)
        return {
            "status": "error",
            "error": str(e)
        }

@app.get("/state")
def get_state():
    return state