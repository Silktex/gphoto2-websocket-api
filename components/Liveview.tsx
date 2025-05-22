import React, { useState, useEffect, useCallback } from 'react';
import { WebSocketService } from '../services/WebSocketService'; // Adjust path as needed

interface LiveviewProps {
  webSocketService: WebSocketService;
}

const Liveview: React.FC<LiveviewProps> = ({ webSocketService }) => {
  const [isLiveviewActive, setIsLiveviewActive] = useState<boolean>(false);
  const [currentFrame, setCurrentFrame] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [mimetype, setMimetype] = useState<string>('image/jpeg'); // Default mimetype

  const handleMessage = useCallback((message: any) => {
    if (typeof message !== 'object' || message === null) {
        // If it's not an object (e.g. binary data for raw preview), ignore for liveview
        return;
    }
    // console.log('Liveview received message:', message);
    if (message.action === 'liveview_frame' && message.frame) {
      setCurrentFrame(message.frame);
      if (message.mimetype) {
        setMimetype(message.mimetype);
      }
      setError(null); // Clear previous errors on new frame
    } else if (message.action === 'stop_liveview' && !message.success) {
      // Handle cases where server explicitly sends a stop_liveview with an error (e.g. camera disconnected)
      console.warn('Liveview stopped by server due to error:', message.error);
      setError(message.error || 'Liveview stopped by server.');
      setIsLiveviewActive(false);
      setCurrentFrame(null);
    } else if (message.action === ActionType.START_LIVEVIEW && !message.success && message.request_id) {
      // This case might be handled by sendCommand's catch block, but as a fallback:
      // If a start_liveview action response indicates failure (e.g. already active for another client)
      // And it was not caught by the sendCommand promise (e.g. if not using expectsResponse properly)
      // This is less likely if sendCommand is used as intended.
      setError(message.error || 'Failed to start liveview (async error).');
      setIsLiveviewActive(false);
    }
  }, []);

  useEffect(() => {
    if (!webSocketService) return;

    webSocketService.addMessageListener(handleMessage);

    // Cleanup function
    return () => {
      webSocketService.removeMessageListener(handleMessage);
      if (isLiveviewActive) { // Use the state at the time of cleanup setup
        console.log('Liveview component unmounting, ensuring liveview is stopped.');
        webSocketService.sendCommand('stop_liveview', {}, false)
          .catch(err => console.error('Error stopping liveview on unmount:', err));
      }
    };
  }, [webSocketService, handleMessage, isLiveviewActive]); // isLiveviewActive in dependency array for the cleanup function's conditional logic

  const toggleLiveview = async () => {
    setError(null); // Clear previous errors

    if (isLiveviewActive) {
      try {
        // Send command, don't necessarily wait for a response if the server doesn't send one for stop.
        // The server's stop_liveview handler sends a confirmation, so we can use expectsResponse: true
        await webSocketService.sendCommand('stop_liveview', {}, true);
        console.log('Liveview stop command sent.');
        setIsLiveviewActive(false);
        setCurrentFrame(null); // Clear frame on stop
      } catch (err: any) {
        console.error('Error stopping liveview:', err);
        setError(err.message || 'Failed to stop liveview.');
        // We still set active to false, as the intention was to stop.
        // The server might have stopped it anyway or the connection might be down.
        setIsLiveviewActive(false);
        setCurrentFrame(null);
      }
    } else {
      try {
        // The server's start_liveview sends an ack, so expectsResponse should be true.
        const response = await webSocketService.sendCommand('start_liveview', {}, true);
        if (response && response.message === "Liveview stream initiated.") { // Check specific success data if available
          console.log('Liveview start command acknowledged by server.');
          setIsLiveviewActive(true);
          setCurrentFrame(null); // Clear any old frame, wait for new ones
        } else {
          // This case might occur if the server sends success: true but without the expected data.
          // Or if the server's success response structure changes.
          console.warn('Liveview start command sent, but response was not as expected:', response);
          setError('Liveview started but acknowledgment was unexpected.');
          setIsLiveviewActive(true); // Assume it started if command didn't throw
        }
      } catch (err: any) {
        console.error('Error starting liveview:', err);
        setError(err.message || 'Failed to start liveview. Check server logs.');
        setIsLiveviewActive(false);
      }
    }
  };

  // Server action enum for type safety, if shared or defined in a common place
  // For now, using string literals as per WebSocketService.ts and server
  const ActionType = {
    START_LIVEVIEW: "start_liveview",
    STOP_LIVEVIEW: "stop_liveview",
    LIVEVIEW_FRAME: "liveview_frame",
  };

  return (
    <div style={{ border: '1px solid #ccc', padding: '10px', margin: '10px' }}>
      <h4>Camera Liveview</h4>
      <button onClick={toggleLiveview} disabled={!webSocketService || !webSocketService.isConnected}>
        {isLiveviewActive ? 'Stop Liveview' : 'Start Liveview'}
      </button>
      {!webSocketService?.isConnected && <p style={{ color: 'orange' }}>WebSocket disconnected. Cannot control liveview.</p>}
      {error && <p style={{ color: 'red' }}>Error: {error}</p>}
      <div style={{ marginTop: '10px', minHeight: '240px', border: '1px solid #eee', background: '#f0f0f0' }}>
        {isLiveviewActive ? (
          currentFrame ? (
            <img 
              src={`data:${mimetype};base64,${currentFrame}`} 
              alt="Liveview Stream" 
              style={{ maxWidth: '100%', maxHeight: '480px' }} 
            />
          ) : (
            <p>Waiting for frames...</p>
          )
        ) : (
          <p>Liveview is Off.</p>
        )}
      </div>
    </div>
  );
};

export default Liveview;
```
