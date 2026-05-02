from opentrons import protocol_api
from opentrons.types import Point

metadata = {"apiLevel": "2.15"}

def run(protocol: protocol_api.ProtocolContext):
    protocol.comment("BLue: slot 1 -> slot 10")

    protocol.home()
    pipette = protocol.load_instrument("p300_single_gen2", "right")

    HOVER_Z = 30
    OFFSET_X = 15
    OFFSET_Y = -10

    start = protocol.deck.position_for("1").move(Point(0, 0, HOVER_Z))
    dest  = protocol.deck.position_for("10").move(Point(OFFSET_X, OFFSET_Y, HOVER_Z))

    pipette.move_to(start)
    protocol.delay(seconds=1)

    pipette.move_to(dest)
    protocol.delay(seconds=1)

    protocol.home()
