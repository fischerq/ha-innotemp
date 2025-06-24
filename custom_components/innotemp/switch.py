"""Platform for switch entities."""

from __future__ import annotations

import logging

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import InnotempDataUpdateCoordinator, InnotempCoordinatorEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up switch entities based on config entry."""
    integration_data = hass.data[DOMAIN][entry.entry_id]
    coordinator: InnotempDataUpdateCoordinator = integration_data["coordinator"]
    config_data: dict = integration_data["config"]

    if config_data is None:
        _LOGGER.warning(
            "Innotemp switch setup: config_data is None, skipping switch entity creation."
        )
        async_add_entities([])  # Add no entities
        return

    entities = []

    # Assuming coordinator.config holds the parsed configuration from async_get_config
    # This will need to be adapted based on the actual structure of the config data
    for room_id, room_data in config_data.items():
        _LOGGER.debug(f"Processing room_id: {room_id}, room_data type: {type(room_data)}, room_data: {room_data}")

        # Attempt to parse room_data if it's a string (JSON)
        if isinstance(room_data, str):
            try:
                import json
                room_data = json.loads(room_data)
                _LOGGER.debug(f"Successfully parsed room_data string for room_id: {room_id}")
            except json.JSONDecodeError:
                _LOGGER.error(f"Failed to parse room_data string for room_id: {room_id}. Data: {room_data}", exc_info=True)
                continue # Skip this room if data is malformed

        if not isinstance(room_data, dict):
            _LOGGER.error(f"Skipping room_id: {room_id} because room_data is not a dictionary. Type: {type(room_data)}, Data: {room_data}")
            continue

        for param_id, param_data in room_data.get("parameters", {}).items():
            if param_data.get("type") == "ONOFFAUTO":  # Example type check
                entities.append(
                    InnotempSwitch(coordinator, entry, room_id, param_id, param_data)
                )

    async_add_entities(entities)


class InnotempSwitch(InnotempCoordinatorEntity, SwitchEntity):
    """Representation of an Innotemp Switch."""

    def __init__(
        self,
        coordinator: InnotempDataUpdateCoordinator,
        config_entry: ConfigEntry,
        room_id: str,
        param_id: str,
        param_data: dict,
    ) -> None:
        """Initialize the switch."""
        entity_config = {
            "param": param_id,
            "label": param_data.get("label", f"Innotemp Switch {param_id}"),
            "room_id": room_id,
            # Add any other relevant parts of param_data if needed by base class
        }
        super().__init__(coordinator, config_entry, entity_config)
        self._room_id = room_id
        self._param_id = param_id
        self._attr_unique_id = (
            f"{config_entry.unique_id}_{self._room_id}_{self._param_id}"
        )
        self._attr_name = entity_config["label"]

        # Get initial state from the coordinator's data
        self._update_state_from_coordinator()

    @property
    def is_on(self) -> bool | None:
        """Return true if switch is on."""
        # Assuming the value in coordinator.data corresponds to the ON/OFF state
        # This mapping (0=OFF, 1=ON, 2=AUTO) needs to be handled based on the API spec
        current_value = self.coordinator.data.get(self._param_id)
        if current_value is not None:
            # Map API value to Home Assistant state
            return current_value == 1
        return None

    async def async_turn_on(self, **kwargs) -> None:
        """Turn the switch on."""
        current_value = self.coordinator.data.get(self._param_id)
        if current_value is None:
            _LOGGER.warning(f"Cannot turn on {self.entity_id}, current state unknown")
            return

        # Assuming 1 is ON, 0 is OFF, 2 is AUTO
        if current_value != 1:
            await self.coordinator.api_client.async_send_command(
                room_id=self._room_id,
                param=self._param_id,
                val_new=1,
                val_prev=current_value,
            )
            # The state will be updated by the SSE listener
            # self.async_write_ha_state()

    async def async_turn_off(self, **kwargs) -> None:
        """Turn the switch off."""
        current_value = self.coordinator.data.get(self._param_id)
        if current_value is None:
            _LOGGER.warning(f"Cannot turn off {self.entity_id}, current state unknown")
            return

        # Assuming 1 is ON, 0 is OFF, 2 is AUTO
        if current_value != 0:
            await self.coordinator.api_client.async_send_command(
                room_id=self._room_id,
                param=self._param_id,
                val_new=0,
                val_prev=current_value,
            )
            # The state will be updated by the SSE listener
            # self.async_write_ha_state()

    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self._update_state_from_coordinator()
        self.async_write_ha_state()
        super()._handle_coordinator_update()

    def _update_state_from_coordinator(self) -> None:
        """Update the entity's state from coordinator data."""
        # The base InnotempEntity might handle the core data lookup
        # Here, we specifically handle the switch state mapping
        pass  # State is handled by the is_on property
