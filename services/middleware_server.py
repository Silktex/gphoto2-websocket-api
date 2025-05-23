import asyncio
import base64 
import json
import logging
import os
import time 
import shutil 
import mimetypes 
from enum import Enum
from typing import Any, Dict, List, Optional, Callable 

import uvicorn
import websockets 
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ValidationError

# --- Basic Logging Setup ---
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=LOG_LEVEL, format="%(asctime)s - %(levelname)s - %(name)s - %(message)s"
)
logger = logging.getLogger(__name__)

# --- Base Paths for Middleware Storage ---
MW_CAPTURES_BASE_DIR = "middleware_captures"
MW_PHOTOMETRIC_SETS_BASE_DIR = os.path.join(MW_CAPTURES_BASE_DIR, "photometric_sets")

# --- FastAPI Application Setup ---
app = FastAPI(title="Middleware WebSocket API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_credentials=True,
    allow_methods=["*"], 
    allow_headers=["*"], 
)

# --- Pydantic Models for Middleware API ---
class ActionTypeMiddleware(str, Enum):
    PING_MIDDLEWARE = "PING_MIDDLEWARE"
    PONG_MIDDLEWARE = "PONG_MIDDLEWARE" 
    LIST_CAMERAS_PI = "LIST_CAMERAS_PI" 
    SELECT_CAMERA_PI = "SELECT_CAMERA_PI"
    DESELECT_CAMERA_PI = "DESELECT_CAMERA_PI"
    GET_CONFIG_PI = "GET_CONFIG_PI"
    SET_CONFIG_PI = "SET_CONFIG_PI"
    CAPTURE_IMAGE_PI = "CAPTURE_IMAGE_PI" 
    GET_LIGHT_STATES_PI = "GET_LIGHT_STATES_PI"
    SET_LIGHT_STATE_PI = "SET_LIGHT_STATE_PI"
    START_LIVEVIEW_PI = "START_LIVEVIEW_PI"
    STOP_LIVEVIEW_PI = "STOP_LIVEVIEW_PI"
    LIVEVIEW_FRAME_MW = "LIVEVIEW_FRAME_MW" 
    CAPTURE_PHOTOMETRIC_SET_MW = "CAPTURE_PHOTOMETRIC_SET_MW" 
    PHOTOMETRIC_PROGRESS_MW = "PHOTOMETRIC_PROGRESS_MW"     
    IMAGE_DATA_FROM_PI = "IMAGE_DATA_FROM_PI" 
    # New actions for Middleware Image/Set Management
    LIST_IMAGE_SETS_MW = "LIST_IMAGE_SETS_MW"
    GET_IMAGE_SET_CONTENTS_MW = "GET_IMAGE_SET_CONTENTS_MW"
    DELETE_IMAGE_SET_MW = "DELETE_IMAGE_SET_MW"
    GET_IMAGE_DATA_MW = "GET_IMAGE_DATA_MW"

class MiddlewareRequest(BaseModel):
    action: ActionTypeMiddleware
    payload: Optional[Dict[str, Any]] = None
    request_id: Optional[str] = None

class MiddlewareResponse(BaseModel):
    action: ActionTypeMiddleware
    success: bool
    data: Optional[Any] = None
    error: Optional[str] = None
    request_id: Optional[str] = None

class PiCameraInfo(BaseModel): model: str; port: str
class SelectCameraPiPayload(BaseModel): model: Optional[str] = None; port: Optional[str] = None
class GetConfigPiPayload(BaseModel): config_name: Optional[str] = None
class SetConfigPiPayload(BaseModel): config_name: str; value: Any 
class CaptureImagePiPayload(BaseModel): download_on_pi: Optional[bool] = True
class SetLightStatePiPayload(BaseModel): light_name: str; state: bool
class LiveviewFrameMiddlewareData(BaseModel): frame: str; mimetype: str
class PhotometricSetMiddlewarePayload(BaseModel): light_sequence: List[str]; set_name_prefix: Optional[str] = None
class PhotometricProgressMiddlewareData(BaseModel): status: str; current_light: Optional[str] = None; set_folder_mw: str; error_detail: Optional[str] = None
class PhotometricSetMiddlewareResponseData(BaseModel): message: str; set_folder_mw: str; image_count: Optional[int] = None; images_captured_mw: Optional[List[str]] = None
class ImageDataFromPiPayload(BaseModel): image_b64: str; mimetype: str; original_filename: str; light_name_for_set: Optional[str] = None

# New Pydantic Models for Middleware Image/Set Management
class ImageSetContentsMiddlewarePayload(BaseModel): set_name: str
class DeleteImageSetMiddlewarePayload(BaseModel): set_name: str
class GetImageDataMiddlewarePayload(BaseModel): image_path_mw: str 

class ImageSetInfoMiddleware(BaseModel): name: str
class ImageFileDetailsMiddleware(BaseModel): filename: str; path_mw: str 
class ImageDataMiddlewareResponse(BaseModel): filename: str; image_b64: str; mimetype: str


