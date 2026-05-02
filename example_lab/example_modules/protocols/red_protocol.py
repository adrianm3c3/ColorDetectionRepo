metadata = {
    "apiLevel": "2.15",
    "protocolName": "Dynamic Color Protocol"
}

# These values should be filled in by the orchestrator before upload
CONFIG = {
    "detected_color": "red",

    "tiprack_slot": 11,
    "source_labware_slot": 2,
    "destination_labware_slot": 1,

    "source_labware_name": "nest_12_reservoir_15ml",
    "destination_labware_name": "corning_96_wellplate_360ul_flat",
    "tiprack_labware_name": "opentrons_96_tiprack_300ul",

    "pipette_name": "p300_single_gen2",
    "pipette_mount": "right",

    "aspirate_well": "A1",
    "dispense_well": "A1",
    "volume_ul": 100
}


def run(protocol):
    detected_color = CONFIG["detected_color"]

    tiprack_slot = str(CONFIG["tiprack_slot"])
    source_labware_slot = str(CONFIG["source_labware_slot"])
    destination_labware_slot = str(CONFIG["destination_labware_slot"])

    tiprack_labware_name = CONFIG["tiprack_labware_name"]
    source_labware_name = CONFIG["source_labware_name"]
    destination_labware_name = CONFIG["destination_labware_name"]

    pipette_name = CONFIG["pipette_name"]
    pipette_mount = CONFIG["pipette_mount"]

    aspirate_well = CONFIG["aspirate_well"]
    dispense_well = CONFIG["dispense_well"]
    volume_ul = CONFIG["volume_ul"]

    protocol.comment(f"{detected_color.upper()} detected")
    protocol.comment(f"Tip rack slot: {tiprack_slot}")
    protocol.comment(f"Source labware slot: {source_labware_slot}")
    protocol.comment(f"Destination labware slot: {destination_labware_slot}")
    protocol.comment(f"Aspirate from: {aspirate_well}")
    protocol.comment(f"Dispense to: {dispense_well}")
    protocol.comment(f"Volume: {volume_ul} uL")

    tiprack = protocol.load_labware(tiprack_labware_name, tiprack_slot)
    source_labware = protocol.load_labware(source_labware_name, source_labware_slot)
    destination_labware = protocol.load_labware(destination_labware_name, destination_labware_slot)

    pipette = protocol.load_instrument(
        pipette_name,
        pipette_mount,
        tip_racks=[tiprack]
    )

    pipette.pick_up_tip()
    pipette.aspirate(volume_ul, source_labware[aspirate_well])
    pipette.dispense(volume_ul, destination_labware[dispense_well])
    pipette.drop_tip()

    protocol.home()