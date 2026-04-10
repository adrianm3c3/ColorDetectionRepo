from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
import requests
import os
import time
import threading
from pathlib import Path



app = FastAPI()



OT2_SERVER_BASE = "http://169.254.19.251:31950"
OT2_HEADERS = {
    "opentrons-version": "2"
}

X3_SERVER_BASE = "http://192.168.1.11:5000"
ENABLE_X3 = True

HOLD_OFF_SECONDS = 15
POST_RUN_COOLDOWN_SECONDS = 10


COLOR_OBJECT_WAIT_SECONDS = 2.5

EVENT_PRIORITY = [
    "person_detected",
    "bottle_detected",
    "orange_detected",
    "red_detected",
    "yellow_detected",
    "purple_detected",
    "blue_detected",
    "green_detected",
]




BASE_DIR = Path(__file__).resolve().parent
PROTOCOL_DIR = BASE_DIR / "protocols"

PROTOCOL_MAP = {
    "orange_detected": PROTOCOL_DIR / "orange_protocol.py",
    "red_detected": PROTOCOL_DIR / "red_protocol.py",
    "yellow_detected": PROTOCOL_DIR / "yellow_protocol.py",
    "purple_detected": PROTOCOL_DIR / "purple_protocol.py",
    "blue_detected": PROTOCOL_DIR / "blue_protocol.py",
    "green_detected": PROTOCOL_DIR / "green_protocol.py",
    "bottle_detected": PROTOCOL_DIR / "bottle_protocol.py",
    "person_detected": PROTOCOL_DIR / "person_protocol.py",
}



class VisionEventPayload(BaseModel):
    event: str = "vision_detected"
    timestamp: Optional[float] = None
    source: Optional[str] = None

    colors_detected: List[str] = Field(default_factory=list)
    color_boxes: List[Dict[str, Any]] = Field(default_factory=list)

    objects_detected: List[str] = Field(default_factory=list)
    object_boxes: List[Dict[str, Any]] = Field(default_factory=list)


class RunEventPayload(BaseModel):
    event: str




execution_lock = threading.Lock()

state = {
    "busy": False,
    "busy_reason": None,
    "busy_until": 0.0,

    "current_run_id": None,
    "current_protocol_id": None,
    "current_event": None,

    "last_action": None,
    "last_time": None,
    "last_input": None,
    "last_result": None,
    "last_error": None,

    "last_candidate_events": [],
    "last_selected_event": None,
    "last_x3_action": None,

    "pending_color_event": None,
    "pending_color_started_at": None,
    "pending_color_expires_at": None,
    "pending_color_payload": None,
    "pending_color_list": [],
    "pending_object_seen_during_window": False,
}




def debug(msg: str):
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] [ORCHESTRATOR] {msg}", flush=True)


def now_ts() -> float:
    return time.time()


def retry_after_seconds() -> int:
    remaining = int(max(0, state["busy_until"] - now_ts()))
    if remaining <= 0:
        remaining = HOLD_OFF_SECONDS
    return remaining


def set_busy(reason: str, hold_for_seconds: int = HOLD_OFF_SECONDS):
    state["busy"] = True
    state["busy_reason"] = reason
    state["busy_until"] = now_ts() + hold_for_seconds
    debug(f"STATE -> busy=True, reason={reason}, hold_for_seconds={hold_for_seconds}")


def clear_busy():
    debug("STATE -> clearing busy state")
    state["busy"] = False
    state["busy_reason"] = None
    state["busy_until"] = 0.0


def set_post_run_cooldown():
    state["busy"] = True
    state["busy_reason"] = "post_run_cooldown"
    state["busy_until"] = now_ts() + POST_RUN_COOLDOWN_SECONDS
    debug(f"STATE -> post run cooldown for {POST_RUN_COOLDOWN_SECONDS} seconds")


def is_local_holdoff_active() -> bool:
    active = state["busy"] and now_ts() < state["busy_until"]
    debug(f"is_local_holdoff_active -> {active}")
    return active


def hold_off_response(reason: str, hold_seconds: int | None = None) -> dict:
    if hold_seconds is None:
        hold_seconds = retry_after_seconds()

    debug(f"Returning hold_off response: reason={reason}, retry_after_seconds={hold_seconds}")

    return {
        "status": "hold_off",
        "reason": reason,
        "retry_after_seconds": int(hold_seconds),
        "busy": state["busy"],
        "busy_reason": state["busy_reason"],
        "current_run_id": state["current_run_id"]
    }



