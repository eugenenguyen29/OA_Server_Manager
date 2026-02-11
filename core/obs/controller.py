import asyncio
import base64
import hashlib
import json
import logging
from typing import Any, Dict, Optional

import websockets


class OBSWebSocketClient:
    """
    OBS WebSocket client for controlling OBS Studio via WebSocket.

    Supports OBS WebSocket 5.x protocol with authentication.
    """

    def __init__(
        self, host: str = "localhost", port: int = 4455, password: Optional[str] = None
    ):
        self.host = host
        self.port = port
        self.password = password
        self.websocket = None
        self.request_id_counter = 0
        self.logger = logging.getLogger(__name__)

    async def connect(self) -> bool:
        """Connect to OBS WebSocket server."""
        try:
            uri = f"ws://{self.host}:{self.port}"
            # Try connection without subprotocol first (more compatible)
            try:
                self.websocket = await websockets.connect(uri)
                self.logger.info(
                    f"Connected to OBS WebSocket at {uri} (no subprotocol)"
                )
            except Exception:
                # Fallback to subprotocol if needed
                self.websocket = await websockets.connect(
                    uri, subprotocols=["obswebsocket"]
                )
                self.logger.info(
                    f"Connected to OBS WebSocket at {uri} (with subprotocol)"
                )

            # Wait for Hello message with timeout
            hello_message = await asyncio.wait_for(self.websocket.recv(), timeout=10.0)
            hello_data = json.loads(hello_message)

            if hello_data["op"] != 0:  # OpCode 0 = Hello
                raise Exception(
                    f"Expected Hello message, got op code {hello_data['op']}"
                )

            self.logger.info(f"OBS Version: {hello_data['d']['obsStudioVersion']}")
            self.logger.info(
                f"WebSocket Version: {hello_data['d']['obsWebSocketVersion']}"
            )

            # Send Identify message
            await self._identify(hello_data["d"])

            # Wait for Identified message with timeout
            identified_message = await asyncio.wait_for(
                self.websocket.recv(), timeout=10.0
            )
            identified_data = json.loads(identified_message)

            if identified_data["op"] != 2:  # OpCode 2 = Identified
                raise Exception(
                    f"Expected Identified message, got op code {identified_data['op']}"
                )

            self.logger.info("Successfully identified with OBS WebSocket")
            return True

        except Exception as e:
            self.logger.error(f"Failed to connect to OBS: {e}")
            return False

    async def _identify(self, hello_data: Dict[str, Any]) -> None:
        """Send Identify message to OBS WebSocket server."""
        identify_message = {
            "op": 1,  # OpCode 1 = Identify
            "d": {
                "rpcVersion": 1,
                "eventSubscriptions": 33,  # General + Config events
            },
        }

        if "authentication" in hello_data and self.password:
            auth_data = hello_data["authentication"]
            challenge = auth_data["challenge"]
            salt = auth_data["salt"]

            secret = base64.b64encode(
                hashlib.sha256((self.password + salt).encode()).digest()
            ).decode()

            auth_response = base64.b64encode(
                hashlib.sha256((secret + challenge).encode()).digest()
            ).decode()

            identify_message["d"]["authentication"] = auth_response
            self.logger.info("Authentication required - including auth response")

        await self.websocket.send(json.dumps(identify_message))

    def _get_next_request_id(self) -> str:
        """Generate next request ID."""
        self.request_id_counter += 1
        return str(self.request_id_counter)

    async def send_request(
        self, request_type: str, request_data: Optional[Dict] = None
    ) -> Dict[str, Any]:
        """Send a request to OBS and wait for response."""
        if not self.websocket:
            raise Exception("Not connected to OBS WebSocket")

        request_id = self._get_next_request_id()

        request_message = {
            "op": 6,  # OpCode 6 = Request
            "d": {"requestType": request_type, "requestId": request_id},
        }

        if request_data:
            request_message["d"]["requestData"] = request_data

        await self.websocket.send(json.dumps(request_message))

        timeout_seconds = 10.0
        start_time = asyncio.get_event_loop().time()

        while True:
            elapsed = asyncio.get_event_loop().time() - start_time
            if elapsed > timeout_seconds:
                raise Exception(f"Request timeout after {timeout_seconds}s")

            response_message = await asyncio.wait_for(
                self.websocket.recv(), timeout=timeout_seconds - elapsed
            )
            response_data = json.loads(response_message)

            if response_data["op"] == 7:  # OpCode 7 = RequestResponse
                if response_data["d"]["requestId"] == request_id:
                    if response_data["d"]["requestStatus"]["result"]:
                        return response_data["d"].get("responseData", {})
                    else:
                        error_code = response_data["d"]["requestStatus"]["code"]
                        error_comment = response_data["d"]["requestStatus"].get(
                            "comment", "Unknown error"
                        )
                        raise Exception(
                            f"OBS Request failed: {error_code} - {error_comment}"
                        )

    async def start_record(self) -> bool:
        """Start recording in OBS."""
        try:
            await self.send_request("StartRecord")
            self.logger.info("Recording started")
            return True
        except Exception as e:
            self.logger.error(f"Failed to start recording: {e}")
            return False

    async def stop_record(self) -> bool:
        """Stop recording in OBS."""
        try:
            response = await self.send_request("StopRecord")
            output_path = response.get("outputPath", "Unknown")
            self.logger.info(f"Recording stopped - saved to: {output_path}")
            return True
        except Exception as e:
            self.logger.error(f"Failed to stop recording: {e}")
            return False

    async def get_record_status(self) -> Dict[str, Any]:
        """Get current recording status."""
        try:
            response = await self.send_request("GetRecordStatus")
            return {
                "active": response.get("outputActive", False),
                "paused": response.get("outputPaused", False),
                "duration": response.get("outputDuration", 0),
                "bytes": response.get("outputBytes", 0),
            }
        except Exception as e:
            self.logger.error(f"Failed to get recording status: {e}")
            return {"active": False, "paused": False, "duration": 0, "bytes": 0}

    async def get_scene_list(self) -> list:
        """Get list of available scenes."""
        try:
            response = await self.send_request("GetSceneList")
            scenes = response.get("scenes", [])
            return [scene["sceneName"] for scene in scenes]
        except Exception as e:
            self.logger.error(f"Failed to get scene list: {e}")
            return []

    async def set_current_scene(self, scene_name: str) -> bool:
        """Set the current scene."""
        try:
            await self.send_request("SetCurrentProgramScene", {"sceneName": scene_name})
            self.logger.info(f"Switched to scene: {scene_name}")
            return True
        except Exception as e:
            self.logger.error(f"Failed to set scene {scene_name}: {e}")
            return False

    async def disconnect(self) -> None:
        """Disconnect from OBS WebSocket server."""
        if self.websocket:
            await self.websocket.close()
            self.websocket = None
            self.logger.info("Disconnected from OBS WebSocket")