# --- Path Safety Helper (Middleware Specific) ---
def _is_path_safe_mw(allowed_base_dir: str, user_provided_path_segment: str) -> bool:
    """
    Validates if a path constructed from a base directory and a user-provided segment
    is safe and remains within the allowed base directory.
    The user_provided_path_segment is expected to be a single segment (e.g., a directory name or filename)
    or a relative path from the base_dir.
    """
    # Normalize the allowed base directory to an absolute path
    abs_base_dir = os.path.abspath(allowed_base_dir)

    # Join the base directory with the user-provided segment and normalize
    # This resolves any ".." or "." components in user_provided_path_segment
    combined_path = os.path.join(abs_base_dir, user_provided_path_segment)
    abs_resolved_path = os.path.normpath(combined_path)

    # Check if the resolved absolute path starts with the absolute base directory path
    # This is the primary check for directory traversal
    if not abs_resolved_path.startswith(abs_base_dir):
        logger.warning(f"Path safety validation failed: Resolved path '{abs_resolved_path}' is outside base '{abs_base_dir}'. User segment: '{user_provided_path_segment}'")
        return False
    
    # As an additional check, ensure the original user_provided_path_segment itself doesn't try to escape upwards
    # This handles cases where user_provided_path_segment might be an absolute path itself.
    # os.path.join behaves such that if user_provided_path_segment is absolute, it becomes the result.
    if os.path.isabs(user_provided_path_segment):
        logger.warning(f"Path safety validation failed: User segment '{user_provided_path_segment}' is an absolute path.")
        return False

    # Check for ".." components in the original user_provided_path_segment to further prevent tricky inputs.
    # This is somewhat redundant if normpath works perfectly, but adds defense in depth.
    if ".." in user_provided_path_segment.split(os.path.sep):
        logger.warning(f"Path safety validation failed: '..' component detected in user segment '{user_provided_path_segment}'.")
        return False
        
    return True


