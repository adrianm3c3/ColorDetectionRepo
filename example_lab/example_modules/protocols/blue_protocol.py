metadata = {
    "apiLevel": "2.15",
    "protocolName": "Blue Protocol"
}

def run(protocol):
    tiprack = protocol.load_labware("opentrons_96_tiprack_300ul", 11)
    reservoir = protocol.load_labware("nest_12_reservoir_15ml", 2)
    plate = protocol.load_labware("corning_96_wellplate_360ul_flat", 1)
    pipette = protocol.load_instrument("p300_single_gen2", "right", tip_racks=[tiprack])

    protocol.comment("BLue detected: transfer to A1") 
    pipette.pick_up_tip()
    pipette.aspirate(100, reservoir["A2"])
    pipette.dispense(100, plate["A2"])
    pipette.drop_tip()