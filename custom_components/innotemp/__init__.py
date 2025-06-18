"""The Innotemp Heating Controller integration."""

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .api import InnotempApiClient
from .coordinator import InnotempDataUpdateCoordinator
from .const import DOMAIN

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.SWITCH]

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Innotemp Heating Controller from a config entry."""

    hass.data.setdefault(DOMAIN, {})  # type: ignore[no-untyped-call]

    host = entry.data["host"]
    username = entry.data["username"]
    password = entry.data["password"]

    api_client = InnotempApiClient(hass, host, username, password)

    # Login and fetch initial configuration
    try:
        await api_client.async_login()
        config_data = await api_client.async_get_config()
        _LOGGER.debug("Initial configuration fetched: %s", config_data)
    except Exception as ex:
        _LOGGER.error("Failed to connect and fetch initial config: %s", ex)
        return False

    coordinator = InnotempDataUpdateCoordinator(hass, api_client)

    # Pass the coordinator's async_set_updated_data as the callback for SSE
    coordinator.sse_task = hass.async_create_task(
        api_client.async_sse_connect(coordinator.async_set_updated_data)
    )

    hass.data[DOMAIN][entry.entry_id] = {
        "api": api_client,
        "coordinator": coordinator,
        "config": config_data,  # Store config data for entity creation
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    await coordinator.async_config_entry_first_refresh()

    return True