# --- Pi WebSocket Client ---
class PiWebSocketClient: # ... (same as before, no changes needed for this task)
    def __init__(self, pi_server_url: str):
        self.pi_server_url = pi_server_url
        self.ws: Optional[websockets.WebSocketClientProtocol] = None
        self.is_connected_to_pi: bool = False
        self.is_connecting_to_pi: bool = False 
        self.pi_message_listeners: List[Callable] = [] 
        self.pi_command_responses: Dict[str, asyncio.Future] = {} 
        self.pi_request_id_counter: int = 0
        self._listen_task: Optional[asyncio.Task] = None 
        self._reconnect_lock = asyncio.Lock() 
        self.frontend_liveview_websocket: Optional[WebSocket] = None
        self.frontend_liveview_request_id: Optional[str] = None
        self.active_photometric_frontend_ws: Optional[WebSocket] = None
        self.active_photometric_request_id_frontend: Optional[str] = None
        self.active_photometric_set_folder_mw: Optional[str] = None
        self.active_photometric_captured_images_mw: List[str] = []
        self.active_photometric_light_sequence: List[str] = []
        self.active_photometric_current_light_idx: int = 0 
    def generate_pi_request_id(self) -> str: 
        self.pi_request_id_counter += 1
        return f"pi_mw_req_{self.pi_request_id_counter}_{int(time.time()*1000)}"
    async def connect_to_pi(self) -> bool: 
        if self.is_connected_to_pi: return True
        if self.is_connecting_to_pi: return False 
        async with self._reconnect_lock: 
            if self.is_connected_to_pi: return True 
            self.is_connecting_to_pi = True
            logger.info(f"Pi Client: Attempting to connect to Pi server at {self.pi_server_url}...")
            try:
                self.ws = await websockets.connect(self.pi_server_url, open_timeout=5) 
                self.is_connected_to_pi = True; self.is_connecting_to_pi = False
                logger.info("Pi Client: Successfully connected to Pi server.")
                if self._listen_task and not self._listen_task.done(): logger.warning("Pi Client: Listener task already exists.")
                else: self._listen_task = asyncio.create_task(self._listen_to_pi())
                return True
            except Exception as e:
                logger.error(f"Pi Client: Failed to connect to Pi server: {e}", exc_info=True)
                self.is_connected_to_pi = False; self.is_connecting_to_pi = False; self.ws = None
                await self.clear_frontend_liveview_state(); await self.clear_active_photometric_state() 
                return False
    async def _forward_liveview_frame(self, frame_data: Dict[str, Any]): 
        if self.frontend_liveview_websocket:
            try:
                mw_response = MiddlewareResponse(
                    action=ActionTypeMiddleware.LIVEVIEW_FRAME_MW, success=True,
                    data=LiveviewFrameMiddlewareData(frame=frame_data.get("frame", ""), mimetype=frame_data.get("mimetype", "image/jpeg")),
                    request_id=self.frontend_liveview_request_id 
                )
                await self.frontend_liveview_websocket.send_json(mw_response.dict())
                logger.debug("Pi Client: Forwarded liveview frame to frontend.")
            except Exception as e:
                logger.warning(f"Pi Client: Frontend liveview websocket disconnected/error: {e}. Stopping liveview forwarding.")
                await self.clear_frontend_liveview_state()
        else: logger.debug("Pi Client: Received liveview frame, but no frontend ws for liveview.")
    async def _handle_image_data_from_pi(self, image_data_payload_dict: Dict[str, Any]):
        if not self.active_photometric_frontend_ws or not self.active_photometric_set_folder_mw:
            logger.warning("Pi Client: Received image data from Pi, but no active photometric sequence context in middleware.")
            return
        try:
            image_data = ImageDataFromPiPayload(**image_data_payload_dict)
            img_bytes = base64.b64decode(image_data.image_b64)
            filename_base = image_data.light_name_for_set or os.path.splitext(image_data.original_filename)[0]
            filename_ext = os.path.splitext(image_data.original_filename)[1] or f".{image_data.mimetype.split('/')[-1]}" 
            if not filename_ext.startswith("."): filename_ext = "." + filename_ext
            safe_filename_base = re.sub(r'[^\w\-.]', '_', filename_base); final_filename = f"{safe_filename_base}{filename_ext}"
            save_path = os.path.join(self.active_photometric_set_folder_mw, final_filename)
            os.makedirs(os.path.dirname(save_path), exist_ok=True) 
            with open(save_path, "wb") as f: f.write(img_bytes)
            logger.info(f"Pi Client: Saved image from Pi to middleware at {save_path} for light {image_data.light_name_for_set}")
            relative_save_path = os.path.relpath(save_path, MW_CAPTURES_BASE_DIR)
            self.active_photometric_captured_images_mw.append(relative_save_path.replace("\\", "/"))
        except Exception as e: logger.error(f"Pi Client: Error handling image data from Pi: {e}", exc_info=True)
    async def _listen_to_pi(self): 
        logger.info("Pi Client: Listener task started.")
        try:
            if not self.ws: logger.error("Pi Client: Listener started without valid WebSocket."); return
            async for message_str in self.ws:
                try:
                    message_json = json.loads(message_str)
                    logger.debug(f"Pi Client: Received message from Pi: {message_json}")
                    pi_action = message_json.get("action")
                    pi_request_id = message_json.get("request_id")
                    if pi_action == ActionTypeMiddleware.IMAGE_DATA_FROM_PI.value: 
                        asyncio.create_task(self._handle_image_data_from_pi(message_json.get("data", {})))
                    elif pi_action == "liveview_frame": asyncio.create_task(self._forward_liveview_frame(message_json))
                    elif pi_request_id and pi_request_id in self.pi_command_responses:
                        future = self.pi_command_responses.pop(pi_request_id)
                        if not future.done(): future.set_result(message_json)
                        else: logger.warning(f"Pi Client: Received response for already handled request_id {pi_request_id}.")
                    else:
                        logger.info(f"Pi Client: Unsolicited message or no matching future: {message_json}")
                        for listener in self.pi_message_listeners: 
                            try: listener(message_json) 
                            except Exception as e: logger.error(f"Pi Client: Error in message listener: {e}", exc_info=True)
                except json.JSONDecodeError: logger.error(f"Pi Client: Failed to decode JSON from Pi: {message_str}")
                except Exception as e: logger.error(f"Pi Client: Error processing message from Pi: {e}", exc_info=True)
        except websockets.exceptions.ConnectionClosed: logger.warning(f"Pi Client: Connection to Pi server closed.")
        except Exception as e: logger.error(f"Pi Client: Listener task error: {e}", exc_info=True)
        finally: 
            logger.info("Pi Client: Listener task stopped.")
            current_is_connected = self.is_connected_to_pi 
            self.is_connected_to_pi = False; self.ws = None 
            await self.clear_frontend_liveview_state(); await self.clear_active_photometric_state()
            for req_id, future in list(self.pi_command_responses.items()): 
                if not future.done(): future.set_exception(ConnectionError("Connection to Pi server lost."))
                self.pi_command_responses.pop(req_id, None)
            if current_is_connected: 
                logger.info("Pi Client: Attempting to reconnect to Pi server in 5 seconds...")
                await asyncio.sleep(5); asyncio.create_task(self.connect_to_pi()) 
    async def send_to_pi(self, action: str, payload: Optional[Dict[str, Any]] = None, original_request_id: Optional[str] = None, timeout: int = 10) -> Any:
        if not self.is_connected_to_pi or not self.ws:
            logger.warning("Pi Client: Not connected. Attempting to connect...");
            if not await self.connect_to_pi(): return {"success": False, "error": "Failed to connect to Pi server.", "request_id": original_request_id, "action": action} 
        pi_request_id = self.generate_pi_request_id()
        message_to_pi = {"action": action, "payload": payload or {}, "request_id": pi_request_id}
        future = asyncio.get_event_loop().create_future()
        self.pi_command_responses[pi_request_id] = future
        try:
            logger.info(f"Pi Client: Sending to Pi: {message_to_pi}")
            await self.ws.send(json.dumps(message_to_pi)); response_from_pi = await asyncio.wait_for(future, timeout=timeout)
            logger.debug(f"Pi Client: Response for {pi_request_id}: {response_from_pi}"); return response_from_pi 
        except asyncio.TimeoutError:
            logger.error(f"Pi Client: Timeout for Pi action '{action}', pi_req_id '{pi_request_id}'."); self.pi_command_responses.pop(pi_request_id, None) 
            return {"success": False, "error": f"Timeout for Pi action '{action}'.", "request_id": original_request_id, "action": action}
        except websockets.exceptions.ConnectionClosed:
            logger.error(f"Pi Client: Connection closed for Pi action '{action}'."); self.pi_command_responses.pop(pi_request_id, None)
            return {"success": False, "error": f"Connection to Pi lost for '{action}'.", "request_id": original_request_id, "action": action}
        except Exception as e:
            logger.error(f"Pi Client: Error sending/awaiting for '{action}': {e}", exc_info=True); self.pi_command_responses.pop(pi_request_id, None)
            return {"success": False, "error": f"Error communicating with Pi for '{action}': {str(e)}", "request_id": original_request_id, "action": action}
    async def disconnect_from_pi(self): 
        logger.info("Pi Client: Disconnecting from Pi server..."); await self.clear_frontend_liveview_state(); await self.clear_active_photometric_state() 
        if self._listen_task and not self._listen_task.done(): 
            self._listen_task.cancel()
            try: await self._listen_task
            except asyncio.CancelledError: logger.info("Pi Client: Listener task cancelled.")
            except Exception as e: logger.error(f"Pi Client: Error during listener task cancellation: {e}", exc_info=True)
        if self.ws and self.is_connected_to_pi:
            try: await self.ws.close(); logger.info("Pi Client: WebSocket connection to Pi closed.")
            except websockets.exceptions.WebSocketException as e: logger.error(f"Pi Client: Error closing WebSocket to Pi: {e}")
        self.is_connected_to_pi = False; self.ws = None
        for req_id, future in list(self.pi_command_responses.items()):
            if not future.done(): future.set_exception(ConnectionError("Disconnected from Pi server by middleware."))
            self.pi_command_responses.pop(req_id, None)
        logger.info("Pi Client: Disconnected from Pi server and cleaned up.")
    async def clear_frontend_liveview_state(self): 
        logger.debug("Pi Client: Clearing frontend liveview state."); self.frontend_liveview_websocket = None; self.frontend_liveview_request_id = None
    async def clear_active_photometric_state(self):
        logger.debug("Pi Client: Clearing active photometric sequence state.")
        self.active_photometric_frontend_ws = None; self.active_photometric_request_id_frontend = None
        self.active_photometric_set_folder_mw = None; self.active_photometric_captured_images_mw = []
        self.active_photometric_light_sequence = []; self.active_photometric_current_light_idx = 0

