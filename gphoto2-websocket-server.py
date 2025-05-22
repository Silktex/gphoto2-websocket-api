#!/usr/bin/env python3

import asyncio
import base64 
import json
import logging
import os
import re
import time
import shutil # For delete_image_set
import mimetypes # For get_image_data

from enum import Enum
from typing import Any, Dict, List, Optional, Tuple, Union

# Attempt to import RPi.GPIO and set a flag
try:
    import RPi.GPIO as GPIO
    gpio_imported_successfully = True
except (ImportError, RuntimeError):
    gpio_imported_successfully = False
    GPIO = None 

import gphoto2 as gp
import uvicorn
from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

# Configure logging
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()
SETTINGS_REFRESH_INTERVAL = int(os.environ.get("SETTINGS_REFRESH_INTERVAL", "10")) 
LIVEVIEW_FRAME_INTERVAL = float(os.environ.get("LIVEVIEW_FRAME_INTERVAL", "0.05")) 
PHOTOMETRIC_CAPTURE_DELAY = float(os.environ.get("PHOTOMETRIC_CAPTURE_DELAY", "0.5")) 

# Base directory for captures and photometric sets
CAPTURES_BASE_DIR = "captures" # General captures
PHOTOMETRIC_SETS_BASE_DIR = os.path.join(CAPTURES_BASE_DIR, "photometric_sets")


logging.basicConfig(
    level=LOG_LEVEL, format="%(asctime)s - %(levelname)s - %(name)s - %(message)s"
)
logger = logging.getLogger(__name__)

# GPIO Pin mapping (BCM mode)
LIGHT_PINS: Dict[str, int] = {
    "lights_top": 24, "light_front": 4, "light_front_left": 18, "light_left": 27,
    "light_rear_left": 22, "light_rear": 3, "light_rear_right": 17, "light_right": 23,
    "light_front_right": 2
}

# --- Pydantic Models ---
class CameraInfo(BaseModel): model: str; port: str
class ConfigDetails(BaseModel): name: str; label: str; value: Union[str, int, float, bool]; type: str; readonly: bool; options: Optional[List[str]] = None
class CaptureResponse(BaseModel): message: str; file_path: Optional[str] = None
class LightStatePayload(BaseModel): light_name: str; state: bool
class LightStatesInfo(BaseModel): states: Dict[str, bool]; gpio_available: bool
class SetLightStateResponseData(BaseModel): light_name: str; new_state: bool; gpio_available: bool; message: str
class LiveviewFrameMessage(BaseModel): action: str = "liveview_frame"; frame: str; mimetype: str = "image/jpeg"

# Models for Photometric Stereo
class PhotometricSetPayload(BaseModel): light_sequence: List[str]; set_name_prefix: Optional[str] = None
class PhotometricProgressData(BaseModel): status: str; current_light: Optional[str] = None; set_folder: str; error_detail: Optional[str] = None
class PhotometricSetResponseData(BaseModel): message: str; set_folder: str; image_count: Optional[int] = None; images_captured: Optional[List[str]] = None

# Models for Image/Set Management
class ImageSetContentsPayload(BaseModel): set_name: str
class DeleteImageSetPayload(BaseModel): set_name: str
class GetImageDataPayload(BaseModel): image_path: str # Relative to server root, e.g., "captures/photometric_sets/set_name/image.jpg"

class ImageSetInfo(BaseModel): name: str # Directory name of the set
class ImageFileDetails(BaseModel): filename: str; path: str # path is relative to server root
class ImageDataResponse(BaseModel): filename: str; image_b64: str; mimetype: str


class ActionType(str, Enum):
    GET_CAMERAS = "get_cameras"; SELECT_CAMERA = "select_camera"; GET_CONFIG = "get_config"
    SET_CONFIG = "set_config"; CAPTURE_IMAGE = "capture_image"; GET_PREVIEW = "get_preview" 
    DESELECT_CAMERA = "deselect_camera"; SET_LIGHT_STATE = "set_light_state"; GET_LIGHT_STATES = "get_light_states"
    START_LIVEVIEW = "start_liveview"; STOP_LIVEVIEW = "stop_liveview"
    CAPTURE_PHOTOMETRIC_SET = "capture_photometric_set"; PHOTOMETRIC_PROGRESS = "photometric_progress"
    # New actions for Image/Set Management
    LIST_IMAGE_SETS = "list_image_sets"
    GET_IMAGE_SET_CONTENTS = "get_image_set_contents"
    DELETE_IMAGE_SET = "delete_image_set"
    GET_IMAGE_DATA = "get_image_data"

class WebSocketRequest(BaseModel): action: ActionType; payload: Optional[Dict[str, Any]] = None; request_id: Optional[str] = None 
class WebSocketResponse(BaseModel): action: ActionType; success: bool; data: Optional[Any] = None; error: Optional[str] = None; request_id: Optional[str] = None


# --- Light Controller Class ---
class LightController: # ... (same as before)
    def __init__(self):
        self.gpio_available = gpio_imported_successfully
        self.light_states: Dict[str, bool] = {name: False for name in LIGHT_PINS.keys()}
        if self.gpio_available and GPIO:
            logger.info("RPi.GPIO imported. Initializing GPIO for lights.")
            try:
                GPIO.setmode(GPIO.BCM); GPIO.setwarnings(False)
                for name, pin in LIGHT_PINS.items(): GPIO.setup(pin, GPIO.OUT); GPIO.output(pin, GPIO.LOW)
                logger.info("GPIO pins for lights initialized.")
            except Exception as e: logger.error(f"GPIO init error: {e}. Disabling GPIO."); self.gpio_available = False
        else: logger.warning("RPi.GPIO not found/failed. Light control stubbed.")
    def set_light_state(self, light_name: str, state: bool) -> Tuple[bool, str]:
        if light_name not in LIGHT_PINS: return False, f"Invalid light name: {light_name}."
        self.light_states[light_name] = state
        msg_action = "ON" if state else "OFF"
        if self.gpio_available and GPIO:
            pin = LIGHT_PINS[light_name]
            try: GPIO.output(pin, GPIO.HIGH if state else GPIO.LOW); logger.info(f"Light '{light_name}' (pin {pin}) to {msg_action}."); return True, f"Light '{light_name}' turned {msg_action}."
            except Exception as e: logger.error(f"GPIO error for light '{light_name}': {e}"); return False, f"GPIO error for '{light_name}'."
        logger.info(f"Mock: Light '{light_name}' to {msg_action} (GPIO not available)."); return True, f"Mock: Light '{light_name}' state {msg_action}."
    def get_light_states(self) -> Dict[str, bool]: return self.light_states
    def get_gpio_availability(self) -> bool: return self.gpio_available
    def cleanup(self):
        if self.gpio_available and GPIO: logger.info("Cleaning up GPIO."); GPIO.cleanup()
        else: logger.info("No GPIO cleanup needed.")

