"""DataUpdateCoordinator for Innotemp."""

from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    CoordinatorEntity,
)
# from homeassistant.util import slugify # For fallback component ID - Assuming this failed
import re # For local slugify
from .const import DOMAIN

# Local slugify implementation as a fallback
def _local_slugify(text: str) -> str:
    """A simple local slugify function."""
    if not text:
        return ""
    text = text.lower()
    # Remove unwanted characters, keep alphanumeric, spaces, and hyphens
    text = re.sub(r"[^\w\s-]", "", text)
    # Replace spaces with hyphens
    text = re.sub(r"\s+", "-", text)
    # Consolidate multiple hyphens
    text = re.sub(r"-+", "-", text)
    # Remove leading/trailing hyphens
    text = text.strip("-")
    return text

class InnotempDataUpdateCoordinator(DataUpdateCoordinator):
    """Innotemp data update coordinator."""

    def __init__(self, hass, logger, api_client):
        """Initialize coordinator."""
        super().__init__(
            hass,
            logger,
            name="Innotemp",
        )
        self.api_client = api_client
        # The data will be updated directly by the API client's SSE callback
        # via the async_set_updated_data method which is part of DataUpdateCoordinator.

    async def _async_update_data(self):
        """Fetch data from API endpoint.

        This method is typically used for polling, but for Innotemp,
        data is pushed via SSE. This method is kept as a formality but
        should not contain polling logic.
        """
        # No polling logic here. Data is pushed via SSE to async_set_updated_data.
        # Return the current state if needed, or None if not required.
        return self.data


class InnotempCoordinatorEntity(CoordinatorEntity):
    """Base entity for Innotemp, inheriting from CoordinatorEntity."""

    def __init__(
        self, coordinator: InnotempDataUpdateCoordinator, config_entry, entity_config
    ):
        """Initialize the entity."""
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._entity_config = entity_config
        self._attr_name = entity_config.get("label")
        self._attr_unique_id = f"{config_entry.unique_id}_{entity_config.get('param')}"

    @property
    def device_info(self):
        """Return device information for the entity."""

        # Check if the entity instance (self) has specific room and component attributes
        # These would have been set by InnotempSwitch or InnotempSensor __init__
        room_attrs = getattr(self, "_room_attributes", None)
        comp_attrs = getattr(self, "_component_attributes", None)

        if room_attrs and isinstance(room_attrs, dict) and room_attrs.get("var"):
            room_var = room_attrs["var"]
            room_label = room_attrs.get("label", "Unknown Room")
            room_type = room_attrs.get("type", "Room")

            if comp_attrs and isinstance(comp_attrs, dict):
                # Entity belongs to a specific component within a room
                comp_var = comp_attrs.get("var")
                comp_type = comp_attrs.get("type")
                comp_label = comp_attrs.get("label")

                # Determine a stable ID for the component device part
                # Prefer 'var', fallback to 'type', then to a slug of label if others missing
                component_stable_id_part = comp_var or comp_type
                if not component_stable_id_part and comp_label: # Fallback to slugified label if no var/type
                    # from homeassistant.helpers.device_registry import slugify # Old import
                    component_stable_id_part = _local_slugify(comp_label) # Use local slugify

                if component_stable_id_part: # Ensure we have something to make it unique
                    device_identifiers = {(DOMAIN, self._config_entry.unique_id, room_var, component_stable_id_part)}
                    device_name = comp_label or f"{room_label} - {component_stable_id_part}"
                    device_model = comp_type or "Component"

                    return {
                        "identifiers": device_identifiers,
                        "name": device_name,
                        "manufacturer": "Innotemp",
                        "model": device_model,
                        "via_device": (DOMAIN, self._config_entry.unique_id, room_var),
                    }

            # Fallback to room-level device if no specific component info or component_stable_id_part is missing
            return {
                "identifiers": {(DOMAIN, self._config_entry.unique_id, room_var)},
                "name": room_label,
                "manufacturer": "Innotemp",
                "model": room_type,
                "via_device": (DOMAIN, self._config_entry.unique_id), # Main controller device
            }

        # Default device for the whole integration if no specific room/component attributes are found
        return {
            "identifiers": {(DOMAIN, self._config_entry.unique_id)},
            "name": "Innotemp Heating Controller", # Main controller device name
            "manufacturer": "Innotemp",
        }