PI_WS_URL = os.environ.get("PI_WEBSOCKET_URL", "ws://localhost:8000/ws")
pi_client = PiWebSocketClient(PI_WS_URL)

async def run_photometric_sequence_mw( # ... (same as before, no changes needed for this task)
    frontend_ws: WebSocket, payload: PhotometricSetMiddlewarePayload, request_id_frontend: str
):
    if pi_client.active_photometric_frontend_ws is not None:
        logger.warning("Middleware: Photometric sequence requested, but one is already active.")
        await frontend_ws.send_json(MiddlewareResponse(action=ActionTypeMiddleware.CAPTURE_PHOTOMETRIC_SET_MW, success=False, error="Another photometric sequence is already in progress.", request_id=request_id_frontend).dict())
        return
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    folder_name_part = f"{payload.set_name_prefix}_{timestamp}" if payload.set_name_prefix else f"photometric_set_{timestamp}"
    safe_set_folder_name_mw = re.sub(r'[^\w\-.]', '_', folder_name_part)
    full_set_folder_mw = os.path.join(MW_PHOTOMETRIC_SETS_BASE_DIR, safe_set_folder_name_mw)
    pi_client.active_photometric_frontend_ws = frontend_ws; pi_client.active_photometric_request_id_frontend = request_id_frontend
    pi_client.active_photometric_set_folder_mw = full_set_folder_mw; pi_client.active_photometric_captured_images_mw = []
    pi_client.active_photometric_light_sequence = payload.light_sequence; pi_client.active_photometric_current_light_idx = 0
    sequence_failed = False; final_error_message = "Photometric sequence completed with errors." 
    try:
        os.makedirs(full_set_folder_mw, exist_ok=True)
        logger.info(f"Middleware: Starting photometric sequence. Set folder on MW: {full_set_folder_mw}")
        await frontend_ws.send_json(MiddlewareResponse(action=ActionTypeMiddleware.PHOTOMETRIC_PROGRESS_MW, success=True, data=PhotometricProgressMiddlewareData(status="Sequence started.", set_folder_mw=safe_set_folder_name_mw).dict(), request_id=request_id_frontend).dict())
        for idx, light_name in enumerate(payload.light_sequence):
            pi_client.active_photometric_current_light_idx = idx; current_light_for_status = f"{light_name} ({idx+1}/{len(payload.light_sequence)})"
            await frontend_ws.send_json(MiddlewareResponse(action=ActionTypeMiddleware.PHOTOMETRIC_PROGRESS_MW, success=True, data=PhotometricProgressMiddlewareData(status=f"Preparing light: {current_light_for_status}", current_light=light_name, set_folder_mw=safe_set_folder_name_mw).dict(), request_id=request_id_frontend).dict())
            light_on_res = await pi_client.send_to_pi(action="set_light_state", payload={"light_name": light_name, "state": True})
            if not light_on_res.get("success"): final_error_message = f"Failed to turn ON light {light_name}: {light_on_res.get('error', 'Pi error')}"; sequence_failed = True; break
            await asyncio.sleep(PHOTOMETRIC_CAPTURE_DELAY) 
            logger.info(f"Middleware: Requesting image data capture from Pi for light {light_name}")
            pi_capture_response = await pi_client.send_to_pi(action="capture_image_data", payload={"light_name_for_set": light_name, "original_filename_suggestion": f"image_{idx+1:02d}_{light_name}.jpg"}, timeout=20)
            if not pi_capture_response.get("success"): final_error_message = f"Pi failed to capture/send image for light {light_name}: {pi_capture_response.get('error', 'Pi error')}"; sequence_failed = True
            else:
                img_data_dict = pi_capture_response.get("data")
                if not img_data_dict or not img_data_dict.get("image_b64"): final_error_message = f"Pi sent success for capture, but no image data for light {light_name}."; sequence_failed = True
                else:
                    try:
                        img_bytes = base64.b64decode(img_data_dict["image_b64"])
                        original_filename = img_data_dict.get("original_filename", f"image_{idx+1:02d}_{light_name}.jpg")
                        filename_base = os.path.splitext(original_filename)[0]; filename_ext = os.path.splitext(original_filename)[1] or f".{img_data_dict.get('mimetype', 'image/jpeg').split('/')[-1]}"
                        if not filename_ext.startswith("."): filename_ext = "." + filename_ext
                        safe_filename_base = re.sub(r'[^\w\-.]', '_', filename_base); final_filename = f"{safe_filename_base}{filename_ext}"
                        save_path = os.path.join(full_set_folder_mw, final_filename)
                        with open(save_path, "wb") as f: f.write(img_bytes)
                        relative_save_path = os.path.relpath(save_path, MW_CAPTURES_BASE_DIR)
                        pi_client.active_photometric_captured_images_mw.append(relative_save_path.replace("\\", "/"))
                        logger.info(f"Middleware: Saved image to {save_path} for light {light_name}")
                        await frontend_ws.send_json(MiddlewareResponse(action=ActionTypeMiddleware.PHOTOMETRIC_PROGRESS_MW, success=True, data=PhotometricProgressMiddlewareData(status=f"Image captured for light: {current_light_for_status}", current_light=light_name, set_folder_mw=safe_set_folder_name_mw).dict(), request_id=request_id_frontend).dict())
                    except Exception as e: final_error_message = f"Error saving image for light {light_name}: {str(e)}"; sequence_failed = True
            light_off_res = await pi_client.send_to_pi(action="set_light_state", payload={"light_name": light_name, "state": False})
            if not light_off_res.get("success") and not sequence_failed: logger.warning(f"Middleware: Failed to turn OFF light {light_name}: {light_off_res.get('error', 'Pi error')}")
            if sequence_failed: break 
            await asyncio.sleep(PHOTOMETRIC_CAPTURE_DELAY / 2) 
    except Exception as e: logger.error(f"Middleware: Unhandled error in photometric sequence: {e}", exc_info=True); final_error_message = f"Critical error: {str(e)}"; sequence_failed = True
    finally:
        for light_name_to_off in payload.light_sequence: await pi_client.send_to_pi(action="set_light_state", payload={"light_name": light_name_to_off, "state": False}, timeout=2)
        final_response_data = PhotometricSetMiddlewareResponseData(message="Photometric sequence completed." if not sequence_failed else final_error_message, set_folder_mw=safe_set_folder_name_mw, image_count=len(pi_client.active_photometric_captured_images_mw), images_captured_mw=pi_client.active_photometric_captured_images_mw)
        await frontend_ws.send_json(MiddlewareResponse(action=ActionTypeMiddleware.CAPTURE_PHOTOMETRIC_SET_MW, success=not sequence_failed, data=final_response_data.dict(), error=final_error_message if sequence_failed else None, request_id=request_id_frontend).dict())
        logger.info(f"Middleware: Photometric sequence for {safe_set_folder_name_mw} ended. Success: {not sequence_failed}"); await pi_client.clear_active_photometric_state()

