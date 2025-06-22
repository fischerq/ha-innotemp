import aiohttp
import asyncio
import json
import logging
from typing import Callable, Awaitable, Dict, Any, Optional

_LOGGER = logging.getLogger(__name__)


class InnotempApiClient:
    """API client for the Innotemp Heating Controller."""

    def __init__(
        self, session: aiohttp.ClientSession, host: str, username: str, password: str
    ):
        """Initialize the API client."""
        self._session = session
        self._host = host
        self._username = username
        self._password = password
        self._base_url = f"http://{host}/inc"
        self._sse_task: Optional[asyncio.Task] = None
        self._config: Optional[Dict[str, Any]] = None
        self._signal_names: Optional[list[str]] = None

    async def async_api_request(
        self,
        method: str,
        endpoint: str,
        data: Optional[Dict[str, Any]] = None,
        attempt: int = 1,
    ) -> Optional[Dict[str, Any]]:
        """Wrap API requests to handle session timeouts."""
        url = f"{self._base_url}/{endpoint}"
        try:
            _LOGGER.debug(f"InnotempApiClient.async_api_request: Type of self._session: {type(self._session)}")
            async with self._session.request(method, url, data=data) as response:
                response.raise_for_status()
                if "application/json" in response.headers.get("Content-Type", ""):
                    return await response.json()
                else:
                    # Handle cases where the response is not strictly JSON but indicates success
                    # This might need adjustment based on actual API responses
                    _LOGGER.debug(
                        f"Non-JSON response from {endpoint}: {await response.text()}"
                    )
                    return {}
        except aiohttp.ClientResponseError as e:
            _LOGGER.warning(f"API request to {endpoint} failed: {e}")
            # Check if it's a potential session timeout error
            # The API analysis didn't specify the exact error, assuming a 401 or similar
            if e.status in [401, 403] and attempt == 1:
                _LOGGER.info(
                    "Session likely timed out, attempting to re-login and retry."
                )
                if await self.async_login():
                    return await self.async_api_request(
                        method, endpoint, data=data, attempt=2
                    )
                else:
                    _LOGGER.error("Failed to re-login after session timeout.")
                    return None
            else:
                _LOGGER.error(f"API request to {endpoint} failed after retry: {e}")
                return None
        except aiohttp.ClientConnectorError as e:
            _LOGGER.error(f"Connection error during API request to {endpoint}: {e}")
            return None
        except Exception as e:
            _LOGGER.error(
                f"An unexpected error occurred during API request to {endpoint}: {e}"
            )
            return None

    async def async_login(self) -> bool:
        """Log in to the controller and establish a session."""
        login_data = {"un": self._username, "pw": self._password}
        # Use a raw request here as session management happens at this level
        url = f"{self._base_url}/groups.read.php"
        try:
            _LOGGER.debug(f"InnotempApiClient.async_login: Type of self._session: {type(self._session)}")
            async with self._session.post(url, data=login_data) as response:
                response.raise_for_status()
                # Session cookie should be handled automatically by aiohttp.ClientSession
                _LOGGER.info("Successfully logged in to Innotemp controller.")
                return True
        except aiohttp.ClientResponseError as e:
            _LOGGER.error(f"Login failed: {e}")
            return False
        except aiohttp.ClientConnectorError as e:
            _LOGGER.error(f"Connection error during login: {e}")
            return False
        except Exception as e:
            _LOGGER.error(f"An unexpected error occurred during login: {e}")
            return False

    async def async_get_config(self) -> Optional[Dict[str, Any]]:
        """Fetch the full configuration data."""
        if self._config is None:
            config_data = {
                "un": self._username,
                "pw": self._password,
                "date_string": "0",
            }
            self._config = await self.async_api_request(
                "POST", "roomconf.read.php", data=config_data
            )
            if self._config:
                _LOGGER.debug("Successfully fetched configuration.")
            else:
                _LOGGER.error("Failed to fetch configuration.")
        return self._config

    async def async_send_command(
        self, room_id: int, param: str, val_new: Any, val_prev: Any
    ) -> bool:
        """Send a command to change a parameter value."""
        command_data = {
            "room_id": room_id,
            "param": param,
            "val_new": val_new,
            "val_prev": val_prev,
        }
        result = await self.async_api_request(
            "POST", "value.save.php", data=command_data
        )
        if result is not None:
            _LOGGER.debug(f"Command sent successfully: {command_data}")
            return True
        _LOGGER.error(f"Failed to send command: {command_data}")
        return False

    async def _get_signal_names(self) -> Optional[list[str]]:
        """Fetch the list of signal names for the SSE stream."""
        if self._signal_names is None:
            init_data = {"init": "1"}
            response = await self.async_api_request(
                "POST", "live_signal.read.php", data=init_data
            )
            if response and isinstance(response, list):
                self._signal_names = response
                _LOGGER.debug(f"Fetched {len(self._signal_names)} signal names.")
            else:
                _LOGGER.error("Failed to fetch signal names or response is not a list.")
        return self._signal_names

    async def async_sse_connect(
        self, callback: Callable[[Dict[str, Any]], Awaitable[None]]
    ):
        """Connect to the Server-Sent Events stream and process data."""
        if self._sse_task and not self._sse_task.done():
            _LOGGER.warning("SSE task is already running.")
            return

        async def sse_listener():
            """Internal task to listen for SSE messages."""
            while True:
                signal_names = await self._get_signal_names()
                if not signal_names:
                    _LOGGER.error(
                        "Could not get signal names for SSE stream. Retrying in 60 seconds."
                    )
                    await asyncio.sleep(60)
                    continue

                sse_url = f"{self._base_url}/live_signal.read.SSE.php"
                _LOGGER.info(f"Connecting to SSE stream at {sse_url}")
                try:
                    async with self._session.get(sse_url) as response:
                        response.raise_for_status()
                        async for line in response.content:
                            line = line.strip()
                            if line.startswith(b"data:"):
                                try:
                                    data_str = line[5:].strip().decode("utf-8")
                                    data_list = json.loads(data_str)
                                    if isinstance(data_list, list):
                                        processed_data = {}
                                        for i, value in enumerate(data_list):
                                            if i < len(signal_names):
                                                processed_data[signal_names[i]] = value
                                            else:
                                                _LOGGER.warning(
                                                    f"Received data for index {i} but only have {len(signal_names)} signal names. Data: {data_list}"
                                                )
                                                break  # Stop processing this message if indices don't match

                                        _LOGGER.debug(
                                            f"SSE received data: {processed_data}"
                                        )
                                        await callback(processed_data)
                                except json.JSONDecodeError as e:
                                    _LOGGER.error(
                                        f"Failed to decode JSON from SSE data: {e} - Data: {line.decode('utf-8')}"
                                    )
                                except Exception as e:
                                    _LOGGER.error(
                                        f"Error processing SSE data: {e} - Data: {line.decode('utf-8')}"
                                    )

                            elif line.startswith(b"event:"):
                                _LOGGER.debug(
                                    f"SSE event received: {line[6:].strip().decode('utf-8')}"
                                )
                            elif line == b"":
                                pass  # Keep-alive or empty line
                            else:
                                _LOGGER.debug(
                                    f"Unknown SSE line: {line.decode('utf-8')}"
                                )

                except aiohttp.ClientConnectorError as e:
                    _LOGGER.error(
                        f"SSE connection error: {e}. Reconnecting in 30 seconds."
                    )
                    await asyncio.sleep(30)
                except aiohttp.ClientResponseError as e:
                    _LOGGER.error(
                        f"SSE response error: {e}. Attempting re-login and reconnect in 30 seconds."
                    )
                    await self.async_login()  # Attempt re-login
                    await asyncio.sleep(30)
                except Exception as e:
                    _LOGGER.error(
                        f"Unexpected error in SSE listener: {e}. Reconnecting in 30 seconds."
                    )
                    await asyncio.sleep(30)

        self._sse_task = asyncio.create_task(sse_listener())
        _LOGGER.info("SSE listener task started.")

    async def async_sse_disconnect(self):
        """Disconnect the Server-Sent Events stream."""
        if self._sse_task:
            _LOGGER.info("Stopping SSE listener task.")
            self._sse_task.cancel()
            try:
                await self._sse_task
            except asyncio.CancelledError:
                _LOGGER.info("SSE listener task cancelled.")
            except Exception as e:
                _LOGGER.error(f"Error while stopping SSE task: {e}")
            self._sse_task = None

    async def async_close(self):
        """Close the client session and SSE task."""
        await self.async_sse_disconnect()
        # self._session will be closed by Home Assistant when the integration is unloaded
