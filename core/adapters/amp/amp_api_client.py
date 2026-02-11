"""AMP (CubeCoders) API client for game server management."""

import asyncio
import functools
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Coroutine, Dict, List, Optional, TypeVar

import aiohttp

T = TypeVar("T")
AsyncFunc = Callable[..., Coroutine[Any, Any, T]]


class AMPAPIError(Exception):
    """Exception raised for AMP API errors."""

    pass


def _require_auth(func: AsyncFunc[T]) -> AsyncFunc[T]:
    """Decorator that ensures client is authenticated before calling method."""

    @functools.wraps(func)
    async def wrapper(self: "AMPAPIClient", *args: Any, **kwargs: Any) -> T:
        if not self.is_authenticated:
            raise AMPAPIError("Not authenticated - call login() first")
        return await func(self, *args, **kwargs)

    return wrapper  # type: ignore[return-value]


@dataclass
class ConsoleEntry:
    """Single console log entry from AMP."""

    timestamp: datetime
    source: str
    message_type: str
    contents: str

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ConsoleEntry":
        """Create ConsoleEntry from API response dict."""
        # AMP returns timestamp as ISO format or epoch
        ts = data.get("Timestamp", "")
        if isinstance(ts, str):
            try:
                timestamp = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            except ValueError:
                timestamp = datetime.now(timezone.utc)
        else:
            timestamp = (
                datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
                if ts > 1e10
                else datetime.fromtimestamp(ts, tz=timezone.utc)
            )

        return cls(
            timestamp=timestamp,
            source=data.get("Source", ""),
            message_type=data.get("Type", ""),
            contents=data.get("Contents", ""),
        )


