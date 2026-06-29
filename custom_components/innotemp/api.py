import aiohttp
import asyncio
import json
import logging
import time
from typing import Callable, Awaitable, Dict, Any, Optional

_LOGGER = logging.getLogger(__name__)
_LOGGER.setLevel(logging.DEBUG)


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
        _LOGGER.debug(
            "InnotempApiClient initialized: host=%s, base_url=%s, username=%s",
            host,
            self._base_url,
            username,
        )

    def _sanitize_data_for_log(
        self, data: Optional[Dict[str, Any]]
    ) -> Optional[Dict[str, Any]]:
        """Return a copy of data with password masked for logging."""
        if data is None:
            return None
        sanitized = dict(data)
        if "pw" in sanitized:
            sanitized["pw"] = "***"
        return sanitized

    async def _api_request(
        self,
        method: str,
        endpoint: str,
        data: Optional[Dict[str, Any]] = None,
        attempt: int = 1,
    ) -> Dict[str, Any]:
        """Wrap API requests to handle session timeouts and errors."""
        url = f"{self._base_url}/{endpoint}"
        t_start = time.monotonic()
        log_data = self._sanitize_data_for_log(data)
        _LOGGER.debug(
            "[innotemp] >>> %s %s | data=%s | attempt=%d | logged_in=%s",
            method,
            url,
            log_data,
            attempt,
            self._is_logged_in,
        )
        try:
            async with self._session.request(
                method, url, data=data, allow_redirects=False
            ) as response:
                elapsed = (time.monotonic() - t_start) * 1000
                response_text = await response.text()
                _LOGGER.debug(
                    "[innotemp] <<< %s %s | status=%s | content_type=%s | "
                    "elapsed=%.0fms | headers=%s",
                    method,
                    url,
                    response.status,
                    response.content_type,
                    elapsed,
                    dict(response.headers),
                )
                _LOGGER.debug(
                    "[innotemp] <<< %s %s | body=%s",
                    method,
                    url,
                    response_text,
                )

                if response.status in [301, 302]:
                    location = response.headers.get("Location", "unknown")
                    _LOGGER.warning(
                        "[innotemp] Redirect %s -> %s (session likely expired)",
                        url,
                        location,
                    )
                    raise InnotempAuthError(
                        f"Request to {url} was redirected to {location}, session likely expired."
                    )

                response.raise_for_status()

                if not response_text:
                    _LOGGER.debug("[innotemp] Empty response body from %s", url)
                    return {}

                try:
                    json_response = json.loads(response_text)
                    if (
                        isinstance(json_response, dict)
                        and json_response.get("info") == "error"
                        and json_response.get("error") == "Access denied."
                    ):
                        _LOGGER.warning(
                            "[innotemp] Access denied for %s: %s",
                            endpoint,
                            json_response,
                        )
                        raise InnotempAuthError(
                            f"Access denied by API payload for {endpoint}: {json_response}"
                        )
                    return json_response
                except json.JSONDecodeError:
                    _LOGGER.warning(
                        "[innotemp] Non-JSON response from %s: %s",
                        endpoint,
                        response_text[:500],
                    )
                    return {"info": "success_non_json"}

        except aiohttp.ClientResponseError as e:
            elapsed = (time.monotonic() - t_start) * 1000
            _LOGGER.error(
                "[innotemp] HTTP error for %s %s: status=%s, message=%s, elapsed=%.0fms",
                method,
                url,
                e.status,
                e.message,
                elapsed,
            )
            if e.status in [401, 403]:
                raise InnotempAuthError(
                    f"Authorization error for {endpoint}: {e}"
                ) from e
            raise InnotempApiError(f"Request to {endpoint} failed: {e}") from e

        except aiohttp.ClientError as e:
            elapsed = (time.monotonic() - t_start) * 1000
            _LOGGER.error(
                "[innotemp] Connection error for %s %s: %s (type=%s, elapsed=%.0fms)",
                method,
                url,
                e,
                type(e).__name__,
                elapsed,
            )
            raise InnotempApiError(f"Connection error for {endpoint}: {e}") from e

    async def _execute_with_retry(
        self, method: str, endpoint: str, data: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Execute an API request with session refresh logic."""
        try:
            if not self._is_logged_in:
                _LOGGER.debug(
                    "[innotemp] Not logged in before %s %s, logging in first",
                    method,
                    endpoint,
                )
                await self.async_login()
            return await self._api_request(method, endpoint, data)
        except InnotempAuthError as e:
            _LOGGER.info(
                "[innotemp] Auth error for %s %s (%s), re-login and retry",
                method,
                endpoint,
                e,
            )
            await self.async_login()
            return await self._api_request(method, endpoint, data)

    async def async_login(self) -> None:
        """Log in to the controller and establish a session."""
        self._is_logged_in = False
        login_data = {"un": self._username, "pw": self._password}
        url = f"{self._base_url}/groups.read.php"

        _LOGGER.info(
            "[innotemp] Login attempt: url=%s, username=%s",
            url,
            self._username,
        )
        t_start = time.monotonic()

        try:
            async with self._session.post(
                url,
                data=login_data,
                allow_redirects=False,
            ) as response:
                elapsed = (time.monotonic() - t_start) * 1000
                response_text = await response.text()

                _LOGGER.debug(
                    "[innotemp] Login response: status=%s, content_type=%s, "
                    "elapsed=%.0fms, headers=%s",
                    response.status,
                    response.content_type,
                    elapsed,
                    dict(response.headers),
                )
                _LOGGER.debug(
                    "[innotemp] Login response body: %s",
                    response_text[:2000],
                )

                if response.status in [301, 302]:
                    location = response.headers.get("Location", "unknown")
                    _LOGGER.error(
                        "[innotemp] Login redirected: %s -> %s "
                        "(device may require HTTPS or different path)",
                        url,
                        location,
                    )
                    raise InnotempAuthError(
                        f"Login redirected to {location} — check host address"
                    )

                if response.status != 200:
                    _LOGGER.error(
                        "[innotemp] Login HTTP error: status=%s, body=%s",
                        response.status,
                        response_text[:500],
                    )
                    response.raise_for_status()

                if not response_text.strip():
                    _LOGGER.error("[innotemp] Login returned empty response body")
                    raise InnotempAuthError(
                        "Login failed: server returned empty response"
                    )

                try:
                    json_response = json.loads(response_text)
                except json.JSONDecodeError as je:
                    _LOGGER.error(
                        "[innotemp] Login response is not valid JSON: "
                        "error=%s, body=%s",
                        je,
                        response_text[:500],
                    )
                    raise InnotempAuthError(
                        f"Login failed: response is not JSON: {response_text[:200]}"
                    ) from je

                _LOGGER.debug(
                    "[innotemp] Login parsed response: %s",
                    json_response,
                )

                info = (
                    json_response.get("info")
                    if isinstance(json_response, dict)
                    else None
                )

                if info == "success":
                    _LOGGER.info(
                        "[innotemp] Login successful (%.0fms)",
                        elapsed,
                    )
                    self._is_logged_in = True
                    return

                _LOGGER.error(
                    "[innotemp] Login rejected by server: full_response=%s",
                    json_response,
                )
                raise InnotempAuthError(
                    f"Login failed: server responded with {json_response}"
                )

        except InnotempAuthError:
            raise
        except aiohttp.ClientError as e:
            elapsed = (time.monotonic() - t_start) * 1000
            _LOGGER.error(
                "[innotemp] Login connection error: %s (type=%s, elapsed=%.0fms)",
                e,
                type(e).__name__,
                elapsed,
            )
            raise InnotempAuthError(
                f"Login connection failed: {type(e).__name__}: {e}"
            ) from e
        except Exception as e:
            elapsed = (time.monotonic() - t_start) * 1000
            _LOGGER.error(
                "[innotemp] Login unexpected error: %s (type=%s, elapsed=%.0fms)",
                e,
                type(e).__name__,
                elapsed,
            )
            raise InnotempAuthError(
                f"Login failed unexpectedly: {type(e).__name__}: {e}"
            ) from e

    async def async_get_config(self) -> Optional[Dict[str, Any]]:
        """Fetch the full configuration data."""
        _LOGGER.info("[innotemp] Fetching configuration from roomconf.read.php")
        config_data = {
            "un": self._username,
            "pw": self._password,
            "date_string": "0",
        }
        config = await self._execute_with_retry(
            "POST", "roomconf.read.php", data=config_data
        )
        if config:
            _LOGGER.debug("[innotemp] Configuration fetched: %s", config)
            return config
        _LOGGER.error("[innotemp] Configuration fetch returned falsy: %s", config)
        return None

    async def async_send_command(
        self, room_id: int, param: str, val_new: Any, val_prev_options: list[Any]
    ) -> bool:
        """Send a command to change a parameter value, trying multiple previous values if needed."""
        _LOGGER.info(
            "[innotemp] Sending command: room_id=%s, param=%s, val_new=%s, "
            "val_prev_options=%s",
            room_id,
            param,
            val_new,
            val_prev_options,
        )
        for i, val_prev in enumerate(val_prev_options):
            command_data = {
                "un": self._username,
                "pw": self._password,
                "room_id": str(room_id),
                "param": param,
                "val_new": str(val_new),
            }
            if val_prev is not None:
                command_data["val_prev"] = str(val_prev)
            else:
                command_data["val_prev"] = ""

            _LOGGER.debug(
                "[innotemp] Command attempt %d/%d: room=%s, param=%s, "
                "new=%s, prev=%s",
                i + 1,
                len(val_prev_options),
                room_id,
                param,
                val_new,
                val_prev,
            )

            try:
                result = await self._execute_with_retry(
                    "POST", "value.save.php", data=command_data
                )
                if result and result.get("info", "").startswith("success"):
                    _LOGGER.info(
                        "[innotemp] Command success: room=%s, param=%s, "
                        "new=%s, prev=%s, response=%s",
                        room_id,
                        param,
                        val_new,
                        val_prev,
                        result,
                    )
                    return True
                else:
                    _LOGGER.warning(
                        "[innotemp] Command rejected: room=%s, param=%s, "
                        "prev=%s, response=%s",
                        room_id,
                        param,
                        val_prev,
                        result,
                    )
            except InnotempAuthError as e:
                _LOGGER.error(
                    "[innotemp] Command auth error (aborting): %s",
                    e,
                )
                raise
            except InnotempApiError as e:
                _LOGGER.error(
                    "[innotemp] Command API error (trying next prev): %s",
                    e,
                )

        _LOGGER.error(
            "[innotemp] Command failed all attempts: room=%s, param=%s, "
            "val_new=%s, tried prev_values=%s",
            room_id,
            param,
            val_new,
            val_prev_options,
        )
        return False

    async def _get_signal_names(self) -> list[str]:
        """Fetch the list of signal names for the SSE stream."""
        if self._signal_names_cache is not None:
            _LOGGER.debug(
                "[innotemp] Using cached signal names (%d entries)",
                len(self._signal_names_cache),
            )
            return self._signal_names_cache

        _LOGGER.debug("[innotemp] Fetching signal names from live_signal.read.php")
        init_data = {"init": "1", "un": self._username, "pw": self._password}
        response = await self._execute_with_retry(
            "POST", "live_signal.read.php", data=init_data
        )
        if response and isinstance(response, list):
            if not response:
                _LOGGER.error("[innotemp] Signal names list is empty")
                raise InnotempApiError("Failed to fetch signal names: list is empty.")

            _LOGGER.info(
                "[innotemp] Fetched %d signal names: %s",
                len(response),
                response,
            )
            self._signal_names_cache = response
            return response

        _LOGGER.error(
            "[innotemp] Signal names response not a list: type=%s, value=%s",
            type(response).__name__,
            response,
        )
        self._signal_names_cache = None
        raise InnotempApiError(
            f"Signal names response was {type(response).__name__}, not list: {response}"
        )

    async def async_sse_connect(
        self, callback: Callable[[Dict[str, Any]], Awaitable[None]]
    ) -> None:
        """Connect to the Server-Sent Events stream and process data."""
        if self._sse_task and not self._sse_task.done():
            _LOGGER.warning("[innotemp] SSE task is already running, skipping")
            return

        async def sse_listener():
            """Internal task to listen for SSE messages."""
            reconnect_count = 0
            while True:
                reconnect_count += 1
                msg_count = 0
                _LOGGER.info(
                    "[innotemp] SSE listener starting (attempt #%d)",
                    reconnect_count,
                )
                try:
                    if not self._is_logged_in:
                        _LOGGER.debug("[innotemp] SSE: not logged in, logging in first")
                        await self.async_login()

                    signal_names = await self._get_signal_names()
                    if not signal_names:
                        _LOGGER.warning("[innotemp] SSE: no signal names, retry in 30s")
                        await asyncio.sleep(30)
                        continue

                    sse_url = f"{self._base_url}/live_signal.read.SSE.php"
                    _LOGGER.info(
                        "[innotemp] SSE connecting: url=%s, signals=%d",
                        sse_url,
                        len(signal_names),
                    )

                    async with self._session.get(sse_url) as response:
                        _LOGGER.debug(
                            "[innotemp] SSE connected: status=%s, headers=%s",
                            response.status,
                            dict(response.headers),
                        )
                        response.raise_for_status()
                        async for line in response.content:
                            if not line.startswith(b"data:"):
                                continue
                            msg_count += 1
                            try:
                                data_str = line.strip()[5:].decode("utf-8")
                                data_list = json.loads(data_str)
                                if isinstance(data_list, list):
                                    _LOGGER.debug(
                                        "[innotemp] SSE msg #%d: %s",
                                        msg_count,
                                        data_list,
                                    )

                                    if len(data_list) != len(signal_names):
                                        _LOGGER.error(
                                            "[innotemp] SSE length mismatch: "
                                            "expected %d signals, got %d values",
                                            len(signal_names),
                                            len(data_list),
                                        )
                                        self._signal_names_cache = None
                                        continue

                                    processed_data = dict(zip(signal_names, data_list))
                                    _LOGGER.debug(
                                        "[innotemp] SSE processed: %s",
                                        processed_data,
                                    )

                                    if callback is None:
                                        _LOGGER.error("[innotemp] SSE callback is None")
                                    elif not callable(callback):
                                        _LOGGER.error(
                                            "[innotemp] SSE callback not callable: %s",
                                            type(callback),
                                        )
                                    else:
                                        callback(processed_data)
                                else:
                                    _LOGGER.warning(
                                        "[innotemp] SSE non-list data: %s",
                                        data_list,
                                    )
                            except (json.JSONDecodeError, IndexError) as e:
                                _LOGGER.warning(
                                    "[innotemp] SSE parse error: %s, line=%s",
                                    e,
                                    line[:200],
                                )

                except InnotempApiError as e:
                    _LOGGER.error("[innotemp] SSE API error, retry in 30s: %s", e)
                except aiohttp.ClientError as e:
                    _LOGGER.error(
                        "[innotemp] SSE connection error (type=%s), retry in 30s: %s",
                        type(e).__name__,
                        e,
                    )
                    self._is_logged_in = False
                except Exception as e:
                    _LOGGER.exception(
                        "[innotemp] SSE unexpected error, retry in 30s: %s",
                        e,
                    )
                    self._is_logged_in = False

                _LOGGER.info(
                    "[innotemp] SSE disconnected after %d messages, reconnecting in 30s",
                    msg_count,
                )
                await asyncio.sleep(30)

        self._sse_task = asyncio.create_task(sse_listener())
        _LOGGER.info("[innotemp] SSE listener task created")

    async def async_sse_disconnect(self) -> None:
        """Disconnect the Server-Sent Events stream."""
        if self._sse_task:
            _LOGGER.info("[innotemp] Stopping SSE listener task")
            self._sse_task.cancel()
            try:
                await self._sse_task
            except asyncio.CancelledError:
                pass
            finally:
                self._sse_task = None
                _LOGGER.info("[innotemp] SSE listener task stopped")
