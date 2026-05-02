from opentrons import protocol_api
import json
import os

metadata = {
    "protocolName": "Vision-Driven Motion Protocol",
    "author": "You",
    "description": "Moves pipette based on camera color detection",
    "apiLevel": "2.15"
}

VISION_FILE = "/data/user_storage/vision_result.json"


def run(protocol: protocol_api.ProtocolContext):

    # ---------------------------
    # Load vision result
    # ---------------------------
    if not os.path.exists(VISION_FILE):
        protocol.pause("Vision result not found. Aborting safely.")
        return

    with open(VISION_FILE, "r") as f:
        vision = json.load(f)

    action = vision.get("action", "none")
    protocol.comment(f"Vision action detected: {action}")

    # ---------------------------
    # Load labware
    # ---------------------------
    tiprack = protocol.load_labware(
        "opentrons_96_tiprack_300ul",
        location=1
    )

    plate = protocol.load_labware(
        "corning_96_wellplate_360ul_flat",
        location=2
    )

    pipette = protocol.load_instrument(
        "p300_single",
        mount="right",
        tip_racks=[tiprack]
    )

    # ---------------------------
    # Action selection
    # ---------------------------
    if action == "green":
        protocol.comment("Executing GREEN path")

        pipette.pick_up_tip()
        pipette.aspirate(100, plate["A1"])
        pipette.dispense(100, plate["B1"])
        pipette.drop_tip()

    elif action == "red":
        protocol.comment("Executing RED debug path")

        pipette.pick_up_tip()
        pipette.aspirate(50, plate["C1"])
        pipette.dispense(50, plate["D1"])
        pipette.drop_tip()

    elif action == "blue":
        protocol.comment("Executing BLUE debug path")

        pipette.pick_up_tip()
        pipette.aspirate(20, plate["E1"])
        pipette.dispense(20, plate["F1"])
        pipette.drop_tip()

    else:
        protocol.pause(
            f"No valid action ('{action}') from vision. "
            "Check camera output."
        )
