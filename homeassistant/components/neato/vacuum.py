"""Support for Neato Connected Vacuums."""
from datetime import timedelta
import logging

from pybotvac.exceptions import NeatoRobotException

import voluptuous as vol

from homeassistant.components.vacuum import (
    ATTR_STATUS,
    DOMAIN,
    STATE_CLEANING,
    STATE_DOCKED,
    STATE_ERROR,
    STATE_IDLE,
    STATE_PAUSED,
    STATE_RETURNING,
    SUPPORT_BATTERY,
    SUPPORT_CLEAN_SPOT,
    SUPPORT_LOCATE,
    SUPPORT_MAP,
    SUPPORT_PAUSE,
    SUPPORT_RETURN_HOME,
    SUPPORT_START,
    SUPPORT_STATE,
    SUPPORT_STOP,
    StateVacuumDevice,
)
from homeassistant.const import ATTR_ENTITY_ID, ATTR_MODE
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.service import extract_entity_ids

from .const import (
    ACTION,
    ALERTS,
    ERRORS,
    MODE,
    NEATO_LOGIN,
    NEATO_DOMAIN,
    NEATO_MAP_DATA,
    NEATO_PERSISTENT_MAPS,
    NEATO_ROBOTS,
    SCAN_INTERVAL_MINUTES,
)

_LOGGER = logging.getLogger(__name__)

SCAN_INTERVAL = timedelta(minutes=SCAN_INTERVAL_MINUTES)

SUPPORT_NEATO = (
    SUPPORT_BATTERY
    | SUPPORT_PAUSE
    | SUPPORT_RETURN_HOME
    | SUPPORT_STOP
    | SUPPORT_START
    | SUPPORT_CLEAN_SPOT
    | SUPPORT_STATE
    | SUPPORT_MAP
    | SUPPORT_LOCATE
)

ATTR_CLEAN_START = "clean_start"
ATTR_CLEAN_STOP = "clean_stop"
ATTR_CLEAN_AREA = "clean_area"
ATTR_CLEAN_BATTERY_START = "battery_level_at_clean_start"
ATTR_CLEAN_BATTERY_END = "battery_level_at_clean_end"
ATTR_CLEAN_SUSP_COUNT = "clean_suspension_count"
ATTR_CLEAN_SUSP_TIME = "clean_suspension_time"
ATTR_CLEAN_PAUSE_TIME = "clean_pause_time"
ATTR_CLEAN_ERROR_TIME = "clean_error_time"
ATTR_LAUNCHED_FROM = "launched_from"

ATTR_NAVIGATION = "navigation"
ATTR_CATEGORY = "category"
ATTR_ZONE = "zone"

SERVICE_NEATO_CUSTOM_CLEANING = "neato_custom_cleaning"

SERVICE_NEATO_CUSTOM_CLEANING_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ENTITY_ID): cv.entity_ids,
        vol.Optional(ATTR_MODE, default=2): cv.positive_int,
        vol.Optional(ATTR_NAVIGATION, default=1): cv.positive_int,
        vol.Optional(ATTR_CATEGORY, default=4): cv.positive_int,
        vol.Optional(ATTR_ZONE): cv.string,
    }
)


async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):
    """Set up the Neato vacuum."""
    pass


async def async_setup_entry(hass, entry, async_add_entities):
    """Set up Neato vacuum with config entry."""
    dev = []
    neato = hass.data.get(NEATO_LOGIN)
    mapdata = hass.data.get(NEATO_MAP_DATA)
    persistent_maps = hass.data.get(NEATO_PERSISTENT_MAPS)
    for robot in hass.data[NEATO_ROBOTS]:
        dev.append(NeatoConnectedVacuum(neato, robot, mapdata, persistent_maps))

    if not dev:
        return

    _LOGGER.debug("Adding vacuums %s", dev)
    async_add_entities(dev, True)

    def neato_custom_cleaning_service(call):
        """Zone cleaning service that allows user to change options."""
        for robot in service_to_entities(call):
            if call.service == SERVICE_NEATO_CUSTOM_CLEANING:
                mode = call.data.get(ATTR_MODE)
                navigation = call.data.get(ATTR_NAVIGATION)
                category = call.data.get(ATTR_CATEGORY)
                zone = call.data.get(ATTR_ZONE)
                try:
                    robot.neato_custom_cleaning(mode, navigation, category, zone)
                except NeatoRobotException as ex:
                    _LOGGER.error("Neato vacuum connection error: %s", ex)

    def service_to_entities(call):
        """Return the known devices that a service call mentions."""
        entity_ids = extract_entity_ids(hass, call)
        entities = [entity for entity in dev if entity.entity_id in entity_ids]
        return entities

    hass.services.async_register(
        DOMAIN,
        SERVICE_NEATO_CUSTOM_CLEANING,
        neato_custom_cleaning_service,
        schema=SERVICE_NEATO_CUSTOM_CLEANING_SCHEMA,
    )