# --- GPhoto2 API Wrapper ---
class GPhoto2API: # ... (previous methods mostly unchanged, new methods added)
    _instance = None
    def __new__(cls, *args, **kwargs): # ... (same)
        if not cls._instance: cls._instance = super(GPhoto2API, cls).__new__(cls)
        return cls._instance
    def __init__(self): # ... (same, ensure new members are initialized if any)
        if not hasattr(self, "initialized"):
            self.camera: Optional[gp.Camera] = None; self.context: gp.Context = gp.Context()
            self.available_cameras: List[CameraInfo] = []; self.selected_camera_info: Optional[CameraInfo] = None
            self.preview_task = None; self.preview_clients: List[WebSocket] = [] 
            self.settings_cache: Dict[str, ConfigDetails] = {}; self.cache_refresh_task = None
            self.light_controller = LightController()
            self.liveview_active: bool = False; self.current_liveview_websocket: Optional[WebSocket] = None
            self.initialized = True
            # Ensure base directories exist at startup
            os.makedirs(CAPTURES_BASE_DIR, exist_ok=True)
            os.makedirs(PHOTOMETRIC_SETS_BASE_DIR, exist_ok=True)
            logger.info(f"GPhoto2API init. Captures dir: '{CAPTURES_BASE_DIR}', Photometric sets dir: '{PHOTOMETRIC_SETS_BASE_DIR}'.")
            logger.info(f"Settings refresh: {SETTINGS_REFRESH_INTERVAL}s. GPIO: {self.light_controller.get_gpio_availability()}. Liveview FPS: ~{1/LIVEVIEW_FRAME_INTERVAL:.0f}")

    # --- Path Validation Helper ---
    def _is_path_safe(self, base_dir: str, user_provided_path_segment: str) -> bool:
        """Validates if a path segment is safe and within the base directory."""
        # Prevent path traversal by ensuring the segment is just a name, not a path itself
        if ".." in user_provided_path_segment or "/" in user_provided_path_segment or "\\" in user_provided_path_segment:
            logger.warning(f"Attempted path traversal or invalid characters in segment: '{user_provided_path_segment}'")
            return False
        
        # Construct the full path and normalize it
        full_path = os.path.normpath(os.path.join(base_dir, user_provided_path_segment))
        
        # Check if the normalized full path is still within the intended base directory
        if not os.path.abspath(full_path).startswith(os.path.abspath(base_dir)):
            logger.warning(f"Path validation failed: '{full_path}' is outside of base '{base_dir}'")
            return False
        return True

    async def _start_periodic_cache_refresh(self): # ... (same)
        if self.cache_refresh_task and not self.cache_refresh_task.done(): logger.debug("Cache refresh task already running."); return
        if self.camera and self.selected_camera_info:
            logger.info(f"Starting periodic settings cache refresh for {self.selected_camera_info.model}.")
            self.cache_refresh_task = asyncio.create_task(self._periodic_cache_refresh_loop())
        else: logger.warning("Cannot start periodic cache refresh: No camera selected.")
    async def _stop_periodic_cache_refresh(self): # ... (same)
        if self.cache_refresh_task and not self.cache_refresh_task.done():
            logger.info("Stopping periodic settings cache refresh task.")
            self.cache_refresh_task.cancel()
            try: await self.cache_refresh_task
            except asyncio.CancelledError: logger.info("Periodic settings cache refresh task cancelled successfully.")
            except Exception as e: logger.error(f"Error during cache refresh task cancellation: {e}", exc_info=True)
        self.cache_refresh_task = None
    async def _periodic_cache_refresh_loop(self): # ... (same)
        if not self.selected_camera_info: logger.error("Cache refresh loop started without selected camera."); return
        logger.info(f"Periodic cache refresh loop started for {self.selected_camera_info.model}.")
        try:
            while True:
                if not self.camera or not self.selected_camera_info: logger.info("Camera deselected. Stopping cache refresh."); break
                logger.debug(f"Periodic refresh: Updating settings for {self.selected_camera_info.model}.")
                await self.refresh_full_settings_cache()
                await asyncio.sleep(SETTINGS_REFRESH_INTERVAL)
        except asyncio.CancelledError: logger.info(f"Cache refresh loop for {self.selected_camera_info.model if self.selected_camera_info else 'N/A'} cancelled.")
        except Exception as e: logger.error(f"Error in cache refresh loop for {self.selected_camera_info.model if self.selected_camera_info else 'N/A'}: {e}", exc_info=True)
        finally: logger.info(f"Cache refresh loop for {self.selected_camera_info.model if self.selected_camera_info else 'N/A'} ended.")
    async def list_cameras(self) -> List[CameraInfo]: # ... (same)
        logger.info("Detecting cameras...")
        try:
            try: gp.check_result(gp.gp_camera_list_free(gp.check_result(gp.gp_camera_list_new(self.context))))
            except gp.GPhoto2Error as e: logger.warning(f"Minor issue pre-autodetect cleanup: {e}.")
            cameras_detected = gp.check_result(gp.gp_camera_autodetect())
            self.available_cameras = [CameraInfo(model=name, port=port) for name, port in cameras_detected]
            if not self.available_cameras: logger.info("No cameras detected.")
            else: logger.info(f"Found cameras: {self.available_cameras}")
            return self.available_cameras
        except gp.GPhoto2Error as ex: logger.error(f"Error detecting cameras: {ex}"); return []
    async def select_camera(self, model: Optional[str] = None, port: Optional[str] = None) -> bool: # ... (same)
        logger.info(f"Attempting to select camera: model={model}, port={port}")
        if self.camera: logger.info("Camera already selected. Deselecting first."); await self.deselect_camera()
        await self.list_cameras()
        if not self.available_cameras: logger.warning("No cameras available."); return False
        cam_to_select = None
        if model and port: cam_to_select = next((c for c in self.available_cameras if c.model == model and c.port == port), None)
        elif model: cam_to_select = next((c for c in self.available_cameras if c.model == model), None)
        elif port: cam_to_select = next((c for c in self.available_cameras if c.port == port), None)
        elif self.available_cameras: cam_to_select = self.available_cameras[0]
        if not cam_to_select: logger.error(f"Camera not found: model='{model}', port='{port}'."); return False
        port_info_list, driver_list = None, None
        try:
            self.camera = gp.Camera()
            port_info_list = gp.check_result(gp.gp_port_info_list_new(self.context)); gp.check_result(gp.gp_port_info_list_load(port_info_list))
            pi = gp.check_result(gp.gp_port_info_list_get_info(port_info_list, gp.check_result(gp.gp_port_info_list_lookup_path(port_info_list, cam_to_select.port))))
            gp.check_result(self.camera.set_port_info(pi))
            driver_list = gp.check_result(gp.gp_abilities_list_new(self.context)); gp.check_result(gp.gp_abilities_list_load(driver_list, self.context))
            camera_abilities = gp.check_result(gp.gp_abilities_list_get_abilities(driver_list, gp.check_result(gp.gp_abilities_list_lookup_model(driver_list, cam_to_select.model))))
            gp.check_result(self.camera.set_abilities(camera_abilities))
            logger.info(f"Initializing camera: {cam_to_select.model} on port {cam_to_select.port}")
            self.camera.init(self.context); self.selected_camera_info = cam_to_select
            logger.info(f"Selected camera: {self.selected_camera_info.model} on port {self.selected_camera_info.port}")
            await self._populate_settings_cache(); await self._start_periodic_cache_refresh()
            return True
        except gp.GPhoto2Error as ex:
            logger.error(f"Error selecting/initializing camera {cam_to_select.model} ({cam_to_select.port}): {ex}")
            if self.camera: try: self.camera.exit(self.context) 
            except gp.GPhoto2Error: pass
            self.camera, self.selected_camera_info = None, None; self.settings_cache.clear(); await self._stop_periodic_cache_refresh(); return False
        finally:
            if port_info_list: gp.check_result(gp.gp_port_info_list_free(port_info_list))
            if driver_list: gp.check_result(gp.gp_abilities_list_free(driver_list))
    async def deselect_camera(self) -> bool: # ... (same, ensures liveview is stopped)
        await self.stop_liveview(); await self._stop_periodic_cache_refresh()
        if self.camera and self.selected_camera_info:
            logger.info(f"Deselecting camera: {self.selected_camera_info.model}")
            if self.preview_task: self.preview_task.cancel(); await asyncio.gather(self.preview_task, return_exceptions=True)
            self.preview_task, self.preview_clients = None, []
            try: self.camera.exit(self.context); logger.info(f"Camera {self.selected_camera_info.model} deselected.")
            except gp.GPhoto2Error as ex: logger.error(f"Error exiting camera {self.selected_camera_info.model}: {ex}")
            finally: self.camera, self.selected_camera_info = None, None; self.settings_cache.clear(); return True
        logger.info("No camera selected to deselect."); self.settings_cache.clear(); return False
    def _get_widget_by_name_recursive(self, widget_name: str, current_widget: gp.CameraWidget) -> Optional[gp.CameraWidget]: # ... (same)
        try:
            if current_widget.get_name() == widget_name: return current_widget
        except gp.GPhoto2Error: pass
        if current_widget.get_type() in [gp.GP_WIDGET_WINDOW, gp.GP_WIDGET_SECTION]:
            for i in range(current_widget.count_children()):
                try:
                    child = current_widget.get_child(i)
                    if (found := self._get_widget_by_name_recursive(widget_name, child)): return found # type: ignore
                except gp.GPhoto2Error as e: logger.debug(f"Error accessing child widget: {e}")
        return None
    def _extract_config_details(self, widget: gp.CameraWidget) -> Optional[ConfigDetails]: # ... (same)
        try:
            widget_name = widget.get_name(); widget_type_id = widget.get_type()
            if not widget_name or widget_type_id in [gp.GP_WIDGET_WINDOW, gp.GP_WIDGET_SECTION, gp.GP_WIDGET_BUTTON]: return None
            label, value, readonly = widget.get_label(), widget.get_value(), widget.get_readonly()
            type_map = {gp.GP_WIDGET_TEXT: "text", gp.GP_WIDGET_RANGE: "range", gp.GP_WIDGET_TOGGLE: "toggle", gp.GP_WIDGET_RADIO: "radio", gp.GP_WIDGET_MENU: "menu", gp.GP_WIDGET_DATE: "date"}
            type_str = type_map.get(widget_type_id, f"unknown (ID: {widget_type_id})")
            options = [str(c) for c in widget.get_choices()] if widget_type_id in [gp.GP_WIDGET_RADIO, gp.GP_WIDGET_MENU] else None
            if widget_type_id == gp.GP_WIDGET_TOGGLE: value = bool(value)
            elif widget_type_id == gp.GP_WIDGET_RANGE and isinstance(value, (int, float)): 
                try: value = float(value)
                except ValueError: pass 
            return ConfigDetails(name=widget_name, label=label, value=value, type=type_str, readonly=readonly, options=options)
        except gp.GPhoto2Error as e: logger.warning(f"Error extracting details for widget '{getattr(widget, 'get_name', lambda: 'N/A')()}': {e}. Skipping."); return None
    def _get_all_configs_recursive(self, widget: gp.CameraWidget, current_config_map: Dict[str, ConfigDetails]): # ... (same)
        if not widget: return
        if details := self._extract_config_details(widget): # type: ignore
            if details.name in current_config_map: logger.warning(f"Duplicate config name '{details.name}'. Overwriting.")
            current_config_map[details.name] = details
        if widget.get_type() in [gp.GP_WIDGET_WINDOW, gp.GP_WIDGET_SECTION]:
            for i in range(widget.count_children()):
                try: self._get_all_configs_recursive(widget.get_child(i), current_config_map)
                except gp.GPhoto2Error as e: logger.error(f"Error getting child widget from '{getattr(widget, 'get_name', lambda: 'N/A')()}': {e}")
    async def _populate_settings_cache(self, is_periodic_refresh: bool = False): # ... (same)
        if not self.camera or not self.selected_camera_info:
            if not is_periodic_refresh: logger.warning("No camera selected. Cannot populate cache.")
            self.settings_cache.clear(); return
        log_prefix = "Periodic refresh: " if is_periodic_refresh else ""
        logger.info(f"{log_prefix}Populating settings cache for {self.selected_camera_info.model}...")
        new_cache: Dict[str, ConfigDetails] = {}
        try:
            self._get_all_configs_recursive(self.camera.get_config(self.context), new_cache)
            if is_periodic_refresh and self.settings_cache != new_cache: logger.info(f"{log_prefix}Cache updated with {len(new_cache)} items. Changes detected.")
            elif is_periodic_refresh: logger.debug(f"{log_prefix}Cache checked, no changes.")
            self.settings_cache = new_cache
            if not is_periodic_refresh: logger.info(f"Cache populated with {len(self.settings_cache)} items.")
        except gp.GPhoto2Error as ex:
            logger.error(f"{log_prefix}Error populating cache for {self.selected_camera_info.model}: {ex}")
            if not is_periodic_refresh: self.settings_cache.clear()
        except Exception as e:
            logger.error(f"{log_prefix}Unexpected error populating cache: {e}", exc_info=True)
            if not is_periodic_refresh: self.settings_cache.clear()
    async def refresh_full_settings_cache(self): # ... (same)
        logger.debug(f"refresh_full_settings_cache called for {self.selected_camera_info.model if self.selected_camera_info else 'N/A'}")
        await self._populate_settings_cache(is_periodic_refresh=True)
    async def get_config(self, config_name: Optional[str] = None) -> Union[List[ConfigDetails], ConfigDetails, None]: # ... (same)
        if not self.camera or not self.selected_camera_info: logger.warning("No camera selected."); return None
        if not self.settings_cache: logger.info("Cache empty. Populating before get_config."); await self._populate_settings_cache(False)
        if not self.settings_cache and not config_name: logger.warning("Failed to populate cache. Cannot get all configs."); return [] 
        if not self.settings_cache and config_name : logger.warning(f"Failed to populate cache. Cannot get config {config_name}"); return None
        if config_name:
            if config_name in self.settings_cache: return self.settings_cache[config_name]
            logger.warning(f"Config '{config_name}' not in cache. Refreshing once."); await self._populate_settings_cache(False)
            if config_name in self.settings_cache: return self.settings_cache[config_name]
            logger.error(f"Config '{config_name}' not found after refresh."); return None
        return list(self.settings_cache.values())
    async def set_config(self, config_name: str, value: Any) -> bool: # ... (same as before, ensure full type conv. logic)
        if not self.camera or not self.selected_camera_info: logger.warning("No camera selected."); return False
        logger.info(f"Setting config '{config_name}' to '{value}' on {self.selected_camera_info.model}")
        try:
            config_tree_root = self.camera.get_config(self.context)
            widget = self._get_widget_by_name_recursive(config_name, config_tree_root)
            if not widget:
                logger.error(f"Widget '{config_name}' not found. Refreshing cache once."); await self._populate_settings_cache(False)
                if config_name not in self.settings_cache: logger.error(f"'{config_name}' still not in cache."); return False
                config_tree_root = self.camera.get_config(self.context); widget = self._get_widget_by_name_recursive(config_name, config_tree_root)
                if not widget: logger.error(f"'{config_name}' in cache but widget search failed again."); return False
            parsed_value = value; widget_type = widget.get_type() 
            if widget_type == gp.GP_WIDGET_RADIO or widget_type == gp.GP_WIDGET_MENU:
                choices = list(widget.get_choices()); parsed_value = str(value)
                if str(value) not in choices:
                    try: value_idx = int(value); parsed_value = choices[value_idx] if 0 <= value_idx < len(choices) else (_ for _ in ()).throw(ValueError("Index out of bounds")) # type: ignore
                    except: logger.error(f"Invalid choice/index '{value}' for '{config_name}'. Avail: {choices}"); return False
            elif widget_type == gp.GP_WIDGET_TOGGLE:
                if isinstance(value, str): parsed_value = 1 if value.lower() in ["on", "true", "1"] else (0 if value.lower() in ["off", "false", "0"] else (_ for _ in ()).throw(ValueError("Invalid toggle string"))) # type: ignore
                elif isinstance(value, bool): parsed_value = int(value)
                elif not (isinstance(value, int) and value in [0,1]): logger.error(f"Invalid toggle type '{type(value)}'"); return False
            elif widget_type == gp.GP_WIDGET_RANGE: 
                try: parsed_value = float(value)
                except: logger.error(f"Invalid range value '{value}'"); return False
            elif widget_type == gp.GP_WIDGET_TEXT: parsed_value = str(value)
            elif widget_type == gp.GP_WIDGET_DATE:
                try: parsed_value = int(value)
                except: logger.error(f"Invalid date value '{value}'"); return False
            if widget.get_readonly(): logger.error(f"Config '{config_name}' is read-only."); return False
            widget.set_value(parsed_value); self.camera.set_config(config_tree_root, self.context)
            logger.info(f"Successfully set '{config_name}' to '{parsed_value}'.")
            if updated_details := self._extract_config_details(widget): self.settings_cache[config_name] = updated_details # type: ignore
            else: logger.warning(f"Could not re-extract details for '{config_name}' after set. Refreshing all."); await self._populate_settings_cache(False)
            return True
        except gp.GPhoto2Error as ex: logger.error(f"GPhoto2Error setting '{config_name}': {ex}"); await self._populate_settings_cache(False); return False
        except Exception as e: logger.error(f"Error setting '{config_name}': {e}", exc_info=True); await self._populate_settings_cache(False); return False
    async def capture_image(self, download: bool = True) -> Optional[CaptureResponse]: # ... (same)
        if not self.camera or not self.selected_camera_info: logger.warning("No camera for capture."); return None
        logger.info(f"Capturing image ({'download' if download else 'on-camera'}) with {self.selected_camera_info.model}")
        try:
            file_path_on_camera = self.camera.capture(gp.GP_CAPTURE_IMAGE, self.context)
            logger.info(f"Captured on camera: {file_path_on_camera.folder}/{file_path_on_camera.name}")
            if not download: return CaptureResponse(message="Captured on camera, not downloaded.")
            timestamp = time.strftime("%Y%m%d-%H%M%S"); base, ext = os.path.splitext(file_path_on_camera.name)
            safe_model_name = re.sub(r'[^\w-]', '_', self.selected_camera_info.model)
            target_filename = f"{base}_{safe_model_name}_{timestamp}{ext}"
            # Save to general captures directory
            target_path_on_server = os.path.join(CAPTURES_BASE_DIR, target_filename)
            os.makedirs(CAPTURES_BASE_DIR, exist_ok=True) # Ensure general captures dir exists

            logger.info(f"Downloading image to: {target_path_on_server}")
            camera_file_obj = self.camera.file_get(
                file_path_on_camera.folder, file_path_on_camera.name, gp.GP_FILE_TYPE_NORMAL, self.context
            )
            camera_file_obj.save(target_path_on_server)
            logger.info(f"Image saved to {target_path_on_server}")
            return CaptureResponse(message="Image captured and downloaded.", file_path=target_path_on_server)
        except gp.GPhoto2Error as ex: logger.error(f"Error capturing image: {ex}"); return None

    async def start_preview(self, websocket: WebSocket): # ... (same raw preview)
        if not self.camera or not self.selected_camera_info:
            await websocket.send_json(WebSocketResponse(action=ActionType.GET_PREVIEW, success=False, error="No camera selected.").dict()); return
        if websocket not in self.preview_clients: self.preview_clients.append(websocket)
        logger.info(f"Client {websocket.client} added for raw preview from {self.selected_camera_info.model}.")
        if self.preview_task and not self.preview_task.done(): logger.info("Raw preview task already running."); return
        logger.info(f"Starting raw preview stream for {self.selected_camera_info.model}.")
        self.preview_task = asyncio.create_task(self._stream_preview())
    async def stop_preview(self, websocket: WebSocket): # ... (same raw preview)
        if websocket in self.preview_clients: self.preview_clients.remove(websocket)
        logger.info(f"Client {websocket.client} removed from raw preview.")
        if not self.preview_clients and self.preview_task and not self.preview_task.done():
            logger.info("No more raw preview clients. Stopping task.")
            self.preview_task.cancel(); await asyncio.gather(self.preview_task, return_exceptions=True)
            self.preview_task = None
    async def _stream_preview(self): # ... (same raw preview)
        if not self.camera or not self.selected_camera_info: logger.error("Raw preview stream: no camera."); return
        logger.info(f"Raw preview task started for {self.selected_camera_info.model}.")
        max_fails = 10; fail_count = 0
        try:
            while self.camera and self.selected_camera_info and self.preview_clients:
                try:
                    cap_file = self.camera.capture_preview(self.context)
                    if not cap_file or not (frame_bytes := memoryview(cap_file.get_data_and_size()).tobytes()): # type: ignore
                        logger.warning("Raw preview capture returned no data."); await asyncio.sleep(0.1); continue
                    for client_ws in list(self.preview_clients): 
                        try: await client_ws.send_bytes(frame_bytes)
                        except WebSocketDisconnect: logger.info(f"Raw preview client {client_ws.client} disconnected."); self.preview_clients.remove(client_ws) if client_ws in self.preview_clients else None
                        except Exception as e: logger.error(f"Error sending raw preview to {client_ws.client}: {e}"); self.preview_clients.remove(client_ws) if client_ws in self.preview_clients else None
                    fail_count = 0; await asyncio.sleep(0.03) 
                except gp.GPhoto2Error as ex:
                    fail_count += 1; logger.error(f"GPhoto2Error raw previewing: {ex} (Fail {fail_count}/{max_fails})")
                    if "Camera is already capturing" in str(ex) or "Could not claim USB" in str(ex): await asyncio.sleep(1)
                    elif fail_count >= max_fails: logger.error("Max raw preview errors. Stopping."); break
                    else: await asyncio.sleep(0.5)
                except Exception as e: logger.error(f"Unexpected raw preview error: {e}", exc_info=True); break
        except asyncio.CancelledError: logger.info(f"Raw preview task for {self.selected_camera_info.model if self.selected_camera_info else 'N/A'} cancelled.")
        finally:
            logger.info(f"Raw preview task for {self.selected_camera_info.model if self.selected_camera_info else 'N/A'} ended.")
            self.preview_task = None; err_resp = WebSocketResponse(action=ActionType.GET_PREVIEW, success=False, error="Raw preview stream ended or failed.").dict()
            for client_ws in list(self.preview_clients): 
                try: await client_ws.send_json(err_resp)
                except: pass 
            self.preview_clients.clear()
    async def start_liveview(self, websocket: WebSocket, request_id: Optional[str] = None): # ... (same Base64 liveview)
        ack_action = ActionType.START_LIVEVIEW 
        if not self.camera or not self.selected_camera_info:
            logger.warning("Liveview: No camera selected."); await websocket.send_json(WebSocketResponse(action=ack_action, success=False, error="No camera selected.", request_id=request_id).dict()); return
        if self.liveview_active or self.current_liveview_websocket:
            logger.warning(f"Liveview already active."); await websocket.send_json(WebSocketResponse(action=ack_action, success=False, error="Liveview already active.", request_id=request_id).dict()); return
        try:
            abilities = self.camera.get_abilities()
            if not (abilities.operations & gp.GP_OPERATION_CAPTURE_PREVIEW):
                logger.warning(f"Camera {self.selected_camera_info.model} no preview support."); await websocket.send_json(WebSocketResponse(action=ack_action, success=False, error="Camera no preview support.", request_id=request_id).dict()); return
        except gp.GPhoto2Error as e:
            logger.error(f"Error checking preview abilities: {e}"); await websocket.send_json(WebSocketResponse(action=ack_action, success=False, error=f"Error checking preview abilities: {e}", request_id=request_id).dict()); return
        self.liveview_active = True; self.current_liveview_websocket = websocket
        logger.info(f"Starting B64 JSON liveview for {websocket.client} on {self.selected_camera_info.model}.")
        await websocket.send_json(WebSocketResponse(action=ack_action, success=True, data={"message": "Liveview stream initiated."}, request_id=request_id).dict())
        fail_count = 0; max_fails = 5
        try:
            while self.liveview_active and self.camera and self.selected_camera_info:
                if self.current_liveview_websocket != websocket: logger.warning("Liveview ws mismatch. Stopping."); break
                try:
                    capture = self.camera.capture_preview(self.context)
                    if not capture or not (file_data_view := capture.get_data_and_size()): logger.warning("Liveview: no data."); await asyncio.sleep(0.1); continue # type: ignore
                    frame_bytes = file_data_view.tobytes(); base64_data = base64.b64encode(frame_bytes).decode('utf-8')
                    await self.current_liveview_websocket.send_json(LiveviewFrameMessage(frame=base64_data).dict())
                    fail_count = 0; await asyncio.sleep(LIVEVIEW_FRAME_INTERVAL) 
                except gp.GPhoto2Error as ex:
                    fail_count += 1; logger.error(f"GPhoto2Error liveview: {ex} (Fail {fail_count})")
                    if fail_count >= max_fails: logger.error(f"Max GPhoto2 errors liveview. Stopping."); await self.current_liveview_websocket.send_json(WebSocketResponse(action=ActionType.STOP_LIVEVIEW, success=False, error=f"Liveview fail: {ex}").dict()); break 
                    await asyncio.sleep(0.5) 
                except WebSocketDisconnect: logger.info(f"Liveview client {websocket.client} disconnected."); break 
                except Exception as e: logger.error(f"Unexpected liveview error: {e}", exc_info=True); await self.current_liveview_websocket.send_json(WebSocketResponse(action=ActionType.STOP_LIVEVIEW, success=False, error=f"Liveview server error: {e}").dict()); break 
        except asyncio.CancelledError: logger.info(f"Liveview stream cancelled.")
        finally:
            logger.info(f"Liveview stream ended.")
            self.liveview_active = False
            if self.current_liveview_websocket == websocket: self.current_liveview_websocket = None
    async def stop_liveview(self): # ... (same Base64 liveview)
        logger.info("Stopping Base64 JSON liveview."); self.liveview_active = False

    async def capture_image_for_set(self, set_folder_name: str, file_basename: str) -> Optional[CaptureResponse]: # ... (same)
        if not self.camera or not self.selected_camera_info: logger.warning("Photometric: No camera selected."); return None
        target_set_dir = os.path.join(PHOTOMETRIC_SETS_BASE_DIR, set_folder_name)
        try: os.makedirs(target_set_dir, exist_ok=True)
        except OSError as e: logger.error(f"Photometric: Error creating dir {target_set_dir}: {e}"); return None
        if '.' not in file_basename: file_basename += ".jpg" 
        target_path_on_server = os.path.join(target_set_dir, file_basename)
        logger.info(f"Photometric: Capturing for set '{set_folder_name}' to '{target_path_on_server}'")
        try:
            file_path_on_camera = self.camera.capture(gp.GP_CAPTURE_IMAGE, self.context)
            logger.info(f"Photometric: Captured on camera: {file_path_on_camera.folder}/{file_path_on_camera.name}")
            camera_file_obj = self.camera.file_get(file_path_on_camera.folder, file_path_on_camera.name, gp.GP_FILE_TYPE_NORMAL, self.context)
            camera_file_obj.save(target_path_on_server); logger.info(f"Photometric: Saved to {target_path_on_server}")
            return CaptureResponse(message="Image captured for set.", file_path=target_path_on_server)
        except gp.GPhoto2Error as ex: logger.error(f"Photometric: GPhoto2Error capturing for set: {ex}"); return None
        except Exception as e: logger.error(f"Photometric: Unexpected error capturing for set: {e}", exc_info=True); return None
    async def run_photometric_sequence(self, websocket: WebSocket, set_name_prefix: Optional[str], light_sequence: List[str], request_id: Optional[str]): # ... (same)
        if not self.camera or not self.selected_camera_info: await websocket.send_json(WebSocketResponse(action=ActionType.CAPTURE_PHOTOMETRIC_SET, success=False, error="No camera selected.", request_id=request_id).dict()); return
        if not self.light_controller.get_gpio_availability(): await websocket.send_json(WebSocketResponse(action=ActionType.CAPTURE_PHOTOMETRIC_SET, success=False, error="GPIO not available.", request_id=request_id).dict()); return
        if not light_sequence: await websocket.send_json(WebSocketResponse(action=ActionType.CAPTURE_PHOTOMETRIC_SET, success=False, error="Light sequence empty.", request_id=request_id).dict()); return
        timestamp = time.strftime("%Y%m%d-%H%M%S")
        folder_name_part = f"{set_name_prefix}_{timestamp}" if set_name_prefix else f"photometric_set_{timestamp}"
        safe_folder_name = re.sub(r'[^\w\-.]', '_', folder_name_part)
        captured_image_paths: List[str] = []; sequence_failed = False; error_message = ""
        logger.info(f"Starting photometric sequence. Set: {safe_folder_name}, Lights: {light_sequence}")
        for light_name_to_reset in LIGHT_PINS.keys(): self.light_controller.set_light_state(light_name_to_reset, False)
        await asyncio.sleep(0.1)
        for idx, light_name in enumerate(light_sequence):
            if light_name not in LIGHT_PINS:
                error_detail = f"Invalid light name '{light_name}'"; logger.error(f"Photometric: {error_detail}")
                await websocket.send_json(WebSocketResponse(action=ActionType.PHOTOMETRIC_PROGRESS, success=False, data=PhotometricProgressData(status=f"error_light_{idx+1}", current_light=light_name, set_folder=safe_folder_name, error_detail=error_detail).dict(), request_id=request_id).dict())
                sequence_failed = True; error_message = error_detail; break 
            progress_data = PhotometricProgressData(status=f"processing_light_{idx+1}_of_{len(light_sequence)}", current_light=light_name, set_folder=safe_folder_name)
            await websocket.send_json(WebSocketResponse(action=ActionType.PHOTOMETRIC_PROGRESS, success=True, data=progress_data.dict(), request_id=request_id).dict())
            set_ok, msg = self.light_controller.set_light_state(light_name, True)
            if not set_ok:
                error_detail = f"Failed to turn ON light: {light_name} ({msg})"; logger.error(f"Photometric: {error_detail}")
                await websocket.send_json(WebSocketResponse(action=ActionType.PHOTOMETRIC_PROGRESS, success=False, data=PhotometricProgressData(status=f"error_light_{idx+1}", current_light=light_name, set_folder=safe_folder_name, error_detail=error_detail).dict(), request_id=request_id).dict())
                sequence_failed = True; error_message = error_detail; break
            await asyncio.sleep(PHOTOMETRIC_CAPTURE_DELAY)
            file_basename = f"image_{idx+1:02d}_{light_name}"
            capture_response = await self.capture_image_for_set(set_folder_name=safe_folder_name, file_basename=file_basename)
            self.light_controller.set_light_state(light_name, False) # Turn light OFF
            if not capture_response or not capture_response.file_path:
                error_detail = f"Capture failed for light: {light_name}. Reason: {capture_response.message if capture_response else 'Unknown'}"
                logger.error(f"Photometric: {error_detail}")
                await websocket.send_json(WebSocketResponse(action=ActionType.PHOTOMETRIC_PROGRESS, success=False, data=PhotometricProgressData(status=f"error_light_{idx+1}", current_light=light_name, set_folder=safe_folder_name, error_detail=error_detail).dict(), request_id=request_id).dict())
                sequence_failed = True; error_message = error_detail; break
            captured_image_paths.append(os.path.relpath(capture_response.file_path, CAPTURES_BASE_DIR)) # Store relative to 'captures'
            await asyncio.sleep(PHOTOMETRIC_CAPTURE_DELAY / 2)
        for light_name_to_off in LIGHT_PINS.keys(): self.light_controller.set_light_state(light_name_to_off, False)
        final_data = PhotometricSetResponseData(message=f"Sequence {'failed: ' + error_message if sequence_failed else 'completed.'}", set_folder=safe_folder_name, image_count=len(captured_image_paths), images_captured=captured_image_paths)
        await websocket.send_json(WebSocketResponse(action=ActionType.CAPTURE_PHOTOMETRIC_SET, success=not sequence_failed, error=error_message if sequence_failed else None, data=final_data.dict(), request_id=request_id).dict())
        logger.info(f"Photometric sequence for {safe_folder_name} ended. Success: {not sequence_failed}")

    # --- New Image/Set Management Methods ---
    async def list_image_sets(self) -> List[ImageSetInfo]:
        set_infos: List[ImageSetInfo] = []
        if not os.path.exists(PHOTOMETRIC_SETS_BASE_DIR) or not os.path.isdir(PHOTOMETRIC_SETS_BASE_DIR):
            logger.info("Photometric sets base directory does not exist or is not a directory.")
            return set_infos # Return empty list
        try:
            for item_name in os.listdir(PHOTOMETRIC_SETS_BASE_DIR):
                if os.path.isdir(os.path.join(PHOTOMETRIC_SETS_BASE_DIR, item_name)):
                    # Basic validation: ensure item_name is a simple directory name
                    if ".." not in item_name and "/" not in item_name and "\\" not in item_name:
                         set_infos.append(ImageSetInfo(name=item_name))
                    else:
                        logger.warning(f"Skipping potentially unsafe directory name: {item_name}")
            return sorted(set_infos, key=lambda x: x.name, reverse=True) # Sort by name, newest first if timestamped
        except OSError as e:
            logger.error(f"Error listing image sets in {PHOTOMETRIC_SETS_BASE_DIR}: {e}")
            return [] # Return empty on error

    async def get_image_set_contents(self, set_name: str) -> Optional[List[ImageFileDetails]]:
        if not self._is_path_safe(PHOTOMETRIC_SETS_BASE_DIR, set_name):
            logger.error(f"Access denied or invalid set name for get_image_set_contents: {set_name}")
            return None
        
        set_dir_path = os.path.join(PHOTOMETRIC_SETS_BASE_DIR, set_name)
        if not os.path.exists(set_dir_path) or not os.path.isdir(set_dir_path):
            logger.warning(f"Image set '{set_name}' not found at {set_dir_path}.")
            return None

        image_files: List[ImageFileDetails] = []
        allowed_extensions = ('.jpg', '.jpeg', '.png', '.cr2', '.nef', '.arw') # Common image extensions
        try:
            for filename in os.listdir(set_dir_path):
                if filename.lower().endswith(allowed_extensions):
                    full_file_path = os.path.join(set_dir_path, filename)
                    if os.path.isfile(full_file_path):
                        # Return path relative to server root (or a defined media root)
                        # For simplicity here, relative to where the server is run from.
                        relative_path = os.path.relpath(full_file_path, os.getcwd()) 
                        # Normalize to use forward slashes for web paths
                        web_friendly_path = relative_path.replace("\\", "/")
                        image_files.append(ImageFileDetails(filename=filename, path=web_friendly_path))
            return sorted(image_files, key=lambda x: x.filename)
        except OSError as e:
            logger.error(f"Error reading contents of image set '{set_name}': {e}")
            return None

    async def delete_image_set(self, set_name: str) -> bool:
        if not self._is_path_safe(PHOTOMETRIC_SETS_BASE_DIR, set_name):
            logger.error(f"Access denied or invalid set name for delete_image_set: {set_name}")
            return False
        
        set_dir_path = os.path.join(PHOTOMETRIC_SETS_BASE_DIR, set_name)
        if not os.path.exists(set_dir_path) or not os.path.isdir(set_dir_path):
            logger.warning(f"Cannot delete: Image set '{set_name}' not found at {set_dir_path}.")
            return False
        
        try:
            shutil.rmtree(set_dir_path)
            logger.info(f"Successfully deleted image set: {set_dir_path}")
            return True
        except OSError as e:
            logger.error(f"Error deleting image set '{set_name}' at {set_dir_path}: {e}")
            return False

    async def get_image_data(self, image_path: str) -> Optional[ImageDataResponse]:
        # Crucial security: Ensure image_path is within allowed directories (e.g., CAPTURES_BASE_DIR)
        # Normalize the user-provided path and the allowed base path
        normalized_image_path = os.path.normpath(image_path)
        absolute_image_path = os.path.abspath(normalized_image_path)
        absolute_captures_base = os.path.abspath(CAPTURES_BASE_DIR)

        if not absolute_image_path.startswith(absolute_captures_base):
            logger.error(f"Security: Access denied for image path outside '{CAPTURES_BASE_DIR}': {image_path}")
            return None
        
        # Double check for any traversal components that might have been missed if image_path was absolute
        if ".." in normalized_image_path:
             logger.error(f"Security: Path traversal suspected in '{image_path}' despite normalization.")
             return None

        if not os.path.exists(absolute_image_path) or not os.path.isfile(absolute_image_path):
            logger.warning(f"Image file not found or not a file: {absolute_image_path}")
            return None
        
        try:
            with open(absolute_image_path, "rb") as f:
                image_bytes = f.read()
            
            base64_data = base64.b64encode(image_bytes).decode('utf-8')
            mimetype, _ = mimetypes.guess_type(absolute_image_path)
            if not mimetype:
                mimetype = 'application/octet-stream' # Default if type can't be guessed
            
            return ImageDataResponse(
                filename=os.path.basename(absolute_image_path),
                image_b64=base64_data,
                mimetype=mimetype
            )
        except IOError as e:
            logger.error(f"IOError reading image file '{absolute_image_path}': {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error getting image data for '{absolute_image_path}': {e}", exc_info=True)
            return None


    async def cleanup(self): # ... (same, ensures all tasks and controllers are cleaned up)
        logger.info("Cleaning up GPhoto2API resources...")
        await self.stop_liveview(); await self._stop_periodic_cache_refresh()
        if self.preview_task and not self.preview_task.done(): self.preview_task.cancel(); await asyncio.gather(self.preview_task, return_exceptions=True)
        if self.camera:
            try: self.camera.exit(self.context); logger.info("Camera exited.")
            except gp.GPhoto2Error as ex: logger.error(f"Error exiting camera: {ex}")
        self.camera, self.selected_camera_info = None, None
        self.settings_cache.clear(); self.preview_clients.clear()
        if self.light_controller: self.light_controller.cleanup()
        logger.info("GPhoto2API cleanup complete.")

