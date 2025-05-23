# gphoto2 WebSocket API Server

## Project Overview

This project provides a WebSocket server, built with FastAPI and `python-gphoto2`, for advanced remote camera control. It's designed to expose comprehensive camera functionalities over a network, making it ideal for IoT applications (e.g., on a Raspberry Pi) and complex imaging setups. Key features include standard camera operations, liveview streaming, GPIO-controlled lighting, automated photometric stereo capture sequences, and management of captured image sets.

A conceptual Next.js frontend, including services like `WebSocketService.ts` and components such as `Liveview.tsx`, `PhotometricStereoControl.tsx`, and `ImageGallery.tsx`, demonstrates how to interact with this powerful API.

## Features

*   **Camera Control:**
    *   List available cameras.
    *   Select and deselect cameras by model or port.
    *   Get and set camera configurations (e.g., shutter speed, ISO, aperture).
    *   In-memory caching for camera configurations with periodic refresh for speed and up-to-date settings.
    *   Capture still images, with an option to download them to the server.
*   **Liveview:**
    *   Real-time preview stream from the camera, sent as Base64 encoded JPEG frames over WebSocket for easy display in web clients.
*   **GPIO Light Control:**
    *   Control connected lights or other devices via GPIO pins on a Raspberry Pi (or similar SBC).
    *   Light pin mapping is configurable within the server script.
    *   Gracefully handles environments where RPi.GPIO is not available by stubbing GPIO functions.
*   **Photometric Stereo:**
    *   Automated capture sequence for photometric stereo.
    *   Iterates through a user-defined sequence of lights.
    *   Captures an image for each light condition.
    *   Saves images into organized, timestamped (and optionally prefixed) sets.
    *   Provides real-time progress updates over WebSocket during the sequence.
*   **Image Set Management:**
    *   List all captured image sets (especially those from photometric stereo).
    *   View the contents (filenames and paths) of a specific image set.
    *   Delete entire image sets.
*   **Image Access:**
    *   Retrieve individual captured images as Base64 encoded data with their mimetype.
*   **Robust API:**
    *   WebSocket-based API for real-time, bidirectional communication.
    *   Input validation using Pydantic for all incoming WebSocket messages.
*   **Logging & Configuration:**
    *   Comprehensive logging for server operations and errors.
    *   Key operational parameters configurable via environment variables.

## Requirements

### Python Server (e.g., Raspberry Pi)

*   **Python:** 3.8+
*   **Key Python Libraries:**
    *   `fastapi`
    *   `uvicorn`
    *   `python-gphoto2` (requires libgphoto2 system library)
    *   `websockets` (typically managed by FastAPI/Uvicorn for WebSocket support)
    *   `pydantic` (for data validation)
    *   `RPi.GPIO` (optional, for light control on Raspberry Pi)
    *   `aiofiles` (often useful with FastAPI for async file operations, though not explicitly in current code, good to note)
*   **System Libraries:**
    *   `libgphoto2-dev` (or equivalent for your OS, e.g., `libgphoto2-devel`) for `python-gphoto2`.
    *   Appropriate kernel headers and C compilers if `RPi.GPIO` needs to be built from source.
*   **Tools:**
    *   `pip` for installing Python packages.
    *   Virtual environments (e.g., `venv`) are highly recommended.

### Frontend (Conceptual - for using the API)

*   A WebSocket client. The provided `services/WebSocketService.ts` (for Next.js/TypeScript) serves as a reference implementation.
*   A JavaScript/TypeScript environment if using the example service or building a similar client.

## Installation (Python Server)

1.  **Clone the Repository:**
    ```bash
    git clone <repository-url>
    cd gphoto2-websocket-api
    ```

2.  **Install System Dependencies:**

    *   **For libgphoto2 (Debian/Ubuntu/Raspberry Pi OS):**
        ```bash
        sudo apt-get update
        sudo apt-get install -y libgphoto2-dev gphoto2
        ```
        *Run `gphoto2 --version` to ensure libgphoto2 is installed and working.*
        *Run `gphoto2 --auto-detect` to check if your camera is detected.*

    *   **For RPi.GPIO (Raspberry Pi only):**
        ```bash
        sudo apt-get install -y python3-rpi.gpio 
        # Or ensure your Python environment can access it.
        ```

