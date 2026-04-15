# ============================================================
# MADSCI ORCHESTRATOR
# ============================================================
# Purpose:
# This script acts as the central coordinator between perception
# systems (camera nodes), robotic devices (OT-2 liquid handler),
# and mobile robots (Yahboom X3).
#
# Responsibilities of this orchestrator:
# 1. Receive perception events from camera nodes
# 2. Decide which event has priority
# 3. Determine whether execution should happen
# 4. Generate a dynamic OT-2 protocol when needed
# 5. Send commands to OT-2 and optionally the X3 robot
# 6. Manage concurrency and safety windows (cooldowns / hold-offs)
#
# Conceptually this script represents the:
#   "Decision Layer" of the robotics system.
#
# Pipeline:
# Perception → Orchestrator Decision → Robot Execution
# ============================================================


from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
import requests
import os
import time
import threading
from pathlib import Path

# ============================================================
# FASTAPI APPLICATION
# ============================================================
# FastAPI provides a lightweight HTTP server that allows other
# components (camera nodes, robots, monitoring tools) to send
# requests to this orchestrator.

app = FastAPI()


# ============================================================
# DEVICE CONNECTION SETTINGS
# ============================================================
# These URLs define where external systems are located.

OT2_SERVER_BASE = "http://169.254.19.251:31950"
# Address of the Opentrons OT-2 robot HTTP API

OT2_HEADERS = {
    "opentrons-version": "2"
}
# Required header when interacting with the OT-2 API

X3_SERVER_BASE = "http://192.168.1.11:5000"
# Address of the Yahboom X3 robot controller

ENABLE_X3 = True
# Toggle to enable/disable X3 robot communication


# ============================================================
# TIMING AND EXECUTION SAFETY PARAMETERS
# ============================================================
# These values prevent unsafe rapid triggering of robot actions.

HOLD_OFF_SECONDS = 15
# Minimum delay between certain orchestrator operations

POST_RUN_COOLDOWN_SECONDS = 10
# Cooldown after OT-2 protocol finishes

COLOR_OBJECT_WAIT_SECONDS = 2.5
# When a color is detected, wait this long to see if an object
# also appears (prevents incorrect protocol execution)


# ============================================================
# EVENT PRIORITY
# ============================================================
# If multiple events are detected simultaneously, this list
# determines which one takes precedence.

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


# ============================================================
# FILE SYSTEM PATHS
# ============================================================
# These paths store robot protocol files.

BASE_DIR = Path(__file__).resolve().parent

PROTOCOL_DIR = BASE_DIR / "protocols"
# Directory containing static OT-2 protocol scripts

GENERATED_PROTOCOL_DIR = BASE_DIR / "generated_protocols"
# Directory where dynamically generated protocols are saved

GENERATED_PROTOCOL_DIR.mkdir(exist_ok=True)


# ============================================================
# STATIC PROTOCOL MAPPING
# ============================================================
# These events correspond to pre-written OT-2 scripts.

FIXED_PROTOCOL_MAP = {
    "bottle_detected": PROTOCOL_DIR / "bottle_protocol.py",
    "person_detected": PROTOCOL_DIR / "person_protocol.py",
}


# ============================================================
# COLOR EVENTS
# ============================================================
# Color detections trigger dynamically generated protocols.

COLOR_EVENTS = {
    "orange_detected",
    "red_detected",
    "yellow_detected",
    "purple_detected",
    "blue_detected",
    "green_detected",
}


TREAT_LABWARE_AS_OBJECTS = False
# If enabled, labware detection could also trigger object logic


# ============================================================
# OT-2 DEFAULT LABWARE CONFIGURATION
# ============================================================
# Default deck layout used when building dynamic protocols.

DEFAULT_TIPRACK_SLOT = "11"
DEFAULT_SOURCE_LABWARE_SLOT = "2"
DEFAULT_DESTINATION_LABWARE_SLOT = "1"

TIPRACK_LABWARE_NAME = "opentrons_96_tiprack_300ul"
SOURCE_LABWARE_NAME = "nest_12_reservoir_15ml"
DESTINATION_LABWARE_NAME = "corning_96_wellplate_360ul_flat"

PIPETTE_NAME = "p300_single_gen2"
PIPETTE_MOUNT = "right"


# Default liquid handling parameters
DEFAULT_ASPIRATE_WELL = "A1"
DEFAULT_VOLUME_UL = 100
DEFAULT_TIP_WELL = "A1"


# ============================================================
# COLOR → WELL MAPPING
# ============================================================
# Each detected color determines the well used for dispensing.

COLOR_TO_DEST_WELL = {
    "red": "A1",
    "blue": "A2",
    "green": "A3",
    "yellow": "A4",
    "orange": "A5",
    "purple": "A6",
}