// services/WebSocketService.ts

interface CommandResponsePromise {
  resolve: (value: any) => void;
  reject: (reason?: any) => void;
  timeoutId?: NodeJS.Timeout;
}

export class WebSocketService {
  private ws: WebSocket | null = null;
  private serverUrl: string;
  private apiToken: string | null; // Can be null if auth is not required initially

  public isConnected: boolean = false;
  private messageListeners: Array<(message: any) => void> = [];
  private commandResponsePromises: Map<string, CommandResponsePromise> = new Map();
  private requestIdCounter: number = 0;

  constructor(serverUrl: string, apiToken: string | null = null) {
    this.serverUrl = serverUrl;
    this.apiToken = apiToken; // Store the token
    if (!this.serverUrl) {
      throw new Error("WebSocket server URL is required.");
    }
  }

  private generateRequestId(): string {
    this.requestIdCounter += 1;
    return `req-${Date.now()}-${this.requestIdCounter}`;
  }

  public connect(): Promise<void> {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.isConnected = true;
      return Promise.resolve();
    }
    if (this.ws && this.ws.readyState === WebSocket.CONNECTING) {
      // If already connecting, return a promise that resolves/rejects with the current attempt
      return new Promise((resolve, reject) => {
        const checkInterval = setInterval(() => {
          if (this.isConnected) {
            clearInterval(checkInterval);
            resolve();
          }
          if (this.ws && (this.ws.readyState === WebSocket.CLOSING || this.ws.readyState === WebSocket.CLOSED)) {
            clearInterval(checkInterval);
            reject(new Error("WebSocket connection failed during previous attempt."));
          }
        }, 100);
      });
    }