# --- FastAPI Application & Connection Manager (handle_message needs updates) ---
app = FastAPI(title="gphoto2 WebSocket Server")
gphoto_api_singleton = GPhoto2API() 
app.add_middleware( CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
@app.on_event("startup")
async def startup_event(): logger.info("Application startup...")
@app.on_event("shutdown")
async def shutdown_event(): logger.info("Application shutdown requested..."); await gphoto_api_singleton.cleanup()

class ConnectionManager: # ... (same structure, handle_message updated)
    def __init__(self, api_instance: GPhoto2API):
        self.active_connections: List[WebSocket] = []
        self.gphoto_api = api_instance 
    async def connect(self, websocket: WebSocket): # ... (same)
        await websocket.accept(); self.active_connections.append(websocket)
        logger.info(f"Client {websocket.client} connected. Total: {len(self.active_connections)}")
    async def disconnect(self, websocket: WebSocket): # ... (same, ensures liveview is stopped)
        if websocket in self.active_connections: self.active_connections.remove(websocket)
        logger.info(f"Client {websocket.client} disconnected.")
        await self.gphoto_api.stop_preview(websocket) 
        if self.gphoto_api.current_liveview_websocket == websocket:
            logger.info(f"Client {websocket.client} was active liveview client. Stopping liveview.")
            await self.gphoto_api.stop_liveview() 
        if not self.active_connections and not self.gphoto_api.preview_clients and not self.gphoto_api.liveview_active:
            if self.gphoto_api.selected_camera_info:
                logger.info("Last client disconnected & no active streams. Deselecting camera.")
                await self.gphoto_api.deselect_camera()
    async def send_response(self, websocket: WebSocket, action: Union[ActionType, str], success: bool, data: Optional[Any] = None, error: Optional[str] = None, request_id: Optional[str] = None): # ... (same)
        action_str = action.value if isinstance(action, ActionType) else action
        response = WebSocketResponse(action=action_str, success=success, data=data, error=error, request_id=request_id) # type: ignore
        try: await websocket.send_json(response.dict())
        except WebSocketDisconnect: logger.warning(f"Client {websocket.client} disconnected before response for '{action_str}' (ReqID: {request_id}).")
        except Exception as e: logger.error(f"Error sending JSON for '{action_str}' (ReqID: {request_id}) to {websocket.client}: {e}")

    async def handle_message(self, websocket: WebSocket, message: str): # Modified for new actions
        req_id: Optional[str] = None; parsed_req: Optional[WebSocketRequest] = None; action_str = "unknown_action"
        try:
            json_data = json.loads(message); req_id = json_data.get("request_id"); action_str = json_data.get("action", "unknown_action")
            parsed_req = WebSocketRequest(**json_data)
            logger.info(f"Action '{parsed_req.action.value}' from {websocket.client}" + (f" (ReqID: {req_id})" if req_id else ""))
            payload = parsed_req.payload or {}

            # Existing actions ... (condensed for brevity, assume they are the same)
            if parsed_req.action == ActionType.GET_CAMERAS: await self.send_response(websocket, parsed_req.action, True, data=[c.dict() for c in await self.gphoto_api.list_cameras()], request_id=req_id)
            elif parsed_req.action == ActionType.SELECT_CAMERA:
                success = await self.gphoto_api.select_camera(payload.get("model"), payload.get("port"))
                data = self.gphoto_api.selected_camera_info.dict() if success and self.gphoto_api.selected_camera_info else None
                await self.send_response(websocket, parsed_req.action, success, data=data, error=None if success else "Failed to select camera.", request_id=req_id)
            elif parsed_req.action == ActionType.DESELECT_CAMERA: await self.send_response(websocket, parsed_req.action, True, data={"message": "Camera deselected." if await self.gphoto_api.deselect_camera() else "No camera selected."}, request_id=req_id)
            elif parsed_req.action == ActionType.GET_CONFIG:
                if not self.gphoto_api.selected_camera_info: await self.send_response(websocket, parsed_req.action, False, error="No camera selected.", request_id=req_id); return
                cfg_data = await self.gphoto_api.get_config(payload.get("config_name"))
                data = [c.dict() for c in cfg_data] if isinstance(cfg_data, list) else (cfg_data.dict() if cfg_data else None)
                await self.send_response(websocket, parsed_req.action, cfg_data is not None, data=data, error=None if cfg_data is not None else "Config not found/failed.", request_id=req_id)
            elif parsed_req.action == ActionType.SET_CONFIG:
                if not self.gphoto_api.selected_camera_info: await self.send_response(websocket, parsed_req.action, False, error="No camera selected.", request_id=req_id); return
                cfg_name, value = payload.get("config_name"), payload.get("value")
                if cfg_name is None or value is None: await self.send_response(websocket, parsed_req.action, False, error="config_name and value required.", request_id=req_id); return
                success = await self.gphoto_api.set_config(cfg_name, value)
                updated_cfg = await self.gphoto_api.get_config(cfg_name) if success else None
                await self.send_response(websocket, parsed_req.action, success, data=updated_cfg.dict() if updated_cfg else None, error=None if success else f"Failed to set config '{cfg_name}'.", request_id=req_id)
            elif parsed_req.action == ActionType.CAPTURE_IMAGE: 
                if not self.gphoto_api.selected_camera_info: await self.send_response(websocket, parsed_req.action, False, error="No camera selected.", request_id=req_id); return
                cap_res = await self.gphoto_api.capture_image(payload.get("download", True))
                await self.send_response(websocket, parsed_req.action, cap_res is not None, data=cap_res.dict() if cap_res else None, error=None if cap_res else "Failed to capture image.", request_id=req_id)
            elif parsed_req.action == ActionType.GET_PREVIEW: 
                if not self.gphoto_api.selected_camera_info: await self.send_response(websocket, parsed_req.action, False, error="No camera selected.", request_id=req_id); return
                await self.gphoto_api.start_preview(websocket); await self.send_response(websocket, parsed_req.action, True, data={"message": "Raw byte preview stream requested."}, request_id=req_id)
            elif parsed_req.action == ActionType.GET_LIGHT_STATES:
                states = self.gphoto_api.light_controller.get_light_states(); gpio_avail = self.gphoto_api.light_controller.get_gpio_availability()
                await self.send_response(websocket, parsed_req.action, True, data=LightStatesInfo(states=states, gpio_available=gpio_avail).dict(), request_id=req_id)
            elif parsed_req.action == ActionType.SET_LIGHT_STATE:
                try: light_payload = LightStatePayload(**payload)
                except Exception as e: await self.send_response(websocket, parsed_req.action, False, error=f"Invalid payload: {e}", request_id=req_id); return
                success, message = self.gphoto_api.light_controller.set_light_state(light_payload.light_name, light_payload.state)
                response_data = SetLightStateResponseData(light_name=light_payload.light_name, new_state=self.gphoto_api.light_controller.light_states.get(light_payload.light_name, light_payload.state), gpio_available=self.gphoto_api.light_controller.get_gpio_availability(), message=message)
                await self.send_response(websocket, parsed_req.action, success, data=response_data.dict(), request_id=req_id)
            elif parsed_req.action == ActionType.START_LIVEVIEW: await self.gphoto_api.start_liveview(websocket, req_id)
            elif parsed_req.action == ActionType.STOP_LIVEVIEW:
                await self.gphoto_api.stop_liveview()
                await self.send_response(websocket, parsed_req.action, True, data={"message": "Liveview stream stopped."}, request_id=req_id)
            elif parsed_req.action == ActionType.CAPTURE_PHOTOMETRIC_SET:
                try:
                    photometric_payload = PhotometricSetPayload(**payload)
                    if not photometric_payload.light_sequence: await self.send_response(websocket, parsed_req.action, False, error="Light sequence cannot be empty.", request_id=req_id); return
                    asyncio.create_task(self.gphoto_api.run_photometric_sequence(websocket, photometric_payload.set_name_prefix, photometric_payload.light_sequence, req_id))
                except Exception as e: logger.error(f"Invalid payload for CAPTURE_PHOTOMETRIC_SET: {e}", exc_info=True); await self.send_response(websocket, parsed_req.action, False, error=f"Invalid payload: {e}", request_id=req_id)
            
            # New Image/Set Management actions
            elif parsed_req.action == ActionType.LIST_IMAGE_SETS:
                sets_data = await self.gphoto_api.list_image_sets()
                await self.send_response(websocket, parsed_req.action, True, data=[s.dict() for s in sets_data], request_id=req_id)
            
            elif parsed_req.action == ActionType.GET_IMAGE_SET_CONTENTS:
                try:
                    contents_payload = ImageSetContentsPayload(**payload)
                    contents_data = await self.gphoto_api.get_image_set_contents(contents_payload.set_name)
                    if contents_data is not None:
                        await self.send_response(websocket, parsed_req.action, True, data=[c.dict() for c in contents_data], request_id=req_id)
                    else:
                        await self.send_response(websocket, parsed_req.action, False, error=f"Set '{contents_payload.set_name}' not found or empty.", request_id=req_id)
                except Exception as e: # Pydantic validation error
                    await self.send_response(websocket, parsed_req.action, False, error=f"Invalid payload for get_image_set_contents: {e}", request_id=req_id)

            elif parsed_req.action == ActionType.DELETE_IMAGE_SET:
                try:
                    delete_payload = DeleteImageSetPayload(**payload)
                    success = await self.gphoto_api.delete_image_set(delete_payload.set_name)
                    if success:
                        await self.send_response(websocket, parsed_req.action, True, data={"message": f"Set '{delete_payload.set_name}' deleted successfully."}, request_id=req_id)
                    else:
                        await self.send_response(websocket, parsed_req.action, False, error=f"Failed to delete set '{delete_payload.set_name}'. It might not exist or an error occurred.", request_id=req_id)
                except Exception as e: # Pydantic validation error
                    await self.send_response(websocket, parsed_req.action, False, error=f"Invalid payload for delete_image_set: {e}", request_id=req_id)

            elif parsed_req.action == ActionType.GET_IMAGE_DATA:
                try:
                    image_data_payload = GetImageDataPayload(**payload)
                    image_data = await self.gphoto_api.get_image_data(image_data_payload.image_path)
                    if image_data:
                        await self.send_response(websocket, parsed_req.action, True, data=image_data.dict(), request_id=req_id)
                    else:
                        await self.send_response(websocket, parsed_req.action, False, error=f"Image not found or access denied: {image_data_payload.image_path}", request_id=req_id)
                except Exception as e: # Pydantic validation error
                    await self.send_response(websocket, parsed_req.action, False, error=f"Invalid payload for get_image_data: {e}", request_id=req_id)
            
            else: 
                logger.warning(f"Unknown action '{parsed_req.action}' (ReqID: {req_id})")
                await self.send_response(websocket, parsed_req.action, False, error="Unknown action.", request_id=req_id)
        except json.JSONDecodeError: logger.error(f"Invalid JSON from {websocket.client}: {message}"); await self.send_response(websocket, action_str, False, error="Invalid JSON.", request_id=req_id)
        except Exception as e: logger.error(f"Error processing msg (Action: {action_str}, ReqID: {req_id}): {e}", exc_info=True); await self.send_response(websocket, action_str, False, error=f"Server error: {e}", request_id=req_id)

manager = ConnectionManager(api_instance=gphoto_api_singleton)
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket): # ... (same)
    await manager.connect(websocket)
    try:
        while True: await manager.handle_message(websocket, await websocket.receive_text())
    except WebSocketDisconnect: logger.debug(f"Client {websocket.client} triggered WebSocketDisconnect.")
    except Exception as e: logger.error(f"Error in WebSocket endpoint for {websocket.client}: {e}", exc_info=True); await asyncio.gather(websocket.close(code=1011, reason=str(e)), return_exceptions=True)
    finally: await manager.disconnect(websocket)

