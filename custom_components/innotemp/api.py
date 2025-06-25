import aiohttp
import asyncio
import json
import logging
from typing import Callable, Awaitable, Dict, Any, Optional

# Standard logger for Home Assistant components
_LOGGER = logging.getLogger(__name__)
_LOGGER.setLevel(logging.WARNING)  # Changed logger level to debug


# Custom exceptions for better error handling
class InnotempApiError(Exception):
    """Generic Innotemp API error."""

    pass


class InnotempAuthError(InnotempApiError):
    """Innotemp API authentication error."""

    pass


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
        self._is_logged_in = False

    async def _api_request(
        self,
        method: str,
        endpoint: str,
        data: Optional[Dict[str, Any]] = None,
        attempt: int = 1,
    ) -> Dict[str, Any]:
        """Wrap API requests to handle session timeouts and errors."""
        url = f"{self._base_url}/{endpoint}"
        try:
            # Use allow_redirects=False to prevent POST data from being lost on redirects
            _LOGGER.debug("Sending %s request to %s with data: %s", method, url, data)
            async with self._session.request(
                method, url, data=data, allow_redirects=False
            ) as response:
                _LOGGER.debug(
                    "Received response from %s: Status %s, Headers: %s",
                    url,
                    response.status,
                    response.headers,
                )

                # Check for redirects which may indicate a session timeout
                if response.status in [301, 302]:
                    raise InnotempAuthError(
                        "Request to %s was redirected, session likely expired.", url
                    )

                response.raise_for_status()

                # The server sometimes sends JSON with a text/html content type, so we try to parse it regardless
                response_text = await response.text()
                if not response_text:
                    return {}  # Return empty dict for empty responses
                _LOGGER.debug("Response body from %s: %s", url, response_text)

                try:
                    return json.loads(response_text)
                except json.JSONDecodeError:
                    _LOGGER.warning(
                        "Response from %s was not valid JSON: %s",
                        endpoint,
                        response_text,
                    )
                    # For some endpoints, a non-JSON response might still be a success indicator
                    return {"info": "success_non_json"}

        except aiohttp.ClientResponseError as e:
            # Re-raise as our custom auth error for the retry logic
            if e.status in [401, 403]:
                raise InnotempAuthError(
                    f"Authorization error for {endpoint}: {e}"
                ) from e
            _LOGGER.error("API request to %s failed with status %s", endpoint, e.status)
            raise InnotempApiError(f"Request to {endpoint} failed: {e}") from e

        except aiohttp.ClientError as e:
            _LOGGER.error("Connection error during API request to %s: %s", endpoint, e)
            raise InnotempApiError(f"Connection error for {endpoint}: {e}") from e

    async def _execute_with_retry(
        self, method: str, endpoint: str, data: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Execute an API request with session refresh logic."""
        try:
            if not self._is_logged_in:
                await self.async_login()
            return await self._api_request(method, endpoint, data)
        except InnotempAuthError:
            _LOGGER.info("Session likely timed out, attempting to re-login and retry.")
            await self.async_login()  # This will raise on failure
            # Retry the request once after successful re-login
            return await self._api_request(method, endpoint, data)

    async def async_login(self) -> None:
        """Log in to the controller and establish a session."""
        self._is_logged_in = False
        login_data = {"un": self._username, "pw": self._password}

        # We don't use the wrapper here as this is the base authentication call
        _LOGGER.debug("Attempting login to %s", self._base_url)
        url = f"{self._base_url}/groups.read.php"
        try:
            async with self._session.post(url, data=login_data) as response:
                response.raise_for_status()
                json_response = await response.json()

                # *** FIX: Explicitly check for the "success" message in the response body ***
                if json_response.get("info") == "success":
                    _LOGGER.info("Successfully logged in to Innotemp controller.")
                    self._is_logged_in = True
                    _LOGGER.debug(
                        "Login successful. Received response: %s", json_response
                    )
                    return

                else:
                    raise InnotempAuthError(
                        f"Login failed: Server responded with info: {json_response.get('info')}"
                    )

        except (aiohttp.ClientError, json.JSONDecodeError) as e:
            _LOGGER.error("Login request failed: %s", e)
            raise InnotempAuthError(
                "Login request failed, could not connect or parse response."
            ) from e

    async def async_get_config(self) -> Optional[Dict[str, Any]]:
        """Fetch the full configuration data."""
        config_data = {
            "un": self._username,
            "pw": self._password,
            "date_string": "0",
        }
        config = await self._execute_with_retry(
            "POST", "roomconf.read.php", data=config_data
        )
        if config:
            _LOGGER.debug("Successfully fetched configuration: %s", config)
            return config
        _LOGGER.error("Failed to fetch configuration. Received response: %s", config)
        return None

    async def async_send_command(
        self, room_id: int, param: str, val_new: Any, val_prev: Any
    ) -> bool:
        """Send a command to change a parameter value."""
        command_data = {
            "un": self._username,  # Add credentials for robustness
            "pw": self._password,
            "room_id": str(room_id),
            "param": param,
            "val_new": str(val_new),
            "val_prev": str(val_prev),
        }
        result = await self._execute_with_retry(
            "POST", "value.save.php", data=command_data
        )
        if result and result.get("info", "").startswith("success"):
            _LOGGER.debug(
                f"Command sent successfully for room {room_id}: {param} -> {val_new}. Response: {result}"
            )
            return True
        _LOGGER.error(
            f"Failed to send command for room {room_id}: {param} -> {val_new}. Response: {result}"
        )
        return False

    async def _get_signal_names(self) -> list[str]:
        """Fetch the list of signal names for the SSE stream."""
        # *** FIX: Add credentials to this call ***
        init_data = {"init": "1", "un": self._username, "pw": self._password}
        response = await self._execute_with_retry(
            "POST", "live_signal.read.php", data=init_data
        )
        if response and isinstance(response, list):
            _LOGGER.debug(f"Fetched {len(response)} signal names for SSE: %s", response)
            return response
        _LOGGER.error("Failed to fetch signal names or response is not a list.")
        raise InnotempApiError("Could not fetch signal names.")

    async def async_sse_connect(
        self, callback: Callable[[Dict[str, Any]], Awaitable[None]]
    ) -> None:
        """Connect to the Server-Sent Events stream and process data."""
        if self._sse_task and not self._sse_task.done():
            _LOGGER.warning("SSE task is already running.")
            return

        async def sse_listener():
            """Internal task to listen for SSE messages."""
            while True:
                try:
                    # Always ensure we are logged in before starting/restarting the loop
                    if not self._is_logged_in:
                        await self.async_login()

                    signal_names = await self._get_signal_names()
                    if not signal_names:
                        _LOGGER.warning(
                            "No signal names fetched, cannot connect to SSE."
                        )
                        await asyncio.sleep(30)  # Wait before retrying
                        continue
                    sse_url = f"{self._base_url}/live_signal.read.SSE.php"

                    async with self._session.get(sse_url) as response:
                        response.raise_for_status()
                        async for line in response.content:
                            if not line.startswith(b"data:"):
                                continue
                            try:
                                data_str = line.strip()[5:].decode("utf-8")
                                data_list = json.loads(data_str)
                                if isinstance(data_list, list):
                                    _LOGGER.debug("Received SSE data: %s", data_list)
                                    processed_data = dict(zip(signal_names, data_list))

                                    if callback is None:
                                        _LOGGER.error(
                                            "SSE callback is None. Cannot process data."
                                        )
                                    elif not callable(callback):
                                        _LOGGER.error(
                                            "SSE callback is not callable. Type: %s. Cannot process data.",
                                            type(callback),
                                        )
                                    else:
                                        # Corrected: async_set_updated_data is synchronous
                                        callback(processed_data)
                            except (json.JSONDecodeError, IndexError) as e:
                                _LOGGER.warning("Error processing SSE data line: %s", e)

                except InnotempApiError as e:
                    _LOGGER.error("API error in SSE listener, will retry: %s", e)
                except aiohttp.ClientError as e:
                    _LOGGER.error("Connection error in SSE listener, will retry: %s", e)
                    self._is_logged_in = False  # Force re-login on next loop
                except Exception as e:
                    _LOGGER.exception(
                        "Unexpected error in SSE listener, will retry: %s", e
                    )
                    self._is_logged_in = False

                _LOGGER.info("SSE connection lost. Reconnecting in 30 seconds.")
                await asyncio.sleep(30)

        self._sse_task = asyncio.create_task(sse_listener())
        _LOGGER.info("SSE listener task started.")

    async def async_sse_disconnect(self) -> None:
        """Disconnect the Server-Sent Events stream."""
        if self._sse_task:
            _LOGGER.info("Stopping SSE listener task.")
            self._sse_task.cancel()
            try:
                await self._sse_task
            except asyncio.CancelledError:
                pass  # Expected
            finally:
                self._sse_task = None
                _LOGGER.info("SSE listener task stopped.")
