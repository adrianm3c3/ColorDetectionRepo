metadata = {
    "apiLevel": "2.15",
    "protocolName": "Dynamic Color Protocol - RED"
}

CONFIG = {
    "detected_color": "red",
    "event_name": "red_detected",
    "tiprack_slot": "1",
    "tip_well": "G6",
    "source_labware_slot": "7",
    "destination_labware_slot": "2",
    "tiprack_labware_name": "opentrons_96_tiprack_300ul",
    "source_labware_name": "nest_12_reservoir_15ml",
    "destination_labware_name": "corning_96_wellplate_360ul_flat",
    "pipette_name": "p300_single_gen2",
    "pipette_mount": "right",
    "aspirate_well": "A1",
    "dispense_well": "A1",
    "volume_ul": 100
}

def run(protocol):
    protocol.comment(f"Dynamic event: {CONFIG['event_name']}")
    protocol.comment(f"Detected color: {CONFIG['detected_color']}")
    protocol.comment(f"Tip rack slot: {CONFIG['tiprack_slot']}")
    protocol.comment(f"Tip well: {CONFIG['tip_well']}")
    protocol.comment(f"Source labware slot: {CONFIG['source_labware_slot']}")
    protocol.comment(f"Destination labware slot: {CONFIG['destination_labware_slot']}")
    protocol.comment(f"Aspirate well: {CONFIG['aspirate_well']}")
    protocol.comment(f"Dispense well: {CONFIG['dispense_well']}")
    protocol.comment(f"Volume: {CONFIG['volume_ul']} uL")

    tiprack = protocol.load_labware(
        CONFIG["tiprack_labware_name"],
        CONFIG["tiprack_slot"]
    )

    source_labware = protocol.load_labware(
        CONFIG["source_labware_name"],
        CONFIG["source_labware_slot"]
    )

    destination_labware = protocol.load_labware(
        CONFIG["destination_labware_name"],
        CONFIG["destination_labware_slot"]
    )

    pipette = protocol.load_instrument(
        CONFIG["pipette_name"],
        CONFIG["pipette_mount"],
        tip_racks=[tiprack]
    )

    pipette.pick_up_tip(tiprack[CONFIG["tip_well"]])
    pipette.aspirate(CONFIG["volume_ul"], source_labware[CONFIG["aspirate_well"]])
    pipette.dispense(CONFIG["volume_ul"], destination_labware[CONFIG["dispense_well"]])
    pipette.drop_tip()

    protocol.home()
