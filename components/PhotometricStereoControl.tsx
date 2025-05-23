import React, { useState, useEffect, useCallback } from 'react';
import { WebSocketService } from '../services/WebSocketService'; // Adjust path as needed

interface PhotometricStereoControlProps {
  webSocketService: WebSocketService;
}

// Define server message structures for clarity (mirroring Pydantic models)
interface PhotometricProgressData {
  status: string;
  current_light?: string;
  set_folder: string;
  error_detail?: string;
}

interface PhotometricSetResponseData {
  message: string;
  set_folder: string;
  image_count?: number;
  images_captured?: string[];
}

interface ServerMessage {
  action: string;
  success: boolean;
  data?: PhotometricProgressData | PhotometricSetResponseData; // Can be one of these for relevant actions
  error?: string;
  request_id?: string;
}

const DEFAULT_LIGHT_SEQUENCE = [
  "lights_top", "light_front", "light_front_left", "light_left",
  "light_rear_left", "light_rear", "light_rear_right", "light_right",
  "light_front_right"
];

const PhotometricStereoControl: React.FC<PhotometricStereoControlProps> = ({ webSocketService }) => {
  const [setNamePrefix, setSetNamePrefix] = useState<string>('');
  const [lightSequence, setLightSequence] = useState<string[]>(DEFAULT_LIGHT_SEQUENCE);
  const [isSequenceRunning, setIsSequenceRunning] = useState<boolean>(false);
  const [progressMessage, setProgressMessage] = useState<string | null>(null);
  const [lastSetFolder, setLastSetFolder] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [capturedFilePaths, setCapturedFilePaths] = useState<string[]>([]);

  const handleServerMessages = useCallback((message: ServerMessage) => {
    // console.log('PhotometricStereo received message:', message);
    if (message.action === 'photometric_progress') {
      const progressData = message.data as PhotometricProgressData;
      if (message.success && progressData) {
        setProgressMessage(`Progress: ${progressData.status} ${progressData.current_light ? '- Light: ' + progressData.current_light : ''}`);
        if (progressData.set_folder) {
            setLastSetFolder(progressData.set_folder); // Update folder path as soon as known
        }
      } else if (!message.success && progressData) {
        const errorMsg = `Error during sequence: ${progressData.error_detail || message.error || 'Unknown progress error'}`;
        setError(errorMsg);
        setProgressMessage(errorMsg);
        // Consider if a progress error should stop the sequence indication on client side
        // setIsSequenceRunning(false); // This might be too soon, server sends final status
      }
    }
    // The final 'capture_photometric_set' response is handled by the sendCommand promise
  }, []);

  useEffect(() => {
    if (!webSocketService) return;

    webSocketService.addMessageListener(handleServerMessages);

    return () => {
      webSocketService.removeMessageListener(handleServerMessages);
      // Note: If the component unmounts while a sequence is running,
      // the server will continue the sequence unless explicitly told to stop.
      // A 'cancel_photometric_set' command would be needed for that.
      // For now, we just clean up the listener.
    };
  }, [webSocketService, handleServerMessages]);

  const handleStartSequence = async () => {
    if (!webSocketService || !webSocketService.isConnected) {
      setError("WebSocket not connected.");
      return;
    }

    setIsSequenceRunning(true);
    setProgressMessage("Initializing photometric sequence...");
    setError(null);
    setLastSetFolder(null);
    setCapturedFilePaths([]);

    try {
      const response: ServerMessage = await webSocketService.sendCommand(
        'capture_photometric_set',
        { set_name_prefix: setNamePrefix, light_sequence: lightSequence },
        true // Expects a final response
      );

      // The 'response' here is the final message from the server for CAPTURE_PHOTOMETRIC_SET
      const responseData = response.data as PhotometricSetResponseData;

      if (response.success && responseData) {
        setProgressMessage(responseData.message || "Sequence completed successfully.");
        setLastSetFolder(responseData.set_folder);
        setCapturedFilePaths(responseData.images_captured || []);
        console.log("Photometric sequence completed:", responseData);
      } else {
        const errorMsg = response.error || (responseData ? responseData.message : 'Unknown error completing sequence.');
        setError(errorMsg);
        setProgressMessage(`Sequence failed: ${errorMsg}`);
        if (responseData?.set_folder) setLastSetFolder(responseData.set_folder); // Folder might exist even on failure
        if (responseData?.images_captured) setCapturedFilePaths(responseData.images_captured); // Some images might have been captured
        console.error("Photometric sequence failed:", response);
      }
    } catch (err: any) {
      console.error('Error sending CAPTURE_PHOTOMETRIC_SET command or processing its response:', err);
      setError(err.message || 'Failed to start or complete sequence due to communication error.');
      setProgressMessage("Sequence failed or was interrupted by an error.");
    } finally {
      setIsSequenceRunning(false);
      // Progress message is already set by success/error handlers, no need to clear here unless desired
    }
  };
  
  // Basic UI to edit light sequence (example: comma-separated string)
  const handleLightSequenceChange = (event: React.ChangeEvent<HTMLTextAreaElement>) => {
    const newSequence = event.target.value.split(',').map(s => s.trim()).filter(s => s);
    setLightSequence(newSequence);
  };


  return (
    <div style={{ border: '1px solid #ccc', padding: '10px', margin: '10px' }}>
      <h4>Photometric Stereo Control</h4>
      
      <div>
        <label htmlFor="setNamePrefix">Set Name Prefix (Optional): </label>
        <input
          type="text"
          id="setNamePrefix"
          value={setNamePrefix}
          onChange={(e) => setSetNamePrefix(e.target.value)}
          disabled={isSequenceRunning}
          style={{ marginRight: '10px' }}
        />
      </div>

      <div style={{ marginTop: '10px' }}>
        <label htmlFor="lightSequence">Light Sequence (comma-separated):</label>
        <textarea
          id="lightSequence"
          value={lightSequence.join(', ')}
          onChange={handleLightSequenceChange}
          disabled={isSequenceRunning}
          rows={3}
          style={{ width: '100%', boxSizing: 'border-box', fontFamily: 'monospace' }}
        />
        <small>Default: {DEFAULT_LIGHT_SEQUENCE.join(', ')}</small>
      </div>

      <button 
        onClick={handleStartSequence} 
        disabled={isSequenceRunning || !webSocketService || !webSocketService.isConnected}
        style={{ marginTop: '10px' }}
      >
        {isSequenceRunning ? 'Sequence Running...' : 'Start Photometric Sequence'}
      </button>

      {!webSocketService?.isConnected && <p style={{ color: 'orange', marginTop: '5px' }}>WebSocket disconnected. Cannot start sequence.</p>}
      
      {progressMessage && <p style={{ marginTop: '10px', fontStyle: 'italic' }}>Status: {progressMessage}</p>}
      {error && <p style={{ color: 'red', marginTop: '5px' }}>Error: {error}</p>}

      {lastSetFolder && !isSequenceRunning && (
        <div style={{ marginTop: '10px', borderTop: '1px dashed #eee', paddingTop: '10px' }}>
          <h5>Last Sequence Output:</h5>
          <p><strong>Set Folder:</strong> {lastSetFolder}</p>
          {capturedFilePaths.length > 0 && (
            <div>
              <strong>Captured Images ({capturedFilePaths.length}):</strong>
              <ul style={{ maxHeight: '150px', overflowY: 'auto', fontSize: '0.9em', border: '1px solid #f0f0f0', padding: '5px' }}>
                {capturedFilePaths.map((path, index) => (
                  <li key={index}>{path}</li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </div>
  );
};

export default PhotometricStereoControl;
```