    return new Promise((resolve, reject) => {
      this.ws = new WebSocket(this.serverUrl);

      this.ws.onopen = () => {
        console.log("WebSocket connected.");
        // Send authentication message if token is provided
        // The server is not expecting a token based on its current implementation,
        // but this is where it would go if it did.
        // For now, we can send a generic "auth" message if needed, or nothing.
        // Let's assume for now the server does not require an explicit auth message beyond connection.
        // If it did:
        // if (this.apiToken) {
        //   this.ws?.send(JSON.stringify({ token: this.apiToken, request_id: "auth" }));
        // }
        this.isConnected = true;
        resolve();
      };

      this.ws.onmessage = (event) => {
        try {
          const message = JSON.parse(event.data as string);
          console.debug("WebSocket message received:", message);

          if (message.request_id && this.commandResponsePromises.has(message.request_id)) {
            const promiseHandlers = this.commandResponsePromises.get(message.request_id);
            if (promiseHandlers) {
              if (promiseHandlers.timeoutId) {
                clearTimeout(promiseHandlers.timeoutId);
              }
              if (message.success) {
                promiseHandlers.resolve(message.data);
              } else {
                promiseHandlers.reject(new Error(message.error || "Command failed"));
              }
              this.commandResponsePromises.delete(message.request_id);
            }
          } else if (message.action === 'get_preview' && event.data instanceof Blob) {
            // Handle binary preview data (if server sends it this way for previews)
            // This example assumes preview frames might come as binary messages not tied to request_id
            this.messageListeners.forEach(listener => listener(event.data));
          } else if (event.data instanceof ArrayBuffer) {
             // Generic ArrayBuffer listener (e.g. for preview frames sent as bytes)
            this.messageListeners.forEach(listener => listener(event.data));
          }
          else {
            // For messages not tied to a specific command request_id (e.g., broadcasts, async updates)
            // Or if it's a response to a command that didn't expect a specific response via promise (rare)
            this.messageListeners.forEach(listener => listener(message));
          }
        } catch (error) {
          console.error("Error parsing WebSocket message or in listener:", error);
          // Notify generic listeners about the raw error if parsing failed
          this.messageListeners.forEach(listener => listener({ error: "Failed to parse message", rawData: event.data }));
        }
      };

      this.ws.onclose = (event) => {
        console.log(`WebSocket disconnected: ${event.code} ${event.reason}`);
        this.isConnected = false;
        const disconnectError = new Error(`WebSocket disconnected: ${event.code} ${event.reason || "Connection closed"}`);
        
        this.commandResponsePromises.forEach((promise, key) => {
          if (promise.timeoutId) clearTimeout(promise.timeoutId);
          promise.reject(disconnectError);
        });
        this.commandResponsePromises.clear();
        // Notify generic listeners about the disconnection
        this.messageListeners.forEach(listener => listener({ type: "disconnect", reason: event.reason, code: event.code }));
      };

      this.ws.onerror = (event) => {
        // Type of 'event' for onerror is just Event, not ErrorEvent in all browser contexts for WebSocket
        // For more detailed error, one might need to investigate specific browser behavior or rely on onclose.
        console.error("WebSocket error:", event);
        const error = new Error("WebSocket error occurred. See console for details.");
        
        this.commandResponsePromises.forEach((promise, key) => {
          if (promise.timeoutId) clearTimeout(promise.timeoutId);
          promise.reject(error);
        });
        this.commandResponsePromises.clear();
        
        // If the connection was never established, reject the connect promise
        if (!this.isConnected) {
          reject(error);
        }
        // Notify generic listeners
        this.messageListeners.forEach(listener => listener({ type: "error", errorEvent: event }));
        this.isConnected = false; // Ensure isConnected is false on error
      };
    });
  }

  public disconnect(): void {
    if (this.ws) {
      console.log("Disconnecting WebSocket...");
      this.ws.close();
      // onclose handler will manage isConnected and cleanup
    }
  }

  public sendCommand(action: string, payload: any = {}, expectsResponse: boolean = true, timeout: number = 10000): Promise<any> {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
      console.error("WebSocket not connected or not open. State:", this.ws?.readyState);
      return Promise.reject(new Error("WebSocket not connected."));
    }

    const requestId = this.generateRequestId();
    const message = {
      action: action, // Server expects "action" not "command"
      payload: payload,
      request_id: requestId,
    };

    try {
      this.ws.send(JSON.stringify(message));
      console.debug("WebSocket command sent:", message);
    } catch (error) {
      console.error("Error sending WebSocket command:", error);
      return Promise.reject(error);
    }

    if (expectsResponse) {
      return new Promise((resolve, reject) => {
        const timeoutId = setTimeout(() => {
          this.commandResponsePromises.delete(requestId);
          reject(new Error(`Command '${action}' with request_id '${requestId}' timed out after ${timeout}ms`));
        }, timeout);

        this.commandResponsePromises.set(requestId, { resolve, reject, timeoutId });
      });
    } else {
      return Promise.resolve({ message: "Command sent, no response expected.", request_id: requestId });
    }
  }

  public addMessageListener(listener: (message: any) => void): void {
    if (!this.messageListeners.includes(listener)) {
      this.messageListeners.push(listener);
    }
  }

  public removeMessageListener(listener: (message: any) => void): void {
    this.messageListeners = this.messageListeners.filter(l => l !== listener);
  }

  // Specific helper for preview frames if they are sent as raw binary data
  public addPreviewFrameListener(listener: (frame: ArrayBuffer) => void): () => void {
    const internalListener = (data: any) => {
      if (data instanceof ArrayBuffer) {
        listener(data);
      }
      // Add Blob handling if server sends previews as Blobs (less common for raw frames)
      // else if (data instanceof Blob) {
      //   data.arrayBuffer().then(buffer => listener(buffer)).catch(console.error);
      // }
    };
    this.addMessageListener(internalListener);
    return () => this.removeMessageListener(internalListener); // Return a function to easily remove this specific listener
  }
}

// Example Usage (typically in a React context or another service):
/*
const WS_URL = process.env.NEXT_PUBLIC_WS_URL || 'ws://localhost:8000/ws';
const webSocketService = new WebSocketService(WS_URL);

webSocketService.connect()
  .then(() => {
    console.log("Successfully connected to WebSocket server.");
    // Example: Get list of cameras
    webSocketService.sendCommand('get_cameras', {})
      .then(response => console.log("Cameras:", response))
      .catch(error => console.error("Error getting cameras:", error));

    // Add a generic listener for any messages not handled by command promises (e.g. server broadcasts)
    const myListener = (message: any) => {
      console.log("Generic message listener received:", message);
    };
    webSocketService.addMessageListener(myListener);

    // Add a listener for preview frames (assuming they are ArrayBuffer)
    const previewSubscription = webSocketService.addPreviewFrameListener((frame) => {
      console.log('Preview frame received, size:', frame.byteLength);
      // Process the frame (e.g., display on a canvas)
    });


    // To stop listening to previews:
    // previewSubscription();

    // To remove the generic listener:
    // webSocketService.removeMessageListener(myListener);
  })
  .catch(error => {
    console.error("WebSocket connection failed:", error);
  });

// To disconnect:
// webSocketService.disconnect();
*/
```
