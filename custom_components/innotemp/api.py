"""HTTP/SSE client for the Innotemp heating controller's PHP web API.

This module deliberately depends only on aiohttp and the standard library so
that it can be used outside Home Assistant (see ``scripts/smoke_test.py``).
"""

import asyncio
import json
import logging
import re
import time
from typing import Any, Callable, Dict, Optional

import aiohttp

_LOGGER = logging.getLogger(__name__)

# Regular requests should never hang for minutes when the controller is
# offline; the SSE stream is long-lived, so only its connect phase is bounded.
REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=30)
SSE_TIMEOUT = aiohttp.ClientTimeout(total=None, sock_connect=30)
SSE_RECONNECT_DELAY = 30

_HOST_RE = re.compile(
    r"^(?:\[[0-9a-f:.]+\]|[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)*)"
    r"(?::(\d{1,5}))?$"
)


class InnotempApiError(Exception):
    """Generic Innotemp API error."""


class InnotempAuthError(InnotempApiError):
    """The controller rejected the credentials or the session expired."""


class InnotempConnectionError(InnotempApiError):
    """The controller could not be reached (offline, DNS, timeout, ...)."""


class InnotempInvalidResponseError(InnotempApiError):
    """The controller answered with something that is not the expected JSON."""


class InnotempCommandError(InnotempApiError):
    """The controller did not confirm a value.save.php write."""


def normalize_host(host: Any) -> str:
    """Return the canonical form of a user-entered host, or raise ValueError.

    Accepts an IPv4 address, a hostname or a bracketed IPv6 address, each with
    an optional ``:port``. Surrounding whitespace and a trailing slash are
    stripped and the result is lower-cased, so ``" 192.168.1.5/ "`` and
    ``"192.168.1.5"`` map to the same config entry unique id.
    """
    if not isinstance(host, str):
        raise ValueError("Host must be a string.")
    value = host.strip().rstrip("/").lower()
    if not value:
        raise ValueError("Host cannot be empty.")
    if "://" in value:
        raise ValueError("Enter only the IP address or hostname, without 'http://'.")
    if value in ("http", "https"):
        raise ValueError("Host cannot be 'http' or 'https'.")
    match = _HOST_RE.match(value)
    if not match:
        raise ValueError(f"'{host}' is not a valid IP address or hostname.")
    port = match.group(1)
    if port is not None and not 0 < int(port) < 65536:
        raise ValueError(f"Port {port} is out of range.")
    return value


def parse_sse_data(payload: Any) -> Optional[list]:
    """Parse the ``data:`` payload of one SSE message into a list of values.

    docs/API_SPEC.md describes the payload as a comma-separated string
    (``21.5,88.0,1``); earlier code and tests assumed a JSON array
    (``[21.5, "88.0", 1]``). Both are accepted. CSV values are returned as
    stripped strings (surrounding quotes removed), which is the same form the
    entities already handle for values coming from roomconf. Returns ``None``
    for an empty or unparseable payload.
    """
    if isinstance(payload, (bytes, bytearray)):
        payload = payload.decode("utf-8", errors="replace")
    if not isinstance(payload, str):
        return None
    text = payload.strip()
    if text.startswith("data:"):
        text = text[5:].strip()
    if not text:
        return None
    if text.startswith("["):
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            # e.g. "[1,2" (truncated) -- fall back to CSV on the inner part
            text = text.strip("[]")
        else:
            return parsed if isinstance(parsed, list) else None
    if text.startswith("{"):
        return None
    values = []
    for item in text.split(","):
        item = item.strip()
        if len(item) >= 2 and item[0] == item[-1] and item[0] in "\"'":
            item = item[1:-1]
        values.append(item)
    return values


def format_api_value(value: Any) -> str:
    """Format a value the way the controller's web UI sends it.

    Integral numbers are sent without a decimal point (``21``, not ``21.0``),
    ``None`` becomes the empty string, everything else is passed through
    ``str``. Used for both ``val_new`` and ``val_prev``.
    """
    if value is None:
        return ""
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        number = float(value)
        return str(int(number)) if number.is_integer() else str(value)
    text = str(value).strip()
    try:
        number = float(text)
    except ValueError:
        return text
    if number.is_integer() and "e" not in text.lower():
        return str(int(number))
    return text


