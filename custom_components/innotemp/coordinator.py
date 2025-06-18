"""DataUpdateCoordinator for Innotemp."""

from homeassistant.helpers.update_coordinator import (
from .const import DOMAIN
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    CoordinatorEntity,
)

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

    async def start_sse_listener(self):
        """Start the SSE listener in the API client."""
        # Pass the coordinator's async_set_updated_data method as the callback
        await self.api_client.async_sse_connect(self.async_set_updated_data)

    async def stop_sse_listener(self):
        """Stop the SSE listener in the API client."""
        await self.api_client.async_sse_disconnect()


class InnotempCoordinatorEntity(CoordinatorEntity):
    """Base entity for Innotemp, inheriting from CoordinatorEntity."""

    def __init__(self, coordinator: InnotempDataUpdateCoordinator, config_entry, entity_config):
        """Initialize the entity."""
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._entity_config = entity_config
        self._attr_name = entity_config.get("label")
        self._attr_unique_id = f"{config_entry.unique_id}_{entity_config.get('param')}"

    @property
    def device_info(self):
        """Return device information."""
        # This assumes a single device for the integration instance
        return {
            "identifiers": {(DOMAIN, self._config_entry.unique_id)},
            "name": "Innotemp Heating Controller",
            "manufacturer": "Innotemp", # Replace with actual manufacturer if known
            # You might want to add model and firmware version if available from config
        }