def normalize_label(label: str) -> str:
    return label.strip().lower().replace(" ", "_")


def build_candidate_events(colors_detected: List[str], objects_detected: List[str]) -> List[str]:
    candidates = []

    for obj in objects_detected:
        obj_name = normalize_label(obj)
        candidates.append(f"{obj_name}_detected")

    for color in colors_detected:
        color_name = normalize_label(color)
        candidates.append(f"{color_name}_detected")

    deduped = []
    seen = set()
    for c in candidates:
        if c not in seen:
            deduped.append(c)
            seen.add(c)

    debug(f"build_candidate_events -> {deduped}")
    return deduped


def select_best_event(candidate_events: List[str]) -> Optional[str]:
    if not candidate_events:
        debug("select_best_event -> no candidates")
        return None

    for priority_event in EVENT_PRIORITY:
        if priority_event in candidate_events:
            debug(f"select_best_event -> selected by priority: {priority_event}")
            return priority_event

    for event in candidate_events:
        if event in PROTOCOL_MAP:
            debug(f"select_best_event -> selected by map fallback: {event}")
            return event

    debug("select_best_event -> no mapped event found")
    return None


def resolve_protocol_path(event_name: str) -> Optional[str]:
    path = PROTOCOL_MAP.get(event_name)
    debug(f"resolve_protocol_path -> event={event_name}, path={path}")
    return path


def decide_x3_action(colors_detected: List[str], objects_detected: List[str]) -> Optional[str]:
    if objects_detected:
        return "left_right"
    if colors_detected:
        return "up_down"
    return None


def clear_pending_color():
    debug("Clearing pending color window state")
    state["pending_color_event"] = None
    state["pending_color_started_at"] = None
    state["pending_color_expires_at"] = None
    state["pending_color_payload"] = None
    state["pending_color_list"] = []
    state["pending_object_seen_during_window"] = False


def start_pending_color_window(selected_event: str, payload_dict: dict, color_list: List[str]):
    now = now_ts()
    state["pending_color_event"] = selected_event
    state["pending_color_started_at"] = now
    state["pending_color_expires_at"] = now + COLOR_OBJECT_WAIT_SECONDS
    state["pending_color_payload"] = payload_dict
    state["pending_color_list"] = list(color_list)
    state["pending_object_seen_during_window"] = False

    debug(
        f"Started pending color window -> event={selected_event}, "
        f"colors={color_list}, expires_in={COLOR_OBJECT_WAIT_SECONDS}s"
    )


def pending_color_active() -> bool:
    expires_at = state["pending_color_expires_at"]
    if expires_at is None:
        return False
    return now_ts() < expires_at


def pending_color_expired() -> bool:
    expires_at = state["pending_color_expires_at"]
    if expires_at is None:
        return False
    return now_ts() >= expires_at




def upload_protocol_to_ot2(protocol_path: str) -> str:
    debug(f"Uploading protocol to OT-2: {protocol_path}")

    with open(protocol_path, "rb") as f:
        files = {
            "files": (os.path.basename(protocol_path), f, "text/x-python")
        }

        response = requests.post(
            f"{OT2_SERVER_BASE}/protocols",
            files=files,
            headers=OT2_HEADERS,
            timeout=30
        )

    debug(f"OT-2 /protocols response -> status={response.status_code}, body={response.text}")

    if response.status_code not in (200, 201):
        raise Exception(f"protocol upload failed: {response.status_code} {response.text}")

    body = response.json()
    protocol_id = body["data"]["id"]
    debug(f"Uploaded protocol successfully -> protocol_id={protocol_id}")
    return protocol_id


def create_run(protocol_id: str) -> str | None:
    debug(f"Creating OT-2 run for protocol_id={protocol_id}")

    payload = {
        "data": {
            "protocolId": protocol_id
        }
    }

    response = requests.post(
        f"{OT2_SERVER_BASE}/runs",
        json=payload,
        headers=OT2_HEADERS,
        timeout=30
    )

    debug(f"OT-2 /runs response -> status={response.status_code}, body={response.text}")

    if response.status_code == 409:
        debug("OT-2 returned 409 -> active run already exists")
        return None

    if response.status_code not in (200, 201):
        raise Exception(f"run creation failed: {response.status_code} {response.text}")

    body = response.json()
    run_id = body["data"]["id"]
    debug(f"Run created successfully -> run_id={run_id}")
    return run_id


