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
        self._pending_requests: Dict[str, asyncio.Future] = {}
        self._reader_task: Optional[asyncio.Task] = None

    async def connect(self) -> bool:
        """Connect to OBS WebSocket server."""
        try:
            uri = f"ws://{self.host}:{self.port}"
            self.websocket = await websockets.connect(uri, open_timeout=10)
            self.logger.info(f"Connected to OBS WebSocket at {uri}")

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

            # Start background message reader for dispatching responses
            self._reader_task = asyncio.create_task(self._message_reader())

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

    async def _message_reader(self):
        """Background task that dispatches incoming WebSocket messages."""
        try:
            while self.websocket:
                try:
                    raw = await self.websocket.recv()
                    msg = json.loads(raw)
                    op_code = msg.get("op")
                    if op_code == 7:  # RequestResponse
                        request_id = msg.get("d", {}).get("requestId")
                        fut = self._pending_requests.pop(request_id, None)
                        if fut and not fut.done():
                            fut.set_result(msg.get("d", {}))
                    # op_code 5 = Event - can be logged or handled later
                except websockets.ConnectionClosed:
                    break
                except Exception:
                    continue
        except Exception:
            pass

    async def send_request(
        self, request_type: str, request_data: Optional[Dict] = None
    ) -> Dict[str, Any]:
        """Send a request to OBS and wait for response."""
        if not self.websocket:
            raise Exception("Not connected to OBS WebSocket")

        request_id = self._get_next_request_id()

        fut = asyncio.get_running_loop().create_future()
        self._pending_requests[request_id] = fut

        payload = {
            "op": 6,  # OpCode 6 = Request
            "d": {"requestType": request_type, "requestId": request_id},
        }

        if request_data:
            payload["d"]["requestData"] = request_data

        await self.websocket.send(json.dumps(payload))

        try:
            result = await asyncio.wait_for(fut, timeout=15.0)
        except asyncio.TimeoutError:
            self._pending_requests.pop(request_id, None)
            raise

        if result.get("requestStatus", {}).get("result"):
            return result.get("responseData", {})
        else:
            error_code = result.get("requestStatus", {}).get("code")
            error_comment = result.get("requestStatus", {}).get(
                "comment", "Unknown error"
            )
            raise Exception(f"OBS Request failed: {error_code} - {error_comment}")

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
        if self._reader_task:
            self._reader_task.cancel()
            self._reader_task = None
        for fut in self._pending_requests.values():
            if not fut.done():
                fut.cancel()
        self._pending_requests.clear()
        if self.websocket:
            await self.websocket.close()
            self.websocket = None
            self.logger.info("Disconnected from OBS WebSocket")