# --- WebSocket Endpoint for Frontend ---
@app.websocket("/ws_middleware")
async def websocket_endpoint_middleware(websocket: WebSocket): # Modified
    await websocket.accept()
    client_host = websocket.client.host if websocket.client else "Unknown"; client_port = websocket.client.port if websocket.client else "N/A"
    logger.info(f"Frontend client connected: {client_host}:{client_port}")
    if not pi_client.is_connected_to_pi and not pi_client.is_connecting_to_pi: await pi_client.connect_to_pi()

    try:
        while True:
            data_str = await websocket.receive_text()
            request_id_frontend: Optional[str] = None; action_from_frontend_type: Optional[ActionTypeMiddleware] = None
            response_to_frontend: Optional[MiddlewareResponse] = None; json_data: Dict = {}
            try:
                json_data = json.loads(data_str); req = MiddlewareRequest(**json_data)
                request_id_frontend = req.request_id; action_from_frontend_type = req.action
                logger.info(f"Received from frontend: action='{req.action.value}', request_id='{request_id_frontend}'")
                payload_data = req.payload or {}
                
                actions_to_proxy_directly = [ # Actions proxied with minimal payload modification
                    ActionTypeMiddleware.LIST_CAMERAS_PI, ActionTypeMiddleware.SELECT_CAMERA_PI, ActionTypeMiddleware.DESELECT_CAMERA_PI,
                    ActionTypeMiddleware.GET_CONFIG_PI, ActionTypeMiddleware.SET_CONFIG_PI, ActionTypeMiddleware.CAPTURE_IMAGE_PI,
                    ActionTypeMiddleware.GET_LIGHT_STATES_PI, ActionTypeMiddleware.SET_LIGHT_STATE_PI,
                ]

                if req.action == ActionTypeMiddleware.PING_MIDDLEWARE: 
                    response_to_frontend = MiddlewareResponse(action=ActionTypeMiddleware.PONG_MIDDLEWARE, success=True, data={"message": "pong"}, request_id=request_id_frontend)
                elif req.action in actions_to_proxy_directly: 
                    pi_action_name_map = {
                        ActionTypeMiddleware.LIST_CAMERAS_PI: "get_cameras", ActionTypeMiddleware.SELECT_CAMERA_PI: "select_camera",
                        ActionTypeMiddleware.DESELECT_CAMERA_PI: "deselect_camera", ActionTypeMiddleware.GET_CONFIG_PI: "get_config",
                        ActionTypeMiddleware.SET_CONFIG_PI: "set_config", ActionTypeMiddleware.CAPTURE_IMAGE_PI: "capture_image",
                        ActionTypeMiddleware.GET_LIGHT_STATES_PI: "get_light_states", ActionTypeMiddleware.SET_LIGHT_STATE_PI: "set_light_state",
                    }
                    pi_action_name = pi_action_name_map[req.action]; current_payload = payload_data
                    if req.action == ActionTypeMiddleware.SELECT_CAMERA_PI: current_payload = SelectCameraPiPayload(**payload_data).dict(exclude_none=True)
                    elif req.action == ActionTypeMiddleware.GET_CONFIG_PI: current_payload = GetConfigPiPayload(**payload_data).dict(exclude_none=True)
                    elif req.action == ActionTypeMiddleware.SET_CONFIG_PI: current_payload = SetConfigPiPayload(**payload_data).dict()
                    elif req.action == ActionTypeMiddleware.CAPTURE_IMAGE_PI: current_payload = {"download": CaptureImagePiPayload(**payload_data).download_on_pi}
                    elif req.action == ActionTypeMiddleware.SET_LIGHT_STATE_PI: current_payload = SetLightStatePiPayload(**payload_data).dict()
                    pi_response = await pi_client.send_to_pi(action=pi_action_name, payload=current_payload, original_request_id=request_id_frontend)
                    response_to_frontend = MiddlewareResponse(action=req.action, success=pi_response.get("success", False), data=pi_response.get("data"), error=pi_response.get("error"), request_id=request_id_frontend)
                elif req.action == ActionTypeMiddleware.START_LIVEVIEW_PI: 
                    if pi_client.frontend_liveview_websocket is not None: response_to_frontend = MiddlewareResponse(action=req.action, success=False, error="Liveview already active for another client.", request_id=request_id_frontend)
                    else:
                        pi_response = await pi_client.send_to_pi(action="start_liveview", original_request_id=request_id_frontend)
                        if pi_response.get("success"):
                            pi_client.frontend_liveview_websocket = websocket; pi_client.frontend_liveview_request_id = request_id_frontend
                            response_to_frontend = MiddlewareResponse(action=req.action, success=True, data=pi_response.get("data", {"message": "Liveview started on Pi."}), request_id=request_id_frontend)
                        else: response_to_frontend = MiddlewareResponse(action=req.action, success=False, error=pi_response.get("error", "Pi failed to start liveview."), request_id=request_id_frontend)
                elif req.action == ActionTypeMiddleware.STOP_LIVEVIEW_PI: 
                    pi_response = await pi_client.send_to_pi(action="stop_liveview", original_request_id=request_id_frontend)
                    await pi_client.clear_frontend_liveview_state(); response_to_frontend = MiddlewareResponse(action=req.action, success=pi_response.get("success", True), data=pi_response.get("data", {"message": "Liveview stop requested."}), error=pi_response.get("error"), request_id=request_id_frontend)
                elif req.action == ActionTypeMiddleware.CAPTURE_PHOTOMETRIC_SET_MW:
                    try:
                        photometric_payload = PhotometricSetMiddlewarePayload(**payload_data)
                        if not photometric_payload.light_sequence: response_to_frontend = MiddlewareResponse(action=req.action, success=False, error="Light sequence cannot be empty.", request_id=request_id_frontend)
                        else: asyncio.create_task(run_photometric_sequence_mw(websocket, photometric_payload, request_id_frontend)); response_to_frontend = None 
                    except ValidationError as e: response_to_frontend = MiddlewareResponse(action=req.action, success=False, error=f"Invalid payload for photometric set: {e.errors()}", request_id=request_id_frontend)
                
                # New Middleware Image/Set Management Handlers
                elif req.action == ActionTypeMiddleware.LIST_IMAGE_SETS_MW:
                    set_infos: List[ImageSetInfoMiddleware] = []
                    if os.path.exists(MW_PHOTOMETRIC_SETS_BASE_DIR) and os.path.isdir(MW_PHOTOMETRIC_SETS_BASE_DIR):
                        for item_name in os.listdir(MW_PHOTOMETRIC_SETS_BASE_DIR):
                            if os.path.isdir(os.path.join(MW_PHOTOMETRIC_SETS_BASE_DIR, item_name)) and _is_path_safe_mw(MW_PHOTOMETRIC_SETS_BASE_DIR, item_name):
                                set_infos.append(ImageSetInfoMiddleware(name=item_name))
                        set_infos = sorted(set_infos, key=lambda x: x.name, reverse=True)
                    response_to_frontend = MiddlewareResponse(action=req.action, success=True, data=[s.dict() for s in set_infos], request_id=request_id_frontend)

                elif req.action == ActionTypeMiddleware.GET_IMAGE_SET_CONTENTS_MW:
                    contents_payload = ImageSetContentsMiddlewarePayload(**payload_data)
                    if not _is_path_safe_mw(MW_PHOTOMETRIC_SETS_BASE_DIR, contents_payload.set_name):
                        response_to_frontend = MiddlewareResponse(action=req.action, success=False, error="Invalid or unsafe set name.", request_id=request_id_frontend)
                    else:
                        set_dir_path = os.path.join(MW_PHOTOMETRIC_SETS_BASE_DIR, contents_payload.set_name)
                        image_files: List[ImageFileDetailsMiddleware] = []
                        if os.path.exists(set_dir_path) and os.path.isdir(set_dir_path):
                            allowed_ext = ('.jpg', '.jpeg', '.png', '.cr2', '.nef', '.arw')
                            for filename in os.listdir(set_dir_path):
                                if filename.lower().endswith(allowed_ext) and os.path.isfile(os.path.join(set_dir_path, filename)):
                                    # Path relative to MW_CAPTURES_BASE_DIR for client use
                                    relative_path = os.path.relpath(os.path.join(set_dir_path, filename), MW_CAPTURES_BASE_DIR)
                                    image_files.append(ImageFileDetailsMiddleware(filename=filename, path_mw=relative_path.replace("\\", "/")))
                            image_files = sorted(image_files, key=lambda x: x.filename)
                        response_to_frontend = MiddlewareResponse(action=req.action, success=True, data=[img.dict() for img in image_files], request_id=request_id_frontend)
                
                elif req.action == ActionTypeMiddleware.DELETE_IMAGE_SET_MW:
                    delete_payload = DeleteImageSetMiddlewarePayload(**payload_data)
                    if not _is_path_safe_mw(MW_PHOTOMETRIC_SETS_BASE_DIR, delete_payload.set_name):
                        response_to_frontend = MiddlewareResponse(action=req.action, success=False, error="Invalid or unsafe set name for deletion.", request_id=request_id_frontend)
                    else:
                        set_dir_path = os.path.join(MW_PHOTOMETRIC_SETS_BASE_DIR, delete_payload.set_name)
                        if os.path.exists(set_dir_path) and os.path.isdir(set_dir_path):
                            try: shutil.rmtree(set_dir_path); logger.info(f"Deleted image set: {set_dir_path}"); success = True
                            except Exception as e: logger.error(f"Error deleting set {set_dir_path}: {e}"); success = False; error_msg = str(e)
                        else: success = False; error_msg = "Set not found."
                        response_to_frontend = MiddlewareResponse(action=req.action, success=success, data={"message": f"Set '{delete_payload.set_name}' {'deleted.' if success else 'not deleted.'}"} , error=error_msg if not success else None, request_id=request_id_frontend)

                elif req.action == ActionTypeMiddleware.GET_IMAGE_DATA_MW:
                    image_data_payload = GetImageDataMiddlewarePayload(**payload_data)
                    # image_path_mw is relative to MW_CAPTURES_BASE_DIR
                    if not _is_path_safe_mw(MW_CAPTURES_BASE_DIR, image_data_payload.image_path_mw):
                        response_to_frontend = MiddlewareResponse(action=req.action, success=False, error="Invalid or unsafe image path.", request_id=request_id_frontend)
                    else:
                        full_image_path = os.path.join(MW_CAPTURES_BASE_DIR, image_data_payload.image_path_mw)
                        if os.path.exists(full_image_path) and os.path.isfile(full_image_path):
                            try:
                                with open(full_image_path, "rb") as f: img_bytes = f.read()
                                b64_data = base64.b64encode(img_bytes).decode('utf-8')
                                mimetype, _ = mimetypes.guess_type(full_image_path); mimetype = mimetype or 'application/octet-stream'
                                img_data_resp = ImageDataMiddlewareResponse(filename=os.path.basename(full_image_path), image_b64=b64_data, mimetype=mimetype)
                                response_to_frontend = MiddlewareResponse(action=req.action, success=True, data=img_data_resp.dict(), request_id=request_id_frontend)
                            except Exception as e: logger.error(f"Error reading image {full_image_path}: {e}"); response_to_frontend = MiddlewareResponse(action=req.action, success=False, error=str(e), request_id=request_id_frontend)
                        else: response_to_frontend = MiddlewareResponse(action=req.action, success=False, error="Image not found.", request_id=request_id_frontend)

                else: 
                    logger.warning(f"Unknown action received from frontend: {req.action}")
                    response_to_frontend = MiddlewareResponse(action=req.action, success=False, error="Unknown action requested by frontend.", request_id=request_id_frontend)
                
                if response_to_frontend: await websocket.send_json(response_to_frontend.dict())

            except ValidationError as e: 
                logger.error(f"Invalid payload for action {action_from_frontend_type.value if action_from_frontend_type else 'unknown'}: {e.errors()}")
                await websocket.send_json(MiddlewareResponse(action=action_from_frontend_type if action_from_frontend_type else ActionTypeMiddleware.PING_MIDDLEWARE, success=False, error=f"Invalid payload: {e.errors()}", request_id=request_id_frontend).dict())
            except json.JSONDecodeError: logger.error("Invalid JSON from frontend."); await websocket.send_json({"success": False, "error": "Invalid JSON format."}) 
            except Exception as e: 
                logger.error(f"Error processing message from frontend: {e}", exc_info=True)
                action_val = action_from_frontend_type.value if action_from_frontend_type else (json_data.get("action", "unknown_action") if json_data else "unknown_action")
                await websocket.send_json(MiddlewareResponse(action=action_val, success=False, error=str(e), request_id=request_id_frontend).dict()) # type: ignore
    except WebSocketDisconnect: 
        logger.info(f"Frontend client disconnected: {client_host}:{client_port}")
        if pi_client.frontend_liveview_websocket == websocket:
            logger.info(f"Frontend client (liveview) {client_host}:{client_port} disconnected. Stopping liveview on Pi.")
            asyncio.create_task(pi_client.send_to_pi(action="stop_liveview", original_request_id="fd_disconnect_lv_stop"))
            await pi_client.clear_frontend_liveview_state()
        if pi_client.active_photometric_frontend_ws == websocket:
            logger.info(f"Frontend client (photometric) {client_host}:{client_port} disconnected. Clearing active sequence state.")
            await pi_client.clear_active_photometric_state()
    except Exception as e: logger.error(f"Unexpected error in middleware WebSocket endpoint for {client_host}:{client_port}: {e}", exc_info=True)
    finally: logger.info(f"Cleaning up connection for frontend client {client_host}:{client_port}")

@app.on_event("startup")
async def startup_event(): # ... (same)
    logger.info("Middleware server starting up...")
    os.makedirs(MW_CAPTURES_BASE_DIR, exist_ok=True); os.makedirs(MW_PHOTOMETRIC_SETS_BASE_DIR, exist_ok=True)
    logger.info(f"Middleware storage: Captures '{MW_CAPTURES_BASE_DIR}', Photometric Sets '{MW_PHOTOMETRIC_SETS_BASE_DIR}'")
    await pi_client.connect_to_pi() 
@app.on_event("shutdown")
async def shutdown_event(): # ... (same)
    logger.info("Middleware server shutting down..."); await pi_client.disconnect_from_pi(); logger.info("Middleware server shutdown complete.")
if __name__ == "__main__": # ... (same)
    uvicorn.run(app, host="0.0.0.0", port=8001, log_level=LOG_LEVEL.lower())
```