def play_run(run_id: str) -> dict:
    debug(f"Sending play action for run_id={run_id}")

    payload = {
        "data": {
            "actionType": "play"
        }
    }

    response = requests.post(
        f"{OT2_SERVER_BASE}/runs/{run_id}/actions",
        json=payload,
        headers=OT2_HEADERS,
        timeout=120
    )

    debug(f"OT-2 play response -> status={response.status_code}, body={response.text}")

    if response.status_code not in (200, 201):
        raise Exception(f"run play failed: {response.status_code} {response.text}")

    return response.json()


def get_run_status(run_id: str) -> dict:
    response = requests.get(
        f"{OT2_SERVER_BASE}/runs/{run_id}",
        headers=OT2_HEADERS,
        timeout=30
    )

    debug(f"OT-2 get_run_status -> run_id={run_id}, status={response.status_code}")

    if response.status_code != 200:
        raise Exception(f"get run status failed: {response.status_code} {response.text}")

    return response.json()


def list_runs() -> dict:
    response = requests.get(
        f"{OT2_SERVER_BASE}/runs",
        headers=OT2_HEADERS,
        timeout=30
    )

    debug(f"OT-2 list_runs -> status={response.status_code}")

    if response.status_code != 200:
        raise Exception(f"list runs failed: {response.status_code} {response.text}")

    return response.json()


def robot_has_active_run() -> bool:
    debug("Checking whether OT-2 already has an active run")
    data = list_runs()
    runs = data.get("data", [])

    for run in runs:
        run_id = run.get("id")
        status = run.get("status", "").lower()
        debug(f"Run scan -> run_id={run_id}, status={status}")

        if status not in ("succeeded", "failed", "stopped"):
            debug("Found active OT-2 run")
            return True

    debug("No active OT-2 runs found")
    return False


def wait_for_run_completion(run_id: str, poll_seconds: int = 2, timeout_seconds: int = 600) -> dict:
    debug(f"Waiting for run completion -> run_id={run_id}, timeout={timeout_seconds}s")
    start = now_ts()

    while True:
        run_status = get_run_status(run_id)
        status = run_status.get("data", {}).get("status", "").lower()
        debug(f"Polling run -> run_id={run_id}, status={status}")

        if status in ("succeeded", "failed", "stopped"):
            debug(f"Run reached terminal state -> {status}")
            return run_status

        if now_ts() - start > timeout_seconds:
            raise Exception(f"run {run_id} did not complete within {timeout_seconds} seconds")

        time.sleep(poll_seconds)



def notify_x3_action(action_name: str) -> dict:
    url = f"{X3_SERVER_BASE}/actions/{action_name}"
    debug(f"Notifying X3 -> url={url}")

    try:
        response = requests.post(url, timeout=5)
        result = {
            "ok": True,
            "status_code": response.status_code,
            "body": response.text,
            "action": action_name
        }
        state["last_x3_action"] = action_name
        debug(f"X3 notify success -> {result}")
        return result
    except Exception as e:
        result = {
            "ok": False,
            "error": str(e),
            "action": action_name
        }
        debug(f"X3 notify failed -> {result}")
        return result