3.  **Set up Python Environment and Install Dependencies:**
    *   It's highly recommended to use a virtual environment:
        ```bash
        python3 -m venv venv
        source venv/bin/activate
        ```
    *   Install Python packages (create a `requirements.txt` for easier installation):
        ```bash
        pip install fastapi uvicorn "python-gphoto2>=2.3.0" pydantic RPi.GPIO # Add other dependencies as needed
        ```
        *(If not on Raspberry Pi or without GPIO hardware, you can omit `RPi.GPIO` or install a mock version if your OS complains during pip install. The server will stub GPIO functions if the library isn't found.)*

    *   **Example `requirements.txt`:**
        ```
        fastapi
        uvicorn[standard]
        python-gphoto2>=2.3.0
        pydantic
        RPi.GPIO
        # Add other direct dependencies here
        ```
        Then run: `pip install -r requirements.txt`

## Running the Server

The server is controlled by `gphoto2-websocket-server.py` and run using Uvicorn.

1.  **Environment Variables:**
    Configure these variables before running the server, or use their default values.

    *   `LOG_LEVEL`: Logging verbosity. Examples: `DEBUG`, `INFO`, `WARNING`, `ERROR`. (Default: `INFO`)
    *   `SETTINGS_REFRESH_INTERVAL`: Interval in seconds for refreshing the camera settings cache. (Default: `10`)
    *   `LIVEVIEW_FRAME_INTERVAL`: Target interval in seconds between liveview frames (e.g., `0.05` for ~20 FPS). (Default: `0.05`)
    *   `PHOTOMETRIC_CAPTURE_DELAY`: Delay in seconds between a light change and image capture in photometric sequences. (Default: `0.5`)
    *   `CAPTURES_BASE_DIR`: Base directory where all captured images and sets are stored. (Default: `captures`)
    *   `PHOTOMETRIC_SETS_BASE_DIR`: Subdirectory under `CAPTURES_BASE_DIR` specifically for photometric sets. (Default: `captures/photometric_sets`)

    *Note: The previous version mentioned `WS_HOST`, `WS_PORT`, and `API_TOKEN`. In this FastAPI version, host and port are specified in the `uvicorn` command. API token authentication is not currently implemented in the FastAPI server but could be added as middleware.*

2.  **Start the Server:**
    Navigate to the directory containing `gphoto2-websocket-server.py` and run:
    ```bash
    uvicorn gphoto2-websocket-server:app --host 0.0.0.0 --port 8000
    ```
    *   Replace `0.0.0.0` with a specific IP if needed.
    *   Replace `8000` with your desired port.
    *   For development, you can add `--reload` to automatically restart the server on code changes:
        ```bash
        uvicorn gphoto2-websocket-server:app --host 0.0.0.0 --port 8000 --reload
        ```

## WebSocket API Documentation

The server communicates using JSON messages over WebSocket.

*   **Client to Server (Request) Format:**
    ```json
    {
      "action": "ACTION_NAME",
      "payload": { /* action-specific data */ },
      "request_id": "client-generated-unique-id" 
    }
    ```
    *   `request_id` is crucial for matching responses to requests, especially when multiple commands can be in flight.

*   **Server to Client (Response/Event) Format:**
    ```json
    {
      "action": "REQUESTED_ACTION_NAME_OR_EVENT_NAME", 
      "success": true, // or false
      "data": { /* success data, if any */ },
      "error": "Error message if success is false",
      "request_id": "echoed-client-generated-unique-id" // For direct responses
    }
    ```
    *   For server-initiated messages (like `PHOTOMETRIC_PROGRESS` or `liveview_frame`), `request_id` might be the `request_id` of the initial command that started the process, or absent if it's a true broadcast not tied to a specific client request.

### Available Actions (`action` field)

---

1.  **`GET_CAMERAS`**
    *   **Description:** Lists all cameras auto-detected by `libgphoto2`.
    *   **Payload:** `{}` (empty)
    *   **Success `data` Example:**
        ```json
        [
          {"model": "Canon EOS Rebel T6", "port": "usb:001,006"},
          {"model": "Nikon D3200", "port": "usb:001,007"}
        ]
        ```
    *   **Error `error` Example:** `"Failed to list cameras due to gphoto2 error."`

---

2.  **`SELECT_CAMERA`**
    *   **Description:** Selects a camera for subsequent operations. If another camera is selected, it's deselected first.
    *   **Payload Example:**
        ```json
        {"model": "Canon EOS Rebel T6", "port": "usb:001,006"}
        ```
        *   *You can provide `model`, `port`, both, or neither (to select the first available camera).*
    *   **Success `data` Example:**
        ```json
        {"model": "Canon EOS Rebel T6", "port": "usb:001,006"} 
        ```
    *   **Error `error` Example:** `"Failed to select camera or camera not found."`

---

3.  **`DESELECT_CAMERA`**
    *   **Description:** Deselects the currently active camera.
    *   **Payload:** `{}` (empty)
    *   **Success `data` Example:**
        ```json
        {"message": "Camera deselected successfully."}
        ```
    *   **Error `error` Example:** (Usually succeeds, but if error during exit: `"Failed to properly deselect camera."`)

---

4.  **`GET_CONFIG`**
    *   **Description:** Retrieves camera configurations. Can fetch all configurations or a specific one.
    *   **Payload Example (Optional):**
        ```json
        {"config_name": "shutterspeed"} 
        ```
        *If `config_name` is omitted, all configurations are returned.*
    *   **Success `data` Example (All Configs):**
        ```json
        [
          {"name": "iso", "label": "ISO Speed", "value": "100", "type": "menu", "readonly": false, "options": ["100", "200", "400"]},
          {"name": "shutterspeed", "label": "Shutter Speed", "value": "1/125", "type": "menu", "readonly": false, "options": ["1/60", "1/125"]}
        ]
        ```
    *   **Success `data` Example (Specific Config):**
        ```json
        {"name": "iso", "label": "ISO Speed", "value": "100", "type": "menu", "readonly": false, "options": ["100", "200", "400"]}
        ```
    *   **Error `error` Example:** `"Config 'nonexistent_config' not found."` or `"No camera selected."`

---

5.  **`SET_CONFIG`**
    *   **Description:** Sets a specific camera configuration value.
    *   **Payload Example:**
        ```json
        {"config_name": "iso", "value": "200"}
        ```
    *   **Success `data` Example (Returns the updated config entry):**
        ```json
        {"name": "iso", "label": "ISO Speed", "value": "200", "type": "menu", "readonly": false, "options": ["100", "200", "400"]}
        ```
    *   **Error `error` Example:** `"Failed to set config 'iso'. Value 'invalid_value' not in options."`

---

6.  **`CAPTURE_IMAGE`**
    *   **Description:** Captures a still image.
    *   **Payload Example (Optional):**
        ```json
        {"download": true} 
        ```
        *`download` defaults to `true`. If `false`, image is captured on camera memory but not downloaded to server.*
    *   **Success `data` Example:**
        ```json
        {
          "message": "Image captured and downloaded.", 
          "file_path": "captures/image_Canon_EOS_Rebel_T6_20231027-143000.jpg"
        }
        ```
    *   **Error `error` Example:** `"Failed to capture image."`

---

7.  **`START_LIVEVIEW`**
    *   **Description:** Initiates a liveview stream from the camera. Frames are sent as separate WebSocket messages.
    *   **Payload:** `{}` (empty)
    *   **Success `data` Example (Acknowledgement):**
        ```json
        {"message": "Liveview stream initiated."}
        ```
    *   **Liveview Frame Message (Server-sent, not a direct response to `START_LIVEVIEW`):**
        ```json
        {
          "action": "liveview_frame", 
          "frame": "base64-encoded-jpeg-data...", 
          "mimetype": "image/jpeg"
        }
        ```
    *   **Error `error` Example:** `"Liveview is already active for another client."` or `"Camera does not support preview."`

---

8.  **`STOP_LIVEVIEW`**
    *   **Description:** Stops the current liveview stream.
    *   **Payload:** `{}` (empty)
    *   **Success `data` Example:**
        ```json
        {"message": "Liveview stream stopped."}
        ```
    *   **Error `error` Example:** (Usually succeeds if a stream was active)

---

9.  **`GET_LIGHT_STATES`**
    *   **Description:** Retrieves the current states of all configured lights and GPIO availability.
    *   **Payload:** `{}` (empty)
    *   **Success `data` Example:**
        ```json
        {
          "states": {
            "lights_top": false, 
            "light_front": true,
            "light_left": false 
            // ... and so on for all lights
          },
          "gpio_available": true 
        }
        ```
    *   **Error `error` Example:** (This command itself rarely fails, but `gpio_available` indicates hardware status)

---

10. **`SET_LIGHT_STATE`**
    *   **Description:** Sets the state (on/off) of a specific light.
    *   **Payload Example:**
        ```json
        {"light_name": "light_front", "state": true}
        ```
    *   **Success `data` Example:**
        ```json
        {
          "light_name": "light_front",
          "new_state": true,
          "gpio_available": true,
          "message": "Light 'light_front' turned ON." 
        }
        ```
    *   **Error `error` Example:** `"Invalid light name: non_existent_light."` or `"GPIO error setting light 'light_front'."`

---

11. **`CAPTURE_PHOTOMETRIC_SET`**
    *   **Description:** Initiates a photometric stereo capture sequence. The server will send `PHOTOMETRIC_PROGRESS` messages during the sequence and a final `CAPTURE_PHOTOMETRIC_SET` response upon completion or failure.
    *   **Payload Example:**
        ```json
        {
          "light_sequence": ["light_front_left", "light_front_right", "lights_top"],
          "set_name_prefix": "my_object_scan" 
        }
        ```
        *`set_name_prefix` is optional.*
    *   **Success `data` Example (Final response):**
        ```json
        {
          "message": "Photometric sequence completed successfully.",
          "set_folder": "photometric_sets/my_object_scan_20231027-150000",
          "image_count": 3,
          "images_captured": [
            "photometric_sets/my_object_scan_20231027-150000/image_01_light_front_left.jpg",
            "photometric_sets/my_object_scan_20231027-150000/image_02_light_front_right.jpg",
            "photometric_sets/my_object_scan_20231027-150000/image_03_lights_top.jpg"
          ]
        }
        ```
    *   **Error `error` Example (Final response):** `"Photometric sequence failed: Failed to capture image for light: light_front_left."` (The `data` field might still contain partial results like `set_folder` and `images_captured` up to the point of failure).

---

12. **`PHOTOMETRIC_PROGRESS`** (Server-Sent Event)
    *   **Description:** Sent by the server during a `CAPTURE_PHOTOMETRIC_SET` sequence to update the client on progress.
    *   **`data` Structure:**
        ```json
        {
          "status": "processing_light_1_of_3", // e.g., "error_processing_light_1"
          "current_light": "light_front_left", // Optional, name of the light being processed
          "set_folder": "photometric_sets/my_object_scan_20231027-150000",
          "error_detail": null // Or an error message if this step failed
        }
        ```
    *   *This message has `action: "photometric_progress"` and `success: true/false` depending on the step.*

---

13. **`LIST_IMAGE_SETS`**
    *   **Description:** Lists all available photometric image sets.
    *   **Payload:** `{}` (empty)
    *   **Success `data` Example:**
        ```json
        [
          {"name": "my_object_scan_20231027-150000"},
          {"name": "photometric_set_20231026-110000"}
        ]
        ```
        *Sorted by name, potentially newest first if names are timestamped.*
    *   **Error `error` Example:** `"Error listing image sets."`

---

14. **`GET_IMAGE_SET_CONTENTS`**
    *   **Description:** Retrieves the list of image files within a specific photometric set.
    *   **Payload Example:**
        ```json
        {"set_name": "my_object_scan_20231027-150000"}
        ```
    *   **Success `data` Example:**
        ```json
        [
          {"filename": "image_01_light_front_left.jpg", "path": "captures/photometric_sets/my_object_scan_20231027-150000/image_01_light_front_left.jpg"},
          {"filename": "image_02_light_front_right.jpg", "path": "captures/photometric_sets/my_object_scan_20231027-150000/image_02_light_front_right.jpg"}
        ]
        ```
        *`path` is relative to the server's working directory.*
    *   **Error `error` Example:** `"Set 'non_existent_set' not found or empty."`

---

15. **`DELETE_IMAGE_SET`**
    *   **Description:** Deletes an entire photometric image set directory.
    *   **Payload Example:**
        ```json
        {"set_name": "my_object_scan_20231027-150000"}
        ```
    *   **Success `data` Example:**
        ```json
        {"message": "Set 'my_object_scan_20231027-150000' deleted successfully."}
        ```
    *   **Error `error` Example:** `"Failed to delete set 'my_object_scan_20231027-150000'. It might not exist or an error occurred."`

---

16. **`GET_IMAGE_DATA`**
    *   **Description:** Retrieves the Base64 encoded data and mimetype for a specific image file.
    *   **Payload Example:**
        ```json
        {"image_path": "captures/photometric_sets/my_object_scan_20231027-150000/image_01_light_front_left.jpg"}
        ```
        *`image_path` must be relative to the server's working directory and within the allowed `CAPTURES_BASE_DIR`.*
    *   **Success `data` Example:**
        ```json
        {
          "filename": "image_01_light_front_left.jpg",
          "image_b64": "base64-encoded-image-data...",
          "mimetype": "image/jpeg"
        }
        ```
    *   **Error `error` Example:** `"Image not found or access denied: path/to/image.jpg"`

---

## Frontend Integration Example (Conceptual)

A client application (e.g., built with Next.js and TypeScript) can interact with this API using a WebSocket connection. The provided example files:

*   `services/WebSocketService.ts`: A class that encapsulates WebSocket connection management, sending commands (with `request_id` handling), and managing message listeners.
*   `components/Liveview.tsx`: A React component that uses `WebSocketService` to start/stop liveview and displays the received Base64 frames.
*   `components/PhotometricStereoControl.tsx`: A React component for initiating photometric stereo sequences, displaying progress updates, and showing results.
*   `components/ImageGallery.tsx`: A React component for listing image sets, viewing their contents, displaying individual images (by fetching their Base64 data), and deleting sets.

These components demonstrate how to structure a client to consume the different actions and data formats provided by the WebSocket API.

## Troubleshooting

*   **Camera Not Detected:**
    *   Ensure your camera is connected and powered on.
    *   Run `gphoto2 --auto-detect` from the command line. If it's not listed, `libgphoto2` may not support your camera or there might be a connection issue (USB cable, port).
    *   Check `dmesg` (Linux) for USB connection events or errors.
    *   Ensure no other application (like a desktop photo manager) is currently accessing the camera.
    *   On Raspberry Pi, ensure sufficient power is supplied to the USB ports, especially for DSLRs. An externally powered USB hub might be necessary.
*   **libgphoto2 Errors:**
    *   Many `gphoto2` operations can fail if the camera enters a strange state. Try power cycling the camera.
    *   "Could not claim the USB device": Another process might be using the camera.
    *   "Camera is already capturing": This can happen if a previous capture/preview didn't exit cleanly.
*   **FastAPI Server Issues:**
    *   **Port Conflicts:** If `uvicorn` fails to start, ensure port `8000` (or your configured port) is not in use by another application.
    *   **Python Environment:** Make sure all dependencies from `requirements.txt` are installed in the active Python virtual environment.
*   **GPIO Issues (Raspberry Pi):**
    *   **Permissions:** The user running the Python script might need GPIO access permissions (often part of the `gpio` group).
    *   **Pin Numbering:** Ensure you're using the correct pin numbering scheme (BCM is used in this project) that matches your physical connections.
    *   **RPi.GPIO Not Found:** The server will log a warning and stub GPIO functions. Light control will not work. Ensure `RPi.GPIO` is installed correctly in your Python environment.
*   **WebSocket Connection Problems:**
    *   Verify the WebSocket URL (`ws://host:port/ws`) is correct in your client.
    *   Check server logs for connection attempts and errors.
    *   Ensure no firewalls are blocking the WebSocket connection.

## Contributing

Contributions are welcome! Please fork the repository, create a feature branch, and submit a pull request with your changes. Ensure code is well-commented and, if applicable, new API actions are documented.

## License

This project is licensed under the MIT License. See the `LICENSE` file for details.