class NeatoConnectedVacuum(StateVacuumDevice):
    """Representation of a Neato Connected Vacuum."""

    def __init__(self, neato, robot, mapdata, persistent_maps):
        """Initialize the Neato Connected Vacuum."""
        self.robot = robot
        self.neato = neato
        self._available = self.neato.logged_in if self.neato is not None else False
        self._mapdata = mapdata
        self._name = f"{self.robot.name}"
        self._robot_has_map = self.robot.has_persistent_maps
        self._robot_maps = persistent_maps
        self._robot_serial = self.robot.serial
        self._status_state = None
        self._clean_state = None
        self._state = None
        self._clean_time_start = None
        self._clean_time_stop = None
        self._clean_area = None
        self._clean_battery_start = None
        self._clean_battery_end = None
        self._clean_susp_charge_count = None
        self._clean_susp_time = None
        self._clean_pause_time = None
        self._clean_error_time = None
        self._launched_from = None
        self._battery_level = None
        self._robot_boundaries = {}
        self._robot_stats = None

    def update(self):
        """Update the states of Neato Vacuums."""
        if self.neato is None:
            _LOGGER.error("Error while updating vacuum")
            self._state = None
            self._available = False
            return

        _LOGGER.debug("Running Neato Vacuums update")
        try:
            if self._robot_stats is None:
                self._robot_stats = self.robot.get_robot_info().json()
            self.neato.update_robots()
            self._state = self.robot.state
        except NeatoRobotException as ex:
            if self._available:  # print only once when available
                _LOGGER.error("Neato vacuum connection error: %s", ex)
            self._state = None
            self._available = False
            return

        self._available = True
        _LOGGER.debug("self._state=%s", self._state)
        if "alert" in self._state:
            robot_alert = ALERTS.get(self._state["alert"])
        else:
            robot_alert = None
        if self._state["state"] == 1:
            if self._state["details"]["isCharging"]:
                self._clean_state = STATE_DOCKED
                self._status_state = "Charging"
            elif (
                self._state["details"]["isDocked"]
                and not self._state["details"]["isCharging"]
            ):
                self._clean_state = STATE_DOCKED
                self._status_state = "Docked"
            else:
                self._clean_state = STATE_IDLE
                self._status_state = "Stopped"

            if robot_alert is not None:
                self._status_state = robot_alert
        elif self._state["state"] == 2:
            if robot_alert is None:
                self._clean_state = STATE_CLEANING
                self._status_state = (
                    MODE.get(self._state["cleaning"]["mode"])
                    + " "
                    + ACTION.get(self._state["action"])
                )
            else:
                self._status_state = robot_alert
        elif self._state["state"] == 3:
            self._clean_state = STATE_PAUSED
            self._status_state = "Paused"
        elif self._state["state"] == 4:
            self._clean_state = STATE_ERROR
            self._status_state = ERRORS.get(self._state["error"])

        self._battery_level = self._state["details"]["charge"]

        if not self._mapdata.get(self._robot_serial, {}).get("maps", []):
            return

        mapdata = self._mapdata[self._robot_serial]["maps"][0]
        self._clean_time_start = (mapdata["start_at"].strip("Z")).replace("T", " ")
        self._clean_time_stop = (mapdata["end_at"].strip("Z")).replace("T", " ")
        self._clean_area = mapdata["cleaned_area"]
        self._clean_susp_charge_count = mapdata["suspended_cleaning_charging_count"]
        self._clean_susp_time = mapdata["time_in_suspended_cleaning"]
        self._clean_pause_time = mapdata["time_in_pause"]
        self._clean_error_time = mapdata["time_in_error"]
        self._clean_battery_start = mapdata["run_charge_at_start"]
        self._clean_battery_end = mapdata["run_charge_at_end"]
        self._launched_from = mapdata["launched_from"]

        if (
            self._robot_has_map
            and self._state["availableServices"]["maps"] != "basic-1"
            and self._robot_maps[self._robot_serial]
        ):
            allmaps = self._robot_maps[self._robot_serial]
            for maps in allmaps:
                try:
                    self._robot_boundaries = self.robot.get_map_boundaries(
                        maps["id"]
                    ).json()
                except NeatoRobotException as ex:
                    _LOGGER.error("Could not fetch map boundaries: %s", ex)
                    self._robot_boundaries = {}

    @property
    def name(self):
        """Return the name of the device."""
        return self._name

    @property
    def supported_features(self):
        """Flag vacuum cleaner robot features that are supported."""
        return SUPPORT_NEATO

    @property
    def battery_level(self):
        """Return the battery level of the vacuum cleaner."""
        return self._battery_level

    @property
    def available(self):
        """Return if the robot is available."""
        return self._available

    @property
    def icon(self):
        """Return neato specific icon."""
        return "mdi:robot-vacuum-variant"

    @property
    def state(self):
        """Return the status of the vacuum cleaner."""
        return self._clean_state

    @property
    def unique_id(self):
        """Return a unique ID."""
        return self._robot_serial

    @property
    def device_state_attributes(self):
        """Return the state attributes of the vacuum cleaner."""
        data = {}

        if self._status_state is not None:
            data[ATTR_STATUS] = self._status_state
        if self._clean_time_start is not None:
            data[ATTR_CLEAN_START] = self._clean_time_start
        if self._clean_time_stop is not None:
            data[ATTR_CLEAN_STOP] = self._clean_time_stop
        if self._clean_area is not None:
            data[ATTR_CLEAN_AREA] = self._clean_area
        if self._clean_susp_charge_count is not None:
            data[ATTR_CLEAN_SUSP_COUNT] = self._clean_susp_charge_count
        if self._clean_susp_time is not None:
            data[ATTR_CLEAN_SUSP_TIME] = self._clean_susp_time
        if self._clean_pause_time is not None:
            data[ATTR_CLEAN_PAUSE_TIME] = self._clean_pause_time
        if self._clean_error_time is not None:
            data[ATTR_CLEAN_ERROR_TIME] = self._clean_error_time
        if self._clean_battery_start is not None:
            data[ATTR_CLEAN_BATTERY_START] = self._clean_battery_start
        if self._clean_battery_end is not None:
            data[ATTR_CLEAN_BATTERY_END] = self._clean_battery_end
        if self._launched_from is not None:
            data[ATTR_LAUNCHED_FROM] = self._launched_from

        return data

    @property
    def device_info(self):
        """Device info for neato robot."""
        return {
            "identifiers": {(NEATO_DOMAIN, self._robot_serial)},
            "name": self._name,
            "manufacturer": self._robot_stats["data"]["mfg_name"],
            "model": self._robot_stats["data"]["modelName"],
            "sw_version": self._state["meta"]["firmware"],
        }

    def start(self):
        """Start cleaning or resume cleaning."""
        try:
            if self._state["state"] == 1:
                self.robot.start_cleaning()
            elif self._state["state"] == 3:
                self.robot.resume_cleaning()
        except NeatoRobotException as ex:
            _LOGGER.error("Neato vacuum connection error: %s", ex)

    def pause(self):
        """Pause the vacuum."""
        try:
            self.robot.pause_cleaning()
        except NeatoRobotException as ex:
            _LOGGER.error("Neato vacuum connection error: %s", ex)

    def return_to_base(self, **kwargs):
        """Set the vacuum cleaner to return to the dock."""
        try:
            if self._clean_state == STATE_CLEANING:
                self.robot.pause_cleaning()
            self._clean_state = STATE_RETURNING
            self.robot.send_to_base()
        except NeatoRobotException as ex:
            _LOGGER.error("Neato vacuum connection error: %s", ex)

    def stop(self, **kwargs):
        """Stop the vacuum cleaner."""
        try:
            self.robot.stop_cleaning()
        except NeatoRobotException as ex:
            _LOGGER.error("Neato vacuum connection error: %s", ex)

    def locate(self, **kwargs):
        """Locate the robot by making it emit a sound."""
        try:
            self.robot.locate()
        except NeatoRobotException as ex:
            _LOGGER.error("Neato vacuum connection error: %s", ex)

    def clean_spot(self, **kwargs):
        """Run a spot cleaning starting from the base."""
        try:
            self.robot.start_spot_cleaning()
        except NeatoRobotException as ex:
            _LOGGER.error("Neato vacuum connection error: %s", ex)

    def neato_custom_cleaning(self, mode, navigation, category, zone=None, **kwargs):
        """Zone cleaning service call."""
        boundary_id = None
        if zone is not None:
            for boundary in self._robot_boundaries["data"]["boundaries"]:
                if zone in boundary["name"]:
                    boundary_id = boundary["id"]
            if boundary_id is None:
                _LOGGER.error(
                    "Zone '%s' was not found for the robot '%s'", zone, self._name
                )
                return

        self._clean_state = STATE_CLEANING
        try:
            self.robot.start_cleaning(mode, navigation, category, boundary_id)
        except NeatoRobotException as ex:
            _LOGGER.error("Neato vacuum connection error: %s", ex)
