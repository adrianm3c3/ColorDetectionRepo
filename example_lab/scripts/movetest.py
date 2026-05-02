import requests
import json

OT2_IP = "169.254.19.251"   # change if needed
PORT = 2006

BASE = f"http://{OT2_IP}:{PORT}"


def run_protocol():
    r = requests.post(BASE + "/run_protocol")
    print(r.json())


def run_file(script_path):
    payload = {"file": script_path}
    r = requests.post(BASE + "/run_file", json=payload)
    print(r.json())


def move_pipette_example():
    # This will call your custom movement endpoint later (we will add it)
    payload = {"cmd": "move_test"}
    r = requests.post(BASE + "/command", json=payload)
    print(r.json())


def main():
    print("OT-2 status:", requests.get(BASE + "/status").json())
    run_protocol()
    move_pipette_example()
    run_file("/data/user_storage/madsci_protocols/move_pipette.py")


if __name__ == "__main__":
    main()
