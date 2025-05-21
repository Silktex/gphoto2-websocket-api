GPhoto2 WebSocket Server  ***WIP*** Don't use this AI Slop. I will be refining it.
This project provides a WebSocket server for controlling cameras using the libgphoto2 library via the gphoto2 Python bindings. It allows remote clients to list connected cameras, select a camera, capture images, download images, and manage camera configurations over a WebSocket connection.
Features

Camera Detection: Lists all connected cameras with their names, ports, and supported functions (e.g., image capture, configuration) on startup.
WebSocket API: Supports commands to:
List cameras (list_cameras)
Select a camera by port (select_camera)
Capture images (capture_image)
Download images as base64-encoded data (download_last_image)
Get and set camera configurations (get_config, set_config)


Authentication: Requires a token for client connections, configurable via environment variable.
Input Validation: Uses Pydantic to validate WebSocket command payloads.
Logging: Detailed logs for debugging, with configurable log levels.
Temporary File Handling: Safely manages image downloads using temporary files.
Cross-Version Compatibility: Handles tuple-based camera detection for older gphoto2 versions and simplified camera initialization.

Requirements

Python: 3.6+
Operating System: Linux (tested on Raspberry Pi with Debian-based systems)
Dependencies:
gphoto2: Python bindings for libgphoto2
websockets: Version 15.0.1
pydantic: For input validation
libgphoto2: System library for camera control


Hardware: A compatible camera connected via USB (e.g., Sony Alpha-A7r III)

Installation

Clone the Repository:
git clone https://github.com/yourusername/gphoto2-websocket-server.git
cd gphoto2-websocket-server


Install System Dependencies:On Debian-based systems (e.g., Raspberry Pi):
sudo apt-get update
sudo apt-get install libgphoto2-dev


Install Python Dependencies:
pip install gphoto2 websockets==15.0.1 pydantic


Verify Camera Detection:Ensure your camera is connected and detected:
gphoto2 --auto-detect



Usage

Set Environment Variables (optional):

WS_HOST: Host address (default: localhost)
WS_PORT: Port (default: 8765)
LOG_LEVEL: Logging level (DEBUG, INFO, etc.; default: INFO)
API_TOKEN: Authentication token (default: random UUID)Example:

export API_TOKEN=mysecrettoken
export LOG_LEVEL=DEBUG


Run the Server:
python 2gphoto-websocket.py

The server starts at ws://localhost:8765 and logs detected cameras and their supported functions.

Connect a WebSocket Client:

Use a WebSocket client (e.g., wscat, Python websockets library).
Send an authentication message first:{"token": "mysecrettoken"}


Send commands (see below).



WebSocket Commands
All commands are JSON messages with a command field and an optional payload field. Example format:
{"command": "list_cameras", "payload": {}}

Supported Commands

list_cameras:

Payload: Empty ({})
Response: List of cameras with name, port, and index
Example:{"command": "list_cameras", "payload": {}}

Response:{
  "status": "ok",
  "cameras": [
    {"name": "Sony Alpha-A7r III (PC Control)", "port": "usb:001,003", "index": 0}
  ]
}




select_camera:

Payload: {"port": "usb:001,003"}
Response: Confirmation of camera selection
Example:{"command": "select_camera", "payload": {"port": "usb:001,003"}}

Response:{"status": "ok", "message": "Camera on port usb:001,003 selected"}




capture_image:

Payload: Empty ({})
Response: Camera filepath of the captured image
Example:{"command": "capture_image", "payload": {}}

Response:{
  "status": "ok",
  "message": "Image captured successfully",
  "camera_filepath": "/store_00010001/DCIM/100SONY/IMG_0001.JPG"
}




download_last_image:

Payload: {"camera_filepath": "/store_00010001/DCIM/100SONY/IMG_0001.JPG"}
Response: Base64-encoded image data
Example:{"command": "download_last_image", "payload": {"camera_filepath": "/store_00010001/DCIM/100SONY/IMG_0001.JPG"}}

Response:{
  "status": "ok",
  "message": "Image downloaded successfully",
  "filename": "IMG_0001.JPG",
  "mimetype": "image/jpeg",
  "image_b64": "..."
}




get_config:

Payload: {"name": "iso"} or empty ({})
Response: Single config or list of all configs
Example:{"command": "get_config", "payload": {"name": "iso"}}

Response:{
  "status": "ok",
  "config_name": "iso",
  "label": "ISO Sensitivity",
  "type": "menu",
  "value": "100",
  "readonly": false,
  "choices": ["100", "200", "400"]
}




set_config:

Payload: {"name": "iso", "value": "200"}
Response: Updated config details
Example:{"command": "set_config", "payload": {"name": "iso", "value": "200"}}

Response:{
  "status": "ok",
  "message": "Config iso set to 200",
  "config_name": "iso",
  "label": "ISO Sensitivity",
  "type": "menu",
  "value": "200",
  "readonly": false,
  "choices": ["100", "200", "400"]
}





Example WebSocket Client
Below is a simple Python client to interact with the server:
import asyncio
import websockets
import json

async def client():
    uri = "ws://localhost:8765"
    async with websockets.connect(uri) as websocket:
        # Authenticate
        await websocket.send(json.dumps({"token": "mysecrettoken"}))
        
        # List cameras
        await websocket.send(json.dumps({"command": "list_cameras", "payload": {}}))
        response = await websocket.recv()
        print("Cameras:", response)
        
        # Select camera
        await websocket.send(json.dumps({"command": "select_camera", "payload": {"port": "usb:001,003"}}))
        response = await websocket.recv()
        print("Select camera:", response)
        
        # Capture image
        await websocket.send(json.dumps({"command": "capture_image", "payload": {}}))
        response = await websocket.recv()
        print("Capture image:", response)

asyncio.run(client())

Troubleshooting

Camera Not Detected:

Run gphoto2 --auto-detect to verify camera connectivity.
Ensure the camera is powered on and not in use by another application.
Check libgphoto2 compatibility:sudo apt-get install --reinstall libgphoto2-dev




Camera Initialization Errors:

If abilities cannot be retrieved, verify gphoto2 version:pip show gphoto2
gphoto2 --version


Update if needed:pip install --upgrade gphoto2




WebSocket Connection Issues:

Ensure port 8765 is free:sudo netstat -tuln | grep 8765


Verify websockets version:pip show websockets




Authentication Errors:

Check server logs for the API_TOKEN (a UUID if not set).
Ensure the client sends the correct token in the first message.



Contributing
Contributions are welcome! To contribute:

Fork the repository.
Create a feature branch (git checkout -b feature/your-feature).
Commit changes (git commit -m "Add your feature").
Push to the branch (git push origin feature/your-feature).
Open a pull request.

Please include tests and update documentation as needed.
License
This project is licensed under the MIT License. See the LICENSE file for details.
Acknowledgments

Built with libgphoto2 and python-gphoto2.
Uses websockets for WebSocket communication.
Input validation powered by Pydantic.