def execute_event(event_name: str, original_payload: dict | None = None, x3_action: str | None = None) -> dict:
    debug("=" * 80)
    debug(f"execute_event START -> event_name={event_name}, x3_action={x3_action}")

    protocol_path = resolve_protocol_path(event_name)

    state["last_action"] = event_name
    state["last_time"] = now_ts()
    state["last_input"] = original_payload
    state["last_error"] = None
    state["current_event"] = event_name

    if not protocol_path:
        debug(f"No protocol mapped for event -> {event_name}")
        raise HTTPException(status_code=400, detail=f"No protocol mapped for event '{event_name}'")

    if not os.path.exists(protocol_path):
        debug(f"Protocol file missing -> {protocol_path}")
        raise HTTPException(status_code=500, detail=f"Protocol file not found: {protocol_path}")

    if is_local_holdoff_active():
        return hold_off_response("local_holdoff_active")

    got_lock = execution_lock.acquire(blocking=False)
    if not got_lock:
        set_busy("orchestrator_locked", HOLD_OFF_SECONDS)
        return hold_off_response("orchestrator_locked", HOLD_OFF_SECONDS)

    try:
        x3_result = None
        if ENABLE_X3 and x3_action:
            x3_result = notify_x3_action(x3_action)

        set_busy("checking_robot_state", HOLD_OFF_SECONDS)

        try:
            if robot_has_active_run():
                set_busy("ot2_active_run", HOLD_OFF_SECONDS)
                return {
                    **hold_off_response("ot2_active_run", HOLD_OFF_SECONDS),
                    "x3_result": x3_result
                }
        except Exception as e:
            state["last_error"] = str(e)
            debug(f"Robot state check failed -> {e}")
            set_busy("ot2_state_check_failed", HOLD_OFF_SECONDS)
            return {
                **hold_off_response("ot2_state_check_failed", HOLD_OFF_SECONDS),
                "x3_result": x3_result
            }

        set_busy("uploading_protocol", HOLD_OFF_SECONDS)
        protocol_id = upload_protocol_to_ot2(protocol_path)
        state["current_protocol_id"] = protocol_id

        set_busy("creating_run", HOLD_OFF_SECONDS)
        run_id = create_run(protocol_id)
        if run_id is None:
            set_busy("ot2_active_run", HOLD_OFF_SECONDS)
            return {
                **hold_off_response("ot2_active_run", HOLD_OFF_SECONDS),
                "x3_result": x3_result
            }

        state["current_run_id"] = run_id

        set_busy("playing_run", HOLD_OFF_SECONDS)
        play_result = play_run(run_id)

        set_busy("waiting_for_run_completion", HOLD_OFF_SECONDS)
        run_status = wait_for_run_completion(run_id)

        result = {
            "status": "completed",
            "event": event_name,
            "protocol_path": protocol_path,
            "protocol_id": protocol_id,
            "run_id": run_id,
            "play_result": play_result,
            "run_status": run_status,
            "x3_result": x3_result
        }

        state["last_result"] = result
        state["last_selected_event"] = event_name

        debug(f"execute_event SUCCESS -> event_name={event_name}, run_id={run_id}")

        set_post_run_cooldown()
        return result

    except Exception as e:
        state["last_error"] = str(e)
        state["last_result"] = {
            "status": "failed",
            "event": event_name,
            "error": str(e)
        }

        debug(f"execute_event FAILED -> event_name={event_name}, error={e}")

        if "RunAlreadyActive" in str(e) or "409" in str(e):
            set_busy("ot2_active_run", HOLD_OFF_SECONDS)
            return hold_off_response("ot2_active_run", HOLD_OFF_SECONDS)

        raise HTTPException(status_code=500, detail=str(e))

    finally:
        if not is_local_holdoff_active():
            clear_busy()

        state["current_run_id"] = None
        state["current_protocol_id"] = None
        state["current_event"] = None

        execution_lock.release()
        debug(f"execute_event END -> event_name={event_name}")
        debug("=" * 80)


#color detection logic

def maybe_execute_pending_color() -> Optional[dict]:
   
    if state["pending_color_event"] is None:
        return None

    if pending_color_active():
        remaining = max(0.0, state["pending_color_expires_at"] - now_ts())
        debug(f"Pending color still waiting -> remaining={remaining:.2f}s")
        return {
            "status": "waiting_for_object_window",
            "reason": "color_detected_waiting_for_possible_object",
            "pending_event": state["pending_color_event"],
            "seconds_remaining": round(remaining, 2),
            "object_seen_during_window": state["pending_object_seen_during_window"]
        }

    
    pending_event = state["pending_color_event"]
    pending_payload = state["pending_color_payload"]
    pending_colors = state["pending_color_list"]
    saw_object = state["pending_object_seen_during_window"]

    if saw_object:
        debug(
            f"Pending color cancelled -> event={pending_event}, "
            f"colors={pending_colors}, reason=object_seen_during_window"
        )
        result = {
            "status": "blocked",
            "reason": "object_detected_during_color_wait_window",
            "message": "Cannot execute protocol because an object was detected during the color wait window.",
            "pending_event": pending_event,
            "colors_detected": pending_colors
        }
        clear_pending_color()
        state["last_result"] = result
        return result

    debug(
        f"Pending color approved -> event={pending_event}, "
        f"colors={pending_colors}, no object seen in wait window"
    )
    clear_pending_color()
    return execute_event(
        pending_event,
        original_payload=pending_payload,
        x3_action="up_down"
    )



