from fastapi import FastAPI, UploadFile, File, HTTPException
from opentrons.simulate import simulate
import tempfile
import uuid
import os

app = FastAPI()

protocols = {}
runs = {}

@app.post("/protocols")
async def upload_protocol(files: UploadFile = File(...)):
    contents = await files.read()

    with tempfile.NamedTemporaryFile(delete=False, suffix=".py") as f:
        f.write(contents)
        protocol_path = f.name

    protocol_id = str(uuid.uuid4())
    protocols[protocol_id] = {
        "id": protocol_id,
        "filename": files.filename,
        "path": protocol_path
    }

    return {
        "data": {
            "id": protocol_id,
            "filename": files.filename
        }
    }


@app.post("/runs")
def create_run(payload: dict):
    data = payload.get("data", {})
    protocol_id = data.get("protocolId")

    if not protocol_id or protocol_id not in protocols:
        raise HTTPException(status_code=400, detail="invalid or missing protocolId")

    run_id = str(uuid.uuid4())
    runs[run_id] = {
        "id": run_id,
        "protocolId": protocol_id,
        "status": "idle",
        "log": []
    }

    return {
        "data": {
            "id": run_id,
            "protocolId": protocol_id,
            "status": "idle"
        }
    }


@app.post("/runs/{run_id}/actions")
def run_action(run_id: str, payload: dict):
    if run_id not in runs:
        raise HTTPException(status_code=404, detail="run not found")

    data = payload.get("data", {})
    action_type = data.get("actionType")

    if action_type != "play":
        raise HTTPException(status_code=400, detail="only play supported in simulator")

    protocol_id = runs[run_id]["protocolId"]
    protocol_path = protocols[protocol_id]["path"]

    try:
        with open(protocol_path, "r", encoding="utf-8") as f:
            runlog, _bundle = simulate(f)

        steps = [str(step) for step in runlog]

        runs[run_id]["status"] = "succeeded"
        runs[run_id]["log"] = steps

        return {
            "data": {
                "runId": run_id,
                "status": "succeeded",
                "actionType": "play",
                "log": steps
            }
        }

    except Exception as e:
        runs[run_id]["status"] = "failed"
        runs[run_id]["log"] = [str(e)]
        raise HTTPException(status_code=500, detail=f"simulation failed: {e}")


@app.get("/runs/{run_id}")
def get_run(run_id: str):
    if run_id not in runs:
        raise HTTPException(status_code=404, detail="run not found")
    return {"data": runs[run_id]}