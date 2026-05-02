# example_lab/scripts/run_opentrons_smoke.py
import os, json, urllib.request, urllib.error

NODE_URL = os.environ.get("NODE_URL", "http://169.254.19.251:2006")

def req(path, method="GET", data=None):
    url = f"{NODE_URL}{path}"
    body = None if data is None else json.dumps(data).encode()
    req = urllib.request.Request(url, data=body, method=method)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req ) as r:
            print(method, url, "->", r.status, r.reason)
            print(r.read().decode())
    except urllib.error.HTTPError as e:
        print(method, url, "->", e.code, e.reason)
        print(e.read().decode())
    except Exception as e:
        print(method, url, "-> ERROR:", e)

print("NODE_URL =", NODE_URL)
req("/status", "GET")
req("/info", "GET")
req("/run_protocol", "POST", {"test": True})

# --- NEW: run a real protocol file on the OT-2 ---
req("/run_file", "POST", {
    "file": "/data/user_storage/madsci_protocols/move_pipette.py"
})