def _looks_like_html(text: str) -> bool:
    head = text.lstrip()[:200].lower()
    return head.startswith("<!doctype html") or "<html" in head or "<form" in head


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
        self.sse_reconnect_delay: float = SSE_RECONNECT_DELAY
        _LOGGER.debug(
            "InnotempApiClient initialized: host=%s, base_url=%s, username=%s",
            host,
            self._base_url,
            username,
        )

    @property
    def base_url(self) -> str:
        """Return the base URL of the controller's ``/inc`` API directory."""
        return self._base_url

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

    def _log_session_cookie_state(self, response) -> None:
        """Log whether the login session cookie was actually stored.

        The controller authenticates follow-up requests (notably the SSE
        stream) via a PHPSESSID cookie. aiohttp's default CookieJar drops
        cookies set by bare-IP hosts, which makes login look successful while
        every later request is unauthenticated — log that condition loudly.
        """
        try:
            set_cookie = response.headers.getall("Set-Cookie", [])
        except AttributeError:  # plain dict (tests/mocks)
            value = response.headers.get("Set-Cookie")
            set_cookie = [value] if value else []

        jar = getattr(self._session, "cookie_jar", None)
        stored_names: list[str] = []
        if jar is not None:
            try:
                stored_names = [cookie.key for cookie in jar]
            except TypeError:
                stored_names = []

        _LOGGER.debug(
            "[innotemp] Login cookies: %d Set-Cookie header(s), jar now holds=%s",
            len(set_cookie),
            stored_names,
        )
        if set_cookie and not stored_names:
            _LOGGER.warning(
                "[innotemp] Server sent a session cookie on login but the "
                "cookie jar did not store it (host=%s). Requests after login "
                "will likely be rejected. This happens with aiohttp's default "
                "cookie jar and IP-address hosts; the integration must use a "
                "CookieJar(unsafe=True) session.",
                self._host,
            )

    async def _api_request(
        self,
        method: str,
        endpoint: str,
        data: Optional[Dict[str, Any]] = None,
        attempt: int = 1,
    ) -> Any:
        """Perform one request and decode its JSON body.

        Raises InnotempAuthError when the session is gone (redirect to the
        login page, 401/403, an HTML login page, or an 'Access denied.'
        payload), InnotempConnectionError when the controller is unreachable
        and InnotempInvalidResponseError for any other non-JSON body. A
        non-JSON 200 is *not* success: the spec documents JSON responses only,
        and treating arbitrary text as success hid failed writes.
        """
        url = f"{self._base_url}/{endpoint}"
        t_start = time.monotonic()
        _LOGGER.debug(
            "[innotemp] >>> %s %s | data=%s | attempt=%d | logged_in=%s",
            method,
            url,
            self._sanitize_data_for_log(data),
            attempt,
            self._is_logged_in,
        )
        try:
            async with self._session.request(
                method,
                url,
                data=data,
                allow_redirects=False,
                timeout=REQUEST_TIMEOUT,
            ) as response:
                response_text = await response.text()
                _LOGGER.debug(
                    "[innotemp] <<< %s %s | status=%s | elapsed=%.0fms | body=%s",
                    method,
                    url,
                    response.status,
                    (time.monotonic() - t_start) * 1000,
                    response_text[:2000],
                )

                if response.status in (301, 302, 303, 307, 308):
                    location = response.headers.get("Location", "unknown")
                    raise InnotempAuthError(
                        f"Request to {url} was redirected to {location}, "
                        "session likely expired."
                    )

                response.raise_for_status()

                if not response_text.strip():
                    _LOGGER.debug("[innotemp] Empty response body from %s", url)
                    return {}

                try:
                    json_response = json.loads(response_text)
                except json.JSONDecodeError as err:
                    if _looks_like_html(response_text):
                        raise InnotempAuthError(
                            f"{endpoint} returned an HTML page instead of JSON "
                            "(session likely expired)"
                        ) from err
                    raise InnotempInvalidResponseError(
                        f"{endpoint} returned a non-JSON response: "
                        f"{response_text[:200]!r}"
                    ) from err

                if (
                    isinstance(json_response, dict)
                    and json_response.get("info") == "error"
                    and json_response.get("error") == "Access denied."
                ):
                    raise InnotempAuthError(
                        f"Access denied by API payload for {endpoint}: {json_response}"
                    )
                return json_response

        except aiohttp.ClientResponseError as e:
            if e.status in (401, 403):
                raise InnotempAuthError(
                    f"Authorization error for {endpoint}: {e}"
                ) from e
            raise InnotempApiError(f"Request to {endpoint} failed: {e}") from e
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            raise InnotempConnectionError(
                f"Connection error for {endpoint}: {type(e).__name__}: {e}"
            ) from e

    async def _execute_with_retry(
        self, method: str, endpoint: str, data: Optional[Dict[str, Any]] = None
    ) -> Any:
        """Execute an API request, re-logging in once if the session expired."""
        if not self._is_logged_in:
            await self.async_login()
        try:
            return await self._api_request(method, endpoint, data)
        except InnotempAuthError as e:
            _LOGGER.info(
                "[innotemp] Auth error for %s %s (%s), re-login and retry",
                method,
                endpoint,
                e,
            )
            self._is_logged_in = False
            await self.async_login()
            return await self._api_request(method, endpoint, data, attempt=2)

    async def async_login(self) -> None:
        """Log in to the controller and establish a session.

        Raises InnotempAuthError when the controller rejects the login and
        InnotempConnectionError when it cannot be reached.
        """
        self._is_logged_in = False
        login_data = {"un": self._username, "pw": self._password}
        url = f"{self._base_url}/groups.read.php"

        _LOGGER.debug(
            "[innotemp] Login attempt: url=%s, username=%s", url, self._username
        )
        t_start = time.monotonic()

        try:
            async with self._session.post(
                url,
                data=login_data,
                allow_redirects=False,
                timeout=REQUEST_TIMEOUT,
            ) as response:
                response_text = await response.text()
                elapsed = (time.monotonic() - t_start) * 1000
                _LOGGER.debug(
                    "[innotemp] Login response: status=%s, elapsed=%.0fms, body=%s",
                    response.status,
                    elapsed,
                    response_text[:2000],
                )

                if response.status in (301, 302, 303, 307, 308):
                    location = response.headers.get("Location", "unknown")
                    raise InnotempAuthError(
                        f"Login redirected to {location} — check host address"
                    )

                if response.status != 200:
                    raise InnotempConnectionError(
                        f"Login failed with HTTP status {response.status}"
                    )

                if not response_text.strip():
                    raise InnotempAuthError(
                        "Login failed: server returned empty response"
                    )

                try:
                    json_response = json.loads(response_text)
                except json.JSONDecodeError as je:
                    raise InnotempAuthError(
                        f"Login failed: response is not JSON: {response_text[:200]}"
                    ) from je

                info = (
                    json_response.get("info")
                    if isinstance(json_response, dict)
                    else None
                )
                if info != "success":
                    raise InnotempAuthError(
                        f"Login failed: server responded with {json_response}"
                    )

                _LOGGER.debug("[innotemp] Login successful (%.0fms)", elapsed)
                self._log_session_cookie_state(response)
                self._is_logged_in = True

        except InnotempApiError as e:
            _LOGGER.debug("[innotemp] Login failed: %s", e)
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            raise InnotempConnectionError(
                f"Login connection failed: {type(e).__name__}: {e}"
            ) from e

    async def async_get_config(self) -> Optional[Dict[str, Any]]:
        """Fetch the full configuration data (roomconf.read.php)."""
        config_data = {
            "un": self._username,
            "pw": self._password,
            "date_string": "0",
        }
        config = await self._execute_with_retry(
            "POST", "roomconf.read.php", data=config_data
        )
        if config and isinstance(config, dict):
            return config
        _LOGGER.error("[innotemp] Configuration fetch returned no data: %r", config)
        return None

    async def async_send_command(
        self, room_id: int, param: str, val_new: Any, val_prev: Any
    ) -> Dict[str, Any]:
        """Write ``val_new`` to ``param`` via value.save.php.

        ``val_prev`` must be the parameter's current value: the controller
        uses it for optimistic locking, so it is sent exactly once and never
        guessed. Returns the controller's JSON response on success; raises
        InnotempCommandError if the controller does not answer with
        ``{"info": "success", ...}`` and InnotempApiError (or a subclass) for
        transport/session failures.
        """
        command_data = {
            "un": self._username,
            "pw": self._password,
            "room_id": str(room_id),
            "param": param,
            "val_new": format_api_value(val_new),
            "val_prev": format_api_value(val_prev),
        }
        _LOGGER.debug(
            "[innotemp] Sending command: room=%s, param=%s, new=%s, prev=%s",
            room_id,
            param,
            command_data["val_new"],
            command_data["val_prev"],
        )
        result = await self._execute_with_retry(
            "POST", "value.save.php", data=command_data
        )
        if isinstance(result, dict) and result.get("info") == "success":
            _LOGGER.debug("[innotemp] Command accepted: %s", result)
            return result
        raise InnotempCommandError(
            f"Controller did not confirm setting {param}={command_data['val_new']} "
            f"(val_prev={command_data['val_prev']!r}): {result!r}"
        )

    async def async_get_signal_names(self) -> list[str]:
        """Fetch (and cache) the list of signal names for the SSE stream."""
        if self._signal_names_cache is not None:
            return self._signal_names_cache

        init_data = {"init": "1", "un": self._username, "pw": self._password}
        response = await self._execute_with_retry(
            "POST", "live_signal.read.php", data=init_data
        )
        if isinstance(response, list) and response:
            _LOGGER.debug("[innotemp] Fetched %d signal names", len(response))
            self._signal_names_cache = [str(name) for name in response]
            return self._signal_names_cache

        self._signal_names_cache = None
        raise InnotempInvalidResponseError(
            f"Signal names response was {type(response).__name__}, "
            f"expected a non-empty list: {str(response)[:200]}"
        )

    # Backwards-compatible private alias.
    _get_signal_names = async_get_signal_names

    async def _async_read_sse_stream(
        self, signal_names: list[str], callback: Callable[[Dict[str, Any]], Any]
    ) -> int:
        """Connect to the SSE endpoint and forward parsed messages.

        Returns the number of data messages processed before the stream ended.
        """
        sse_url = f"{self._base_url}/live_signal.read.SSE.php"
        msg_count = 0
        # allow_redirects=False: an expired session makes the server redirect
        # to the login page. Following it would yield an HTTP-200 HTML page
        # with no SSE data and the listener would reconnect forever without
        # ever re-authenticating.
        async with self._session.get(
            sse_url, allow_redirects=False, timeout=SSE_TIMEOUT
        ) as response:
            _LOGGER.debug("[innotemp] SSE connected: status=%s", response.status)
            if response.status in (301, 302, 303, 307, 308):
                location = response.headers.get("Location", "unknown")
                raise InnotempAuthError(f"SSE request redirected to {location}")
            response.raise_for_status()
            async for line in response.content:
                if not line.startswith(b"data:"):
                    continue
                values = parse_sse_data(line)
                if values is None:
                    _LOGGER.warning("[innotemp] SSE: unparseable line %r", line[:200])
                    continue
                msg_count += 1
                if len(values) != len(signal_names):
                    _LOGGER.warning(
                        "[innotemp] SSE length mismatch: expected %d signals, got "
                        "%d values; reconnecting with fresh signal names",
                        len(signal_names),
                        len(values),
                    )
                    self._signal_names_cache = None
                    break
                callback(dict(zip(signal_names, values)))
        return msg_count

    async def async_sse_connect(
        self, callback: Callable[[Dict[str, Any]], Any]
    ) -> None:
        """Start a background task that keeps the SSE stream connected.

        ``callback`` receives one ``{signal_name: value}`` dict per message.
        The task reconnects (with a fresh login) after any error or when the
        stream ends, waiting ``sse_reconnect_delay`` seconds in between.
        """
        if self._sse_task and not self._sse_task.done():
            _LOGGER.warning("[innotemp] SSE task is already running, skipping")
            return

        async def sse_listener():
            attempt = 0
            while True:
                attempt += 1
                msg_count = 0
                try:
                    if not self._is_logged_in:
                        await self.async_login()
                    signal_names = await self.async_get_signal_names()
                    _LOGGER.debug(
                        "[innotemp] SSE connecting (attempt #%d, %d signals)",
                        attempt,
                        len(signal_names),
                    )
                    msg_count = await self._async_read_sse_stream(
                        signal_names, callback
                    )
                except asyncio.CancelledError:
                    raise
                except InnotempApiError as e:
                    _LOGGER.warning("[innotemp] SSE error: %s", e)
                except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                    _LOGGER.warning(
                        "[innotemp] SSE connection error (%s): %s",
                        type(e).__name__,
                        e,
                    )
                except Exception as e:  # noqa: BLE001 - keep the listener alive
                    _LOGGER.exception("[innotemp] SSE unexpected error: %s", e)

                _LOGGER.debug(
                    "[innotemp] SSE disconnected after %d messages, reconnecting "
                    "in %ss",
                    msg_count,
                    self.sse_reconnect_delay,
                )
                # Per the API spec, a lost SSE stream should be followed by a
                # full re-authentication before reconnecting.
                self._is_logged_in = False
                await asyncio.sleep(self.sse_reconnect_delay)

        self._sse_task = asyncio.create_task(sse_listener())

    async def async_sse_disconnect(self) -> None:
        """Stop the SSE listener task."""
        if self._sse_task:
            self._sse_task.cancel()
            try:
                await self._sse_task
            except asyncio.CancelledError:
                pass
            finally:
                self._sse_task = None
