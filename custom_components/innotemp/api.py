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
        self._signal_names_cache: Optional[list[str]] = None

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
                    json_response = json.loads(response_text)
                    # Check for application-level auth errors even with HTTP 200 OK
                    if (
                        isinstance(json_response, dict)
                        and json_response.get("info") == "error"
                        and json_response.get("error") == "Access denied."
                    ):
                        _LOGGER.warning(
                            "Access denied by API for %s (payload error), attempting re-login.",
                            endpoint,
                        )
                        raise InnotempAuthError(
                            f"Access denied by API payload for {endpoint}: {json_response}"
                        )
                    return json_response
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
            # "val_prev": str(val_prev), # Will be added conditionally
        }
        if val_prev is not None:
            command_data["val_prev"] = str(val_prev)
        else:
            command_data["val_prev"] = ""  # Send empty string if None
            _LOGGER.debug(
                f"val_prev was None for param {param}, sending empty string for val_prev."
            )

        _LOGGER.debug(f"Sending command to value.save.php with payload: {command_data}")

        try:
            result = await self._execute_with_retry(
                "POST", "value.save.php", data=command_data
            )
            if result and result.get("info", "").startswith("success"):
                _LOGGER.debug(
                    f"Command sent successfully for room {room_id}: {param} -> {val_new}. Response: {result}"
                )
                return True

            # This error log is for non-exception failures (e.g. API returns success=false)
            _LOGGER.error(
                f"API indicated failure for command room {room_id}: {param} -> {val_new}. Payload: {command_data}. Response: {result}"
            )
            return False
        except InnotempAuthError as e:
            _LOGGER.error(
                f"Authentication error sending command for room {room_id}: {param} -> {val_new}. Payload: {command_data}. Error: {e}"
            )
            raise  # Re-raise to allow select.py or other callers to handle
        except InnotempApiError as e:  # Catch other API errors from _execute_with_retry
            _LOGGER.error(
                f"API error sending command for room {room_id}: {param} -> {val_new}. Payload: {command_data}. Error: {e}"
            )
            raise  # Re-raise

    async def _get_signal_names(self) -> list[str]:
        """Fetch the list of signal names for the SSE stream. Uses a cache after the first successful fetch."""
        if self._signal_names_cache is not None:
            _LOGGER.debug("Returning cached signal names: %s", self._signal_names_cache)
            return self._signal_names_cache

        init_data = {"init": "1", "un": self._username, "pw": self._password}
        response = await self._execute_with_retry(
            "POST", "live_signal.read.php", data=init_data
        )
        if response and isinstance(response, list):
            if not response:  # Validation 1: Empty list check
                _LOGGER.error("Fetched signal names list is empty.")
                raise InnotempApiError("Failed to fetch signal names: list is empty.")

            _LOGGER.debug(f"Fetched {len(response)} signal names for SSE: %s", response)
            self._signal_names_cache = response  # Cache the fetched names
            return response

        _LOGGER.error("Failed to fetch signal names or response is not a list. Response: %s", response)
        # Clear cache on error to ensure retry on next attempt if applicable
        self._signal_names_cache = None
        raise InnotempApiError("Could not fetch signal names or response was not a list.")

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

                                    # Validation 2: Check for length mismatch
                                    if len(data_list) != len(signal_names):
                                        _LOGGER.error(
                                            "SSE data length mismatch: expected %s values for signals %s, but got %s values: %s",
                                            len(signal_names),
                                            signal_names,
                                            len(data_list),
                                            data_list,
                                        )
                                        # Clear signal names cache to force re-fetch on next connection attempt,
                                        # as the signal list might have changed.
                                        self._signal_names_cache = None
                                        _LOGGER.warning("Cleared signal names cache due to SSE data length mismatch. Will attempt to re-fetch on next cycle.")
                                        # Depending on strictness, we might raise an error or break the loop here.
                                        # For now, log, clear cache, and continue to next message or retry cycle.
                                        # If we `continue` here, it means we skip this specific data packet.
                                        # If the error is persistent, the listener will eventually retry the connection.
                                        # Re-raising an error here would make the sse_listener exit and retry sooner.
                                        # Let's make it try to re-fetch signals by breaking the inner loop and letting the outer loop restart.
                                        # This requires _get_signal_names to be called again.
                                        # However, the current structure calls _get_signal_names outside the 'async for line' loop.
                                        # So, to force a re-fetch of signal_names, we should probably reconnect.
                                        # The simplest for now is to log and skip this packet.
                                        # A more robust solution might involve a full reconnect if mismatches are persistent.
                                        # For now, we'll log the error and skip this packet by using `continue`.
                                        # To also force a re-fetch of signal names on the next main loop iteration of sse_listener:
                                        self._signal_names_cache = None # Ensure re-fetch
                                        # This will cause the next call to _get_signal_names to fetch fresh.
                                        # However, signal_names variable in current scope is stale.
                                        # The problem is that signal_names is fetched once per SSE connection attempt.
                                        # If it's wrong, the current connection is likely compromised.
                                        # Let's log, clear cache, and then break from this inner processing loop to force a full reconnect.
                                        # This seems safer than continuing to process with mismatched names.
                                        # The outer `while True` loop in `sse_listener` will handle the reconnect.
                                        # Update: The request was to log and continue.
                                        # If we continue, we are skipping this packet. The signal_names list remains the same for the current connection.
                                        # If the server starts sending different length data consistently, the cache won't help until a full reconnect.
                                        # Clearing the cache and continuing means the *next* time _get_signal_names is called (after a reconnect), it will fetch.
                                        # This seems like a reasonable compromise.
                                        self._signal_names_cache = None # Invalidate cache for next full connection attempt
                                        _LOGGER.warning("Signal names cache cleared. A full reconnect will be needed to refresh signal names if the issue persists.")
                                        continue # Skip this data packet

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
                                else:
                                    _LOGGER.warning(
                                        "Received non-list SSE data: %s", data_list
                                    )
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