@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": "madsci_orchestrator",
        "ot2_server_base": OT2_SERVER_BASE,
        "x3_server_base": X3_SERVER_BASE,
        "x3_enabled": ENABLE_X3,
        "busy": state["busy"],
        "busy_reason": state["busy_reason"],
        "busy_until": state["busy_until"],
        "current_run_id": state["current_run_id"],
        "last_selected_event": state["last_selected_event"],
        "last_x3_action": state["last_x3_action"],
        "pending_color_event": state["pending_color_event"],
        "pending_color_expires_at": state["pending_color_expires_at"],
        "pending_object_seen_during_window": state["pending_object_seen_during_window"]
    }


@app.get("/state")
def get_state():
    return state


@app.post("/actions/run_event")
def run_event(payload: RunEventPayload):
    debug(f"/actions/run_event called -> payload={payload.dict()}")
    return execute_event(payload.event, original_payload=payload.dict(), x3_action=None)


@app.post("/events/vision")
def vision_event(payload: VisionEventPayload):
    payload_dict = payload.dict()
    debug(f"/events/vision called -> payload={payload_dict}")

    colors = payload.colors_detected or []
    objects = payload.objects_detected or []

    state["last_input"] = payload_dict
    state["last_time"] = now_ts()

    if state["pending_color_event"] is not None:
        if objects:
            state["pending_object_seen_during_window"] = True
            debug(
                f"Object detected during pending color window -> "
                f"objects={objects}, pending_event={state['pending_color_event']}"
            )

            result = {
                "status": "blocked",
                "reason": "object_detected_during_color_wait_window",
                "message": "Cannot execute protocol because an object was detected while waiting on a color-only trigger.",
                "pending_event": state["pending_color_event"],
                "objects_detected": objects,
                "colors_detected": state["pending_color_list"]
            }

            state["last_result"] = result
            clear_pending_color()
            return result

        pending_result = maybe_execute_pending_color()
        if pending_result is not None:
            return pending_result

    candidate_events = build_candidate_events(colors_detected=colors, objects_detected=objects)
    state["last_candidate_events"] = candidate_events

    if not candidate_events:
        debug("No colors or objects detected -> nothing to do")
        return {
            "status": "ignored",
            "reason": "no_candidate_events",
            "candidate_events": [],
            "selected_event": None,
            "x3_action": None
        }

    
    if colors and objects:
        debug(
            f"Blocked execution because color and object were both detected -> "
            f"colors={colors}, objects={objects}"
        )

        result = {
            "status": "blocked",
            "reason": "color_and_object_detected_together",
            "message": "Cannot execute protocol when both a color and an object are detected together.",
            "colors_detected": colors,
            "objects_detected": objects,
            "candidate_events": candidate_events
        }
        state["last_result"] = result
        return result

    
    if objects and not colors:
        debug(f"Object-only detection -> blocking protocol execution, objects={objects}")

        result = {
            "status": "blocked",
            "reason": "object_detected_no_protocol_execution",
            "message": "Object detected. Protocol execution is blocked.",
            "objects_detected": objects,
            "candidate_events": candidate_events
        }
        state["last_result"] = result
        return result

    
    if colors and not objects:
        selected_event = select_best_event(candidate_events)
        state["last_selected_event"] = selected_event

        if not selected_event:
            debug("Color detected but no mapped color protocol found")
            return {
                "status": "ignored",
                "reason": "no_mapped_protocol",
                "candidate_events": candidate_events,
                "selected_event": None
            }

        start_pending_color_window(
            selected_event=selected_event,
            payload_dict=payload_dict,
            color_list=colors
        )

        return {
            "status": "waiting_for_object_window",
            "reason": "color_detected_waiting_for_possible_object",
            "message": "Color detected. Waiting before execution to confirm no object is also detected.",
            "selected_event": selected_event,
            "colors_detected": colors,
            "seconds_remaining": COLOR_OBJECT_WAIT_SECONDS
        }


    debug("Reached fallback path -> ignoring")
    return {
        "status": "ignored",
        "reason": "unhandled_detection_combination",
        "candidate_events": candidate_events
    }