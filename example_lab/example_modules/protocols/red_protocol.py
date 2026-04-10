metadata = {
    "apiLevel": "2.15",
    "protocolName": "Red Protocol"
}

def run(protocol):
    tiprack = protocol.load_labware("opentrons_96_tiprack_300ul", 11)
    reservoir = protocol.load_labware("nest_12_reservoir_15ml", 2)
    plate = protocol.load_labware("corning_96_wellplate_360ul_flat", 1)
    pipette = protocol.load_instrument("p300_single_gen2", "right", tip_racks=[tiprack])

    protocol.comment("RED detected: transfer to A1")
    pipette.pick_up_tip()
    pipette.aspirate(100, reservoir["A1"])
    pipette.dispense(100, plate["A1"])
    pipette.drop_tip()