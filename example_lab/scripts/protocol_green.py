from opentrons import protocol_api

metadata = {"apiLevel": "2.15"}

def run(protocol: protocol_api.ProtocolContext):
    # ---- CONFIG ----
    COLOR = "red"   # injected later by controller
    VOLUME = 100
    ROWS = ["A", "B", "C"]

    COLOR_TO_COLUMN = {
        "red": 5,
        "green": 12,
        "blue": 8,
    }

    protocol.comment(f"Color selected: {COLOR}")

    # ---- SETUP ----
    protocol.home()

    tiprack = protocol.load_labware(
        "opentrons_96_tiprack_300ul",
        "11"
    )

    pipette = protocol.load_instrument(
        "p300_single_gen2",
        mount="right",
        tip_racks=[tiprack]
    )

    # 🔴 SKIP A1 EXPLICITLY
    pipette.starting_tip = tiprack["B1"]

    source = protocol.load_labware(
        "corning_96_wellplate_360ul_flat",
        "1"
    )

    dest = protocol.load_labware(
        "corning_96_wellplate_360ul_flat",
        "10"
    )

    # ---- LOGIC ----
    column = COLOR_TO_COLUMN[COLOR]

    pipette.pick_up_tip()

    # Aspirate once from center-ish well
    pipette.aspirate(
        VOLUME * len(ROWS),
        source["E6"].bottom(2)
    )

    # Dispense into A?, B?, C? based on color
    for row in ROWS:
        well_name = f"{row}{column}"
        pipette.dispense(
            VOLUME,
            dest[well_name].bottom(2)
        )

    pipette.drop_tip()
    protocol.home()
