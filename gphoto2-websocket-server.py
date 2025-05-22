#!/usr/bin/env python3
import asyncio
import json
import logging
import base64
import os
import tempfile
import time
from typing import Dict, List, Optional, Union, Callable, Set, Any
from contextlib import contextmanager
from uuid import uuid4

import websockets
import gphoto2 as gp
from pydantic import BaseModel, ValidationError

# Configure logging with environment variable support
logging.basicConfig(
    level=os.getenv('LOG_LEVEL', 'INFO').upper(),
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# Pydantic models for input validation
class SelectCameraPayload(BaseModel):
    port: str

class DownloadImagePayload(BaseModel):
    camera_filepath: str

class GetConfigPayload(BaseModel):
    name: Optional[str] = None

class SetConfigPayload(BaseModel):
    name: str
    value: str

class CameraInfo(BaseModel):
    name: str
    port: str
    index: int

class ConfigDetails(BaseModel):
    config_name: str
    label: str
    type: str
    value: Union[str, float, int]
    readonly: bool
    choices: Optional[List[str]] = None
    range: Optional[Dict[str, float]] = None
    path: Optional[str] = None

class DownloadImageResponse(BaseModel):
    filename: str
    mimetype: str
    image_b64: str

class GPhoto2API:
    def __init__(self):
        """Initialize the GPhoto2 API context and camera resources."""
        self.context = gp.Context()
        self.camera: Optional[gp.Camera] = None
        self.camera_port: Optional[str] = None
        self.last_captured_file: Optional[gp.CameraFilePath] = None

    def list_cameras(self) -> List[CameraInfo]:
        """List all available cameras.

        Returns:
            List of CameraInfo objects containing camera name, port, and index.
        Raises:
            Exception: If camera detection fails.
        """
        try:
            camera_list = []
            camera_list_obj = gp.PortInfoList()
            camera_list_obj.load()

            abilities_list = gp.CameraAbilitiesList()
            abilities_list.load(self.context)

            cameras = abilities_list.detect(camera_list_obj, self.context)
            for idx, camera in enumerate(cameras):
                # Handle tuple-based output (model, port)
                if isinstance(camera, tuple):
                    model, port = camera
                    camera_list.append(CameraInfo(
                        name=model,
                        port=port,
                        index=idx
                    ))
                else:
                    # Handle object-based output
                    port_info = camera_list_obj.get_info(camera.port)
                    camera_list.append(CameraInfo(
                        name=camera.model,
                        port=port_info.get_path(),
                        index=idx
                    ))
            return camera_list
        except Exception as e:
            logger.error(f"Error listing cameras: {str(e)}")
            raise

    def select_camera(self, port: str) -> None:
        """Select and initialize a camera by its port.

        Args:
            port: The camera port path (e.g., 'usb:001,006').
        Raises:
            ValueError: If port is invalid.
            Exception: If camera initialization fails.
        """
        try:
            if self.camera is not None:
                self.camera.exit()
                self.camera = None

            self.camera = gp.Camera()
            self.camera_port = port

            port_info_list = gp.PortInfoList()
            port_info_list.load()
            idx = port_info_list.lookup_path(port)
            self.camera.set_port_info(port_info_list.get_info(idx))

            self.camera.init(self.context)
            logger.info(f"Camera selected on port {port}")
        except Exception as e:
            logger.error(f"Error selecting camera: {str(e)}")
            self.camera = None
            raise ValueError(f"Failed to select camera on port {port}: {str(e)}")

    def get_camera_abilities(self) -> List[str]:
        """Get the supported abilities of the currently selected camera.

        Returns:
            List of supported operations (e.g., ['capture_image', 'config']).
        Raises:
            ValueError: If no camera is selected.
        """
        if not self.camera:
            raise ValueError("No camera selected.")

        abilities = self.camera.get_abilities()
        supported_ops = []
        if abilities.operations & gp.GP_OPERATION_CAPTURE_IMAGE:
            supported_ops.append("capture_image")
        if abilities.operations & gp.GP_OPERATION_CAPTURE_VIDEO:
            supported_ops.append("capture_video")
        if abilities.operations & gp.GP_OPERATION_CAPTURE_PREVIEW:
            supported_ops.append("capture_preview")
        if abilities.operations & gp.GP_OPERATION_CONFIG:
            supported_ops.append("config")
        if abilities.operations & gp.GP_OPERATION_TRIGGER_CAPTURE:
            supported_ops.append("trigger_capture")
        return supported_ops

    def capture_image(self) -> str:
        """Capture an image and return the camera filepath.

        Returns:
            The camera filepath (e.g., '/store_00010001/DCIM/100CANON/IMG_0001.JPG').
        Raises:
            ValueError: If no camera is selected or capture is not supported.
            Exception: If capture fails.
        """
        if not self.camera:
            raise ValueError("No camera selected.")
        abilities = self.camera.get_abilities()
        if not abilities.operations & gp.GP_OPERATION_CAPTURE_IMAGE:
            raise ValueError("Camera does not support image capture.")

        try:
            file_path = self.camera.capture(gp.GP_CAPTURE_IMAGE, self.context)
            self.last_captured_file = file_path
            logger.info(f"Image captured: {file_path.folder}/{file_path.name}")
            return f"{file_path.folder}/{file_path.name}"
        except Exception as e:
            logger.error(f"Error capturing image: {str(e)}")
            raise

    def download_image(self, camera_filepath: str) -> DownloadImageResponse:
        """Download an image from the camera and return it as base64.

        Args:
            camera_filepath: Path to the image on the camera.
        Returns:
            DownloadImageResponse with filename, MIME type, and base64-encoded image.
        Raises:
            ValueError: If no camera is selected or filepath is invalid.
            Exception: If download fails.
        """
        if not self.camera:
            raise ValueError("No camera selected.")

        try:
            folder, filename = os.path.split(camera_filepath)
            with tempfile.NamedTemporaryFile(suffix=f"-{filename}", delete=True) as temp_file:
                camera_file = self.camera.file_get(folder, filename, gp.GP_FILE_TYPE_NORMAL, self.context)
                camera_file.save(temp_file.name)
                temp_file.seek(0)
                file_data = temp_file.read()
                base64_data = base64.b64encode(file_data).decode('utf-8')
                return DownloadImageResponse(
                    filename=filename,
                    mimetype=camera_file.get_mime_type(),
                    image_b64=base64_data
                )
        except Exception as e:
            logger.error(f"Error downloading image {camera_filepath}: {str(e)}")
            raise

    def get_config(self, config_name: Optional[str] = None) -> Union[List[ConfigDetails], ConfigDetails]:
        """Get camera configuration(s).

        Args:
            config_name: Optional specific config name to retrieve.
        Returns:
            List of ConfigDetails for all configs if config_name is None, else a single ConfigDetails.
        Raises:
            ValueError: If no camera is selected or config_name is not found.
            Exception: If config retrieval fails.
        """
        if not self.camera:
            raise ValueError("No camera selected.")

        try:
            config = self.camera.get_config(self.context)
            if config_name:
                child = self._find_config_by_name(config, config_name)
                if not child:
                    raise ValueError(f"Config '{config_name}' not found.")
                return ConfigDetails(**self._get_config_details(child))
            else:
                configs = []
                for i in range(config.count_children()):
                    child = config.get_child(i)
                    self._extract_config_details(child, configs)
                return [ConfigDetails(**cfg) for cfg in configs]
        except Exception as e:
            logger.error(f"Error getting configs: {str(e)}")
            raise

    def set_config(self, config_name: str, value: str) -> ConfigDetails:
        """Set a camera configuration value.

        Args:
            config_name: The configuration name to set.
            value: The value to set.
        Returns:
            Updated ConfigDetails for the set configuration.
        Raises:
            ValueError: If no camera, config is read-only, or value is invalid.
            Exception: If setting config fails.
        """
        if not self.camera:
            raise ValueError("No camera selected.")

        try:
            config = self.camera.get_config(self.context)
            child = self._find_config_by_name(config, config_name)
            if not child:
                raise ValueError(f"Config '{config_name}' not found.")
            if child.get_readonly():
                raise ValueError(f"Config '{config_name}' is read-only.")

            config_type = child.get_type()
            if config_type in (gp.GP_WIDGET_MENU, gp.GP_WIDGET_RADIO):
                choices = [child.get_choice(i) for i in range(child.count_choices())]
                if value not in choices:
                    raise ValueError(f"Value '{value}' not in choices: {choices}")
                child.set_value(value)
            elif config_type == gp.GP_WIDGET_RANGE:
                float_val = float(value)
                low, high, inc = child.get_range()
                if float_val < low or float_val > high:
                    raise ValueError(f"Value {float_val} out of range [{low}, {high}]")
                child.set_value(float_val)
            elif config_type == gp.GP_WIDGET_TOGGLE:
                int_val = int(value)
                if int_val not in [0, 1]:
                    raise ValueError("Toggle value must be 0 or 1")
                child.set_value(int_val)
            elif config_type == gp.GP_WIDGET_DATE:
                timestamp = int(value)
                child.set_value(timestamp)
            else:
                child.set_value(value)

            self.camera.set_config(config, self.context)
            logger.info(f"Config '{config_name}' set to '{value}'")
            return ConfigDetails(**self._get_config_details(child))
        except Exception as e:
            logger.error(f"Error setting config '{config_name}' to '{value}': {str(e)}")
            raise

    def _find_config_by_name(self, config, target_name: str, path: str = "") -> Optional[Any]:
        """Recursively find a config by name."""
        if config.get_name() == target_name:
            return config
        for i in range(config.count_children()):
            child = config.get_child(i)
            result = self._find_config_by_name(child, target_name)
            if result:
                return result
        return None

    def _get_config_details(self, config) -> Dict[str, Any]:
        """Get detailed information about a config item."""
        details = {
            "config_name": config.get_name(),
            "label": config.get_label(),
            "type": self._get_type_name(config.get_type()),
            "value": self._get_formatted_value(config),
            "readonly": config.get_readonly()
        }
        if config.get_type() in (gp.GP_WIDGET_MENU, gp.GP_WIDGET_RADIO):
            details["choices"] = [config.get_choice(i) for i in range(config.count_choices())]
        if config.get_type() == gp.GP_WIDGET_RANGE:
            low, high, inc = config.get_range()
            details["range"] = {"min": low, "max": high, "step": inc}
        return details

    def _extract_config_details(self, config, results: List[Dict[str, Any]], path: str = "") -> None:
        """Recursively extract all config details."""
        try:
            config_type = config.get_type()
            if config_type in (gp.GP_WIDGET_SECTION, gp.GP_WIDGET_WINDOW):
                for i in range(config.count_children()):
                    child = config.get_child(i)
                    new_path = f"{path}/{config.get_name()}" if path else config.get_name()
                    self._extract_config_details(child, results, new_path)
                return
            details = self._get_config_details(config)
            details["path"] = f"{path}/{config.get_name()}" if path else config.get_name()
            results.append(details)
        except Exception as e:
            logger.warning(f"Error extracting config details: {str(e)}")

    def _get_type_name(self, type_val: int) -> str:
        """Convert numeric type to string name."""
        type_names = {
            gp.GP_WIDGET_WINDOW: "window",
            gp.GP_WIDGET_SECTION: "section",
            gp.GP_WIDGET_TEXT: "text",
            gp.GP_WIDGET_RANGE: "range",
            gp.GP_WIDGET_TOGGLE: "toggle",
            gp.GP_WIDGET_RADIO: "radio",
            gp.GP_WIDGET_MENU: "menu",
            gp.GP_WIDGET_DATE: "date",
            gp.GP_WIDGET_BUTTON: "button",
        }
        return type_names.get(type_val, f"unknown({type_val})")

    def _get_formatted_value(self, config) -> Union[str, float, int]:
        """Get a formatted value based on config type."""
        try:
            config_type = config.get_type()
            value = config.get_value()
            if config_type in (gp.GP_WIDGET_TEXT, gp.GP_WIDGET_RADIO, gp.GP_WIDGET_MENU):
                return str(value) if value is not None else ""
            elif config_type == gp.GP_WIDGET_RANGE:
                return float(value) if value is not None else 0.0
            elif config_type == gp.GP_WIDGET_TOGGLE:
                return int(value) if value is not None else 0
            elif config_type == gp.GP_WIDGET_DATE:
                return int(value) if value is not None else 0
            return str(value) if value is not None else ""
        except Exception as e:
            logger.warning(f"Error formatting value: {str(e)}")
            return ""

    def cleanup(self) -> None:
        """Clean up camera resources."""
        if self.camera:
            try:
                self.camera.exit()
                logger.info("Camera connection closed.")
            except Exception as e:
                logger.error(f"Error closing camera: {str(e)}")
            finally:
                self.camera = None

    def __enter__(self):
        """Enter context manager."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Exit context manager, cleaning up resources."""
        self.cleanup()

class WebSocketServer:
    def __init__(self):
        """Initialize WebSocket server with environment-based configuration and log detected cameras."""
        self.host = os.getenv('WS_HOST', 'localhost')
        self.port = int(os.getenv('WS_PORT', 8765))
        self.auth_token = os.getenv('API_TOKEN', str(uuid4()))
        self.gphoto_api = GPhoto2API()
        self.clients: Set = set()
        self.command_handlers: Dict[str, Callable] = {
            "list_cameras": self.handle_list_cameras,
            "select_camera": self.handle_select_camera,
            "capture_image": self.handle_capture_image,
            "download_last_image": self.handle_download_last_image,
            "get_config": self.handle_get_config,
            "set_config": self.handle_set_config,
        }

        # Log detected cameras and their supported functions
        self._log_detected_cameras()

    def _log_detected_cameras(self) -> None:
        """Log detected cameras and their supported functions at startup."""
        logger.info("Detecting connected cameras...")
        try:
            cameras = self.gphoto_api.list_cameras()
            if not cameras:
                logger.info("No cameras detected.")
                return

            for camera in cameras:
                logger.info(f"Camera detected: {camera.name} (Port: {camera.port}, Index: {camera.index})")
                try:
                    # Temporarily select the camera to check abilities
                    self.gphoto_api.select_camera(camera.port)
                    abilities = self.gphoto_api.get_camera_abilities()
                    logger.info(f"Supported functions for {camera.name}: {', '.join(abilities) or 'None'}")
                except Exception as e:
                    logger.warning(f"Could not retrieve abilities for {camera.name}: {str(e)}")
                finally:
                    # Ensure cleanup even if abilities retrieval fails
                    self.gphoto_api.cleanup()
        except Exception as e:
            logger.error(f"Error detecting cameras: {str(e)}")

    async def handler(self, websocket):
        """Handle WebSocket connection with authentication.

        Args:
            websocket: WebSocket connection object.
        """
        client_id = id(websocket)
        client_ip = websocket.remote_address[0]
        logger.info(f"Client connected: {client_id} from {client_ip}")
        self.clients.add(websocket)

        try:
            # Authenticate client
            auth_message = await asyncio.wait_for(websocket.recv(), timeout=5.0)
            auth_data = json.loads(auth_message)
            if auth_data.get('token') != self.auth_token:
                await websocket.send(json.dumps({"status": "error", "message": "Unauthorized"}))
                await websocket.close()
                logger.warning(f"Authentication failed for client {client_id} from {client_ip}")
                return

            logger.info(f"Client {client_id} authenticated successfully")

            async for message in websocket:
                try:
                    data = json.loads(message)
                    command = data.get('command')
                    payload = data.get('payload', {})
                    logger.info(f"Received command: {command} with payload: {payload} from {client_ip}")
                    response = await self.process_command(command, payload)
                    # Add command to response for client handling
                    response['command'] = command
                    await websocket.send(json.dumps(response))
                except json.JSONDecodeError:
                    logger.error(f"Invalid JSON from {client_ip}: {message}")
                    await websocket.send(json.dumps({"status": "error", "message": "Invalid JSON"}))
                except Exception as e:
                    logger.error(f"Error processing command from {client_ip}: {str(e)}")
                    await websocket.send(json.dumps({"status": "error", "message": str(e)}))
        except websockets.exceptions.ConnectionClosed:
            logger.info(f"Client disconnected: {client_id} from {client_ip}")
        except asyncio.TimeoutError:
            logger.warning(f"Authentication timeout for client {client_id} from {client_ip}")
        except Exception as e:
            logger.error(f"Unexpected error for client {client_id} from {client_ip}: {str(e)}")
        finally:
            self.clients.discard(websocket)

    async def process_command(self, command: str, payload: Dict) -> Dict:
        """Process a client command.

        Args:
            command: The command name.
            payload: The command payload.
        Returns:
            Response dictionary with status and data or error message.
        """
        handler = self.command_handlers.get(command)
        if not handler:
            return {"status": "error", "message": f"Unknown command: {command}"}
        try:
            return await handler(payload)
        except Exception as e:
            logger.error(f"Error in command {command}: {str(e)}")
            return {"status": "error", "message": str(e)}

    async def handle_list_cameras(self, payload: Dict) -> Dict:
        """Handle list_cameras command."""
        cameras = self.gphoto_api.list_cameras()
        return {"status": "ok", "cameras": [camera.dict() for camera in cameras]}

    async def handle_select_camera(self, payload: Dict) -> Dict:
        """Handle select_camera command."""
        try:
            validated = SelectCameraPayload(**payload)
            self.gphoto_api.select_camera(validated.port)
            return {"status": "ok", "message": f"Camera on port {validated.port} selected"}
        except ValidationError as e:
            return {"status": "error", "message": f"Invalid payload: {str(e)}"}

    async def handle_capture_image(self, payload: Dict) -> Dict:
        """Handle capture_image command."""
        camera_filepath = self.gphoto_api.capture_image()
        return {
            "status": "ok",
            "message": "Image captured successfully",
            "camera_filepath": camera_filepath
        }

    async def handle_download_last_image(self, payload: Dict) -> Dict:
        """Handle download_last_image command."""
        try:
            validated = DownloadImagePayload(**payload)
            image_data = self.gphoto_api.download_image(validated.camera_filepath)
            return {
                "status": "ok",
                "message": "Image downloaded successfully",
                **image_data.dict()
            }
        except ValidationError as e:
            return {"status": "error", "message": f"Invalid payload: {str(e)}"}

    async def handle_get_config(self, payload: Dict) -> Dict:
        """Handle get_config command."""
        try:
            validated = GetConfigPayload(**payload)
            config_data = self.gphoto_api.get_config(validated.name)
            if validated.name:
                return {"status": "ok", **config_data.dict()}
            return {"status": "ok", "configs": [cfg.dict() for cfg in config_data]}
        except ValidationError as e:
            return {"status": "error", "message": f"Invalid payload: {str(e)}"}

    async def handle_set_config(self, payload: Dict) -> Dict:
        """Handle set_config command."""
        try:
            validated = SetConfigPayload(**payload)
            config_data = self.gphoto_api.set_config(validated.name, validated.value)
            return {
                "status": "ok",
                "message": f"Config {validated.name} set to {validated.value}",
                **config_data.dict()
            }
        except ValidationError as e:
            return {"status": "error", "message": f"Invalid payload: {str(e)}"}

    async def start(self):
        """Start the WebSocket server."""
        server = await websockets.serve(self.handler, self.host, self.port)
        logger.info(f"WebSocket server started at ws://{self.host}:{self.port}")
        logger.info(f"API Token: {self.auth_token}")
        try:
            await server.wait_closed()
        except Exception as e:
            logger.error(f"Server error: {str(e)}")
        finally:
            self.gphoto_api.cleanup()

if __name__ == "__main__":
    with GPhoto2API() as gphoto_api:
        server = WebSocketServer()
        try:
            asyncio.run(server.start())
        except KeyboardInterrupt:
            logger.info("Server stopped by user")
        except Exception as e:
            logger.error(f"Fatal error: {str(e)}")