# --- CLI Helpers (condensed, assume same as before) ---
async def _get_camera_abilities_cli(gphoto_api: GPhoto2API, model:str, port:str) -> Optional[Dict]: # ... (same)
    logger.info(f"Getting abilities for {model} on {port} (CLI mode)...")
    if await gphoto_api.select_camera(model, port):
        abilities_text = f"Abilities for {model} (details from cache or direct query)"; cfg_names = list(gphoto_api.settings_cache.keys())[:5]
        await gphoto_api.deselect_camera(); return {"model": model, "port": port, "abilities": abilities_text, "cached_config_names": cfg_names}
    return None
async def _log_detected_cameras_details_cli(gphoto_api_instance: GPhoto2API): # ... (same)
    logger.info("CLI: Attempting to log details for all detected cameras...")
    cameras = await gphoto_api_instance.list_cameras()
    for cam_info in cameras:
        details = await _get_camera_abilities_cli(gphoto_api_instance, cam_info.model, cam_info.port)
        if details: logger.info(f"Camera: {details['model']}, Port: {details['port']}, Abilities: {details['abilities']}, Sample Configs: {details['cached_config_names']}")

# --- Main Execution ---
if __name__ == "__main__": # ... (same)
    import argparse
    parser = argparse.ArgumentParser(description="gphoto2 WebSocket Server with Full Features")
    parser.add_argument("--host",type=str,default="0.0.0.0",help="Host (0.0.0.0)"); parser.add_argument("--port",type=int,default=8000,help="Port (8000)")
    parser.add_argument("--log-cameras",action="store_true",help="List cameras info and exit.")
    cli_args = parser.parse_args()
    logger.info(f"GPIO Available: {gpio_imported_successfully}. Liveview FPS: ~{1/LIVEVIEW_FRAME_INTERVAL:.0f}. Photometric delay: {PHOTOMETRIC_CAPTURE_DELAY}s")
    if cli_args.log_cameras:
        logger.info("Log cameras mode: Will list cameras and exit.")
        async def log_and_exit():
            try: await _log_detected_cameras_details_cli(gphoto_api_singleton)
            except Exception as e: logger.error(f"Error --log-cameras: {e}", exc_info=True)
            finally: logger.info("Log cameras finished. Cleaning up."); await gphoto_api_singleton.cleanup(); os._exit(0) 
        try: asyncio.run(log_and_exit())
        except KeyboardInterrupt: logger.info("Log cameras cancelled."); asyncio.run(gphoto_api_singleton.cleanup()); os._exit(0)
    else:
        logger.info(f"Starting Uvicorn server on {cli_args.host}:{cli_args.port}")
        uvicorn.run( "__main__:app", host=cli_args.host, port=cli_args.port, ws_max_size=16 * 1024 * 1024) # type: ignore
```
