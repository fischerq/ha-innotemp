"""The Innotemp Heating Controller integration."""

import logging

import aiohttp

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_create_clientsession

from .api import InnotempApiClient, InnotempApiError, normalize_host
from .coordinator import InnotempDataUpdateCoordinator
from .const import DOMAIN
from .api_parser import extract_initial_states, create_control_state_map

PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.SELECT,
    Platform.NUMBER,
    Platform.SWITCH,
]

_LOGGER = logging.getLogger(__name__)

# Prefix every entity unique id had before config entries got a unique id
# (``f"{config_entry.unique_id}_{param}"`` with ``unique_id is None``).
LEGACY_UNIQUE_ID_PREFIX = "None_"


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate old config entries.

    1.1 -> 1.2: entries created before the config flow set a unique id have
    ``unique_id=None``, and all their entities were registered as
    ``None_<param>``. Give the entry its host-based unique id (unless another
    entry already owns it) and rename the registered entities to
    ``<entry unique id or entry_id>_<param>`` *in place*, so entity ids,
    history and customisations are kept and no duplicate entities appear.
    """
    if entry.version > 1:
        # Downgrade from a future version: refuse rather than corrupt.
        return False

    if entry.version == 1 and entry.minor_version < 2:
        new_unique_id = entry.unique_id
        if new_unique_id is None:
            try:
                candidate = normalize_host(entry.data.get("host"))
            except ValueError:
                candidate = None
            taken = candidate is not None and any(
                other.unique_id == candidate
                for other in hass.config_entries.async_entries(DOMAIN)
                if other.entry_id != entry.entry_id
            )
            if candidate and not taken:
                new_unique_id = candidate
            elif taken:
                _LOGGER.warning(
                    "Another Innotemp entry already uses host %s; entry %s keeps "
                    "an entry_id-based identity",
                    candidate,
                    entry.entry_id,
                )

        prefix = new_unique_id or entry.entry_id
        ent_reg = er.async_get(hass)

        @callback
        def _migrate_unique_id(reg_entry: er.RegistryEntry) -> dict | None:
            old = reg_entry.unique_id
            if not old.startswith(LEGACY_UNIQUE_ID_PREFIX):
                return None
            new = f"{prefix}_{old[len(LEGACY_UNIQUE_ID_PREFIX):]}"
            existing = ent_reg.async_get_entity_id(reg_entry.domain, DOMAIN, new)
            if existing:
                _LOGGER.warning(
                    "Cannot migrate %s (%s) to unique id %s: already used by %s",
                    reg_entry.entity_id,
                    old,
                    new,
                    existing,
                )
                return None
            _LOGGER.debug(
                "Migrating %s unique id %s -> %s", reg_entry.entity_id, old, new
            )
            return {"new_unique_id": new}

        await er.async_migrate_entries(hass, entry.entry_id, _migrate_unique_id)
        hass.config_entries.async_update_entry(
            entry, unique_id=new_unique_id, minor_version=2
        )
        _LOGGER.info(
            "Migrated Innotemp entry %s to version 1.2 (unique id %s)",
            entry.entry_id,
            new_unique_id,
        )

    return True


@callback
def _async_register_devices(
    hass: HomeAssistant, entry: ConfigEntry, config_data: dict
) -> None:
    """Create the controller device and one device per room.

    Entity device_info points at these via ``via_device``; without them HA
    warns that the parent device does not exist (and will stop accepting it).
    """
    dev_reg = dr.async_get(hass)
    dev_reg.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
        name="Innotemp Heating Controller",
        manufacturer="Innotemp",
    )
    for rooms in config_data.values():
        if not isinstance(rooms, list):
            continue
        for room in rooms:
            attrs = room.get("@attributes", {}) if isinstance(room, dict) else {}
            room_var = attrs.get("var")
            if not room_var:
                continue
            dev_reg.async_get_or_create(
                config_entry_id=entry.entry_id,
                identifiers={(DOMAIN, entry.entry_id, room_var)},
                name=attrs.get("label") or room_var,
                manufacturer="Innotemp",
                model=attrs.get("type") or "Room",
                via_device=(DOMAIN, entry.entry_id),
            )


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Innotemp Heating Controller from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    try:
        host = normalize_host(entry.data.get("host"))
    except ValueError as err:
        # A broken stored host cannot fix itself; retrying would be pointless.
        _LOGGER.error(
            "Stored Innotemp host %r is invalid (%s). Please remove and re-add "
            "the integration.",
            entry.data.get("host"),
            err,
        )
        return False

    username = entry.data["username"]
    password = entry.data["password"]

    # The Innotemp controller authenticates via a PHPSESSID cookie and is
    # typically addressed by a bare IP. aiohttp's default CookieJar silently
    # *discards* cookies set by IP hosts (unsafe=False), so the shared HA
    # session never kept the session cookie. Use a dedicated session with an
    # unsafe cookie jar so the session cookie survives.
    session = async_create_clientsession(
        hass, cookie_jar=aiohttp.CookieJar(unsafe=True)
    )
    api_client = InnotempApiClient(session, host, username, password)

    try:
        await api_client.async_login()
        config_data = await api_client.async_get_config()
    except InnotempApiError as err:
        # (The session is detached by HA when setup fails.) Raising ConfigEntryNotReady makes HA retry with backoff, so an
        # integration whose controller is offline at boot recovers on its own.
        raise ConfigEntryNotReady(
            f"Cannot connect to Innotemp controller at {host}: {err}"
        ) from err
    if not config_data:
        raise ConfigEntryNotReady(
            f"Innotemp controller at {host} returned no configuration"
        )

    coordinator = InnotempDataUpdateCoordinator(
        hass, _LOGGER, api_client, config_entry=entry
    )

    initial_states = extract_initial_states(config_data)
    if initial_states:
        coordinator.async_set_updated_data(initial_states)
    coordinator.control_to_state_map = create_control_state_map(config_data)

    hass.data[DOMAIN][entry.entry_id] = {
        "api": api_client,
        "coordinator": coordinator,
        "config": config_data,
        "session": session,
    }

    _async_register_devices(hass, entry, config_data)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Data is pushed via SSE; _async_update_data just returns the current data.
    await coordinator.async_config_entry_first_refresh()

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry, stopping the SSE listener.

    Without this, removing/reloading the integration left the SSE background
    task running forever and re-adding the entry spawned a second listener.
    """
    _LOGGER.info("[innotemp] Unloading entry %s", entry.entry_id)
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        data = hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
        if data:
            api_client: InnotempApiClient = data["api"]
            coordinator: InnotempDataUpdateCoordinator = data["coordinator"]

            await api_client.async_sse_disconnect()

            sse_wrapper = getattr(coordinator, "sse_task", None)
            if sse_wrapper and not sse_wrapper.done():
                sse_wrapper.cancel()

            await coordinator.async_shutdown()
            # The aiohttp session was created during entry setup, so HA
            # detaches it automatically once the entry is unloaded.
        _LOGGER.info("[innotemp] Entry %s unloaded", entry.entry_id)

    return unload_ok