@dataclass
class UpdateResponse:
    """Response from Core.GetUpdates API call."""

    console_entries: List[ConsoleEntry] = field(default_factory=list)
    status: Dict[str, Any] = field(default_factory=dict)
    messages: List[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "UpdateResponse":
        """Create UpdateResponse from API response dict."""
        console_entries = []
        _logger = logging.getLogger(__name__)
        for entry in data.get("ConsoleEntries", []):
            try:
                console_entries.append(ConsoleEntry.from_dict(entry))
            except Exception as e:
                _logger.debug(f"Failed to parse console entry: {e}")

        return cls(
            console_entries=console_entries,
            status=data.get("Status", {}),
            messages=data.get("Messages", []),
        )


class AMPAPIClient:
    """
    Async client for CubeCoders AMP API.

    Handles authentication and provides methods for:
    - Session management (login/logout)
    - Console message retrieval (Core.GetUpdates)
    - Command execution (Core.SendConsoleMessage)
    - Server status (Core.GetStatus)
    """

    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        instance_id: Optional[str] = None,
        timeout: float = 30.0,
    ):
        """
        Initialize AMP API client.

        Args:
            base_url: AMP panel URL (e.g., "http://localhost:8080")
            username: AMP username
            password: AMP password
            instance_id: Optional instance ID for multi-instance setups
            timeout: Request timeout in seconds
        """
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.instance_id = instance_id
        self.timeout = timeout

        self._session_id: Optional[str] = None
        self._instance_session_id: Optional[str] = (
            None  # Separate session for instance access
        )
        self._http_session: Optional[aiohttp.ClientSession] = None
        self._authenticated = False
        self.logger = logging.getLogger(__name__)

    @property
    def is_authenticated(self) -> bool:
        """Check if client is authenticated."""
        return self._authenticated and self._session_id is not None

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create HTTP session."""
        if self._http_session is None or self._http_session.closed:
            timeout = aiohttp.ClientTimeout(total=self.timeout)
            headers = {
                "Accept": "application/json",
                "Content-Type": "application/json",
                "User-Agent": "ASTRID-Framework/1.0",
            }
            self._http_session = aiohttp.ClientSession(timeout=timeout, headers=headers)
        return self._http_session

    async def _api_call(
        self,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
        use_instance: bool = True,
    ) -> Dict[str, Any]:
        """
        Make API call to AMP.

        Args:
            endpoint: API endpoint (e.g., "Core/GetUpdates")
            params: Optional parameters for the call
            use_instance: If True and instance_id is set, route through instance API

        Returns:
            API response as dict

        Raises:
            AMPAPIError: If API call fails
        """
        session = await self._get_session()

        # Route through instance API if instance_id is set
        if use_instance and self.instance_id and self._session_id:
            url = f"{self.base_url}/API/ADSModule/Servers/{self.instance_id}/API/{endpoint}"
        else:
            url = f"{self.base_url}/API/{endpoint}"

        payload = params or {}
        # Use instance session for instance-proxied calls, otherwise use ADS session
        if use_instance and self.instance_id and self._instance_session_id:
            payload["SESSIONID"] = self._instance_session_id
        elif self._session_id:
            payload["SESSIONID"] = self._session_id

        self.logger.debug(f"API call: POST {url}")

        try:
            async with session.post(url, json=payload) as response:
                if response.status != 200:
                    text = await response.text()
                    raise AMPAPIError(f"API call failed: {response.status} - {text}")

                # Handle empty responses and parse JSON
                text = await response.text()
                if not text.strip():
                    self.logger.debug(f"API response for {endpoint}: (empty)")
                    return {}

                try:
                    data = json.loads(text)
                except json.JSONDecodeError as e:
                    raise AMPAPIError(f"Invalid JSON response: {e} - {text[:200]}")

                self.logger.debug(f"API response for {endpoint}: {data}")

                # Check for API-level errors
                if isinstance(data, dict):
                    # AMP returns success field to indicate if the call worked
                    success = data.get("success", data.get("Success"))
                    if success is False:
                        error_msg = (
                            data.get("Message")
                            or data.get("error")
                            or data.get("reason")
                        )
                        self.logger.error(f"API call failed. Full response: {data}")
                        raise AMPAPIError(f"API error: {error_msg or data}")

                    # Also check for explicit error status
                    if data.get("Status") is False:
                        error_msg = data.get("Message") or data.get("error")
                        self.logger.error(f"API call failed. Full response: {data}")
                        raise AMPAPIError(f"API error: {error_msg or data}")

                return data

        except aiohttp.ClientError as e:
            raise AMPAPIError(f"HTTP error: {e}") from e
        except asyncio.TimeoutError as e:
            raise AMPAPIError(f"Request timeout after {self.timeout}s") from e

    async def login(self, two_factor_token: str = "") -> bool:
        """
        Authenticate with AMP API.

        Args:
            two_factor_token: Optional 2FA/TOTP token if account has 2FA enabled

        Returns:
            True if login succeeded

        Raises:
            AMPAPIError: If login fails
        """
        self.logger.info(f"Logging into AMP at {self.base_url}")

        try:
            # Login always goes to the main ADS, not through instance
            response = await self._api_call(
                "Core/Login",
                {
                    "username": self.username,
                    "password": self.password,
                    "token": two_factor_token,
                    "rememberMe": True,  # Get a remember token for future use
                },
                use_instance=False,  # Login to ADS, not instance
            )

            self.logger.debug(f"Login response: {response}")

            # Extract session ID - AMP API returns it under various key names
            if isinstance(response, dict):
                session_keys = [
                    "sessionID",
                    "SessionID",
                    "session_id",
                    "rememberMeToken",
                    "RememberMeToken",
                ]
                self._session_id = next(
                    (response.get(k) for k in session_keys if response.get(k)), None
                )

                # Some AMP versions return success without explicit session
                # but set cookies - check for success indicators
                if not self._session_id:
                    success = response.get("success", response.get("Success", False))
                    if success or response.get("result") == 0:
                        # Generate a placeholder - actual auth may be cookie-based
                        self._session_id = "cookie-auth"
                        self.logger.info(
                            "Auth appears cookie-based, no explicit session ID"
                        )

                if self._session_id:
                    self._authenticated = True
                    self.logger.info("Successfully authenticated with AMP")

                    # Auto-login to instance if instance_id is configured
                    if self.instance_id:
                        instance_session = await self.login_to_instance(
                            self.instance_id
                        )
                        if not instance_session:
                            self.logger.warning(
                                f"ADS login succeeded but instance login failed for {self.instance_id}"
                            )

                    return True

                # Log the response keys to help debug
                self.logger.error(f"Login response keys: {list(response.keys())}")
                self.logger.error(f"Login response: {response}")

            raise AMPAPIError("Login succeeded but no session ID returned")

        except AMPAPIError:
            self._authenticated = False
            self._session_id = None
            raise

    async def logout(self) -> None:
        """Logout and invalidate session."""
        if self._session_id:
            try:
                await self._api_call("Core/Logout")
            except AMPAPIError as e:
                self.logger.debug(f"Logout error (ignored): {e}")
            finally:
                self._session_id = None
                self._authenticated = False

    async def close(self) -> None:
        """Close HTTP session and cleanup."""
        await self.logout()
        if self._http_session and not self._http_session.closed:
            await self._http_session.close()

    @_require_auth
    async def get_updates(self) -> UpdateResponse:
        """Get latest updates including console entries."""
        response = await self._api_call("Core/GetUpdates")
        return UpdateResponse.from_dict(response)

    @_require_auth
    async def get_status(self) -> Dict[str, Any]:
        """Get current server status."""
        return await self._api_call("Core/GetStatus")

    @_require_auth
    async def get_instance_endpoints(self, instance_id: str) -> Dict[str, Any]:
        """Get API endpoints for a specific instance."""
        return await self._api_call(
            "ADSModule/GetApplicationEndpoints",
            {"instanceId": instance_id},
            use_instance=False,
        )

    @_require_auth
    async def login_to_instance(self, instance_id: str) -> Optional[str]:
        """Login to a specific instance via ADS proxy, returns instance session ID."""
        # Call Core/Login through the instance proxy endpoint
        url = f"{self.base_url}/API/ADSModule/Servers/{instance_id}/API/Core/Login"
        session = await self._get_session()

        payload = {
            "username": self.username,
            "password": self.password,
            "token": "",
            "rememberMe": False,
            "SESSIONID": self._session_id,  # Use ADS session to access the proxy
        }

        self.logger.debug(f"Instance login: POST {url}")

        try:
            async with session.post(url, json=payload) as response:
                text = await response.text()
                if not text.strip():
                    self.logger.warning("Instance login returned empty response")
                    return None

                try:
                    data = json.loads(text)
                except json.JSONDecodeError:
                    self.logger.warning(
                        f"Instance login returned non-JSON: {text[:100]}"
                    )
                    return None

                self.logger.debug(f"Instance login response: {data}")

                # Extract instance session ID
                session_keys = ["sessionID", "SessionID", "session_id"]
                instance_session = next(
                    (data.get(k) for k in session_keys if data.get(k)), None
                )

                if instance_session:
                    self._instance_session_id = instance_session
                    self.logger.info(f"Successfully logged into instance {instance_id}")

                return instance_session

        except aiohttp.ClientError as e:
            self.logger.error(f"Instance login HTTP error: {e}")
            return None

    @_require_auth
    async def send_console_message(self, message: str) -> bool:
        """Send command/message to server console."""
        await self._api_call("Core/SendConsoleMessage", {"message": message})
        return True

    @_require_auth
    async def start_instance(self) -> bool:
        """Start the game server instance."""
        await self._api_call("Core/Start")
        return True

    @_require_auth
    async def stop_instance(self) -> bool:
        """Stop the game server instance."""
        await self._api_call("Core/Stop")
        return True

    @_require_auth
    async def restart_instance(self) -> bool:
        """Restart the game server instance."""
        await self._api_call("Core/Restart")
        return True
