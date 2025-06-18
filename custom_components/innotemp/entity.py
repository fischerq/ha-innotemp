"""Base entity for the Innotemp integration."""

from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .coordinator import InnotempDataUpdateCoordinator


class InnotempEntity(CoordinatorEntity[InnotempDataUpdateCoordinator]):
    """Base class for Innotemp entities."""

    _attr_attribution = "Data provided by Innotemp Heating Controller"

    def __init__(
        self, coordinator: InnotempDataUpdateCoordinator, unique_id: str
    ) -> None:
        """Initialize the Innotemp entity."""
        super().__init__(coordinator)
        self._attr_unique_id = unique_id
