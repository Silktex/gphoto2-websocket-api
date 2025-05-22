import React, { useState, useEffect, useCallback } from 'react';
import { WebSocketService } from '../services/WebSocketService'; // Adjust path as needed

interface ImageGalleryProps {
  webSocketService: WebSocketService;
}

// Interfaces for data structures from the server (mirroring Pydantic models)
interface ImageSet {
  name: string;
}

interface ImageFile {
  filename: string;
  path: string; // Relative path as sent by server
}

interface SelectedImageFile extends ImageFile {
  b64Data?: string;
  mimetype?: string;
}

interface ImageDataResponse {
  filename: string;
  image_b64: string;
  mimetype: string;
}

const ImageGallery: React.FC<ImageGalleryProps> = ({ webSocketService }) => {
  const [imageSets, setImageSets] = useState<ImageSet[]>([]);
  const [selectedSet, setSelectedSet] = useState<string | null>(null);
  const [setContents, setSetContents] = useState<ImageFile[]>([]);
  const [selectedImage, setSelectedImage] = useState<SelectedImageFile | null>(null);

  const [isLoadingSets, setIsLoadingSets] = useState<boolean>(false);
  const [isLoadingContents, setIsLoadingContents] = useState<boolean>(false);
  const [isLoadingImage, setIsLoadingImage] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);

  const fetchImageSets = useCallback(async () => {
    if (!webSocketService || !webSocketService.isConnected) {
      setError("WebSocket not connected. Cannot fetch image sets.");
      return;
    }
    setIsLoadingSets(true);
    setError(null);
    try {
      const response = await webSocketService.sendCommand('list_image_sets', {}, true);
      setImageSets(response || []); // Assuming response is Array<{name: string}> or null/undefined
    } catch (err: any) {
      console.error("Error fetching image sets:", err);
      setError(err.message || "Failed to fetch image sets.");
      setImageSets([]); // Clear sets on error
    } finally {
      setIsLoadingSets(false);
    }
  }, [webSocketService]);

  useEffect(() => {
    if (webSocketService && webSocketService.isConnected) {
      fetchImageSets();
    }
    // Add a listener for WebSocket connection status changes if needed,
    // or ensure fetchImageSets is called when connection is (re-)established.
    // For simplicity, this effect runs when isConnected changes.
  }, [webSocketService, webSocketService?.isConnected, fetchImageSets]);


  const handleSelectSet = async (setName: string) => {
    if (!webSocketService || !webSocketService.isConnected) {
      setError("WebSocket not connected. Cannot fetch set contents.");
      return;
    }
    setSelectedSet(setName);
    setSetContents([]);
    setSelectedImage(null);
    setIsLoadingContents(true);
    setError(null);
    try {
      const response = await webSocketService.sendCommand('get_image_set_contents', { set_name: setName }, true);
      setSetContents(response || []); // Assuming response is Array<ImageFile>
    } catch (err: any)      console.error(`Error fetching contents for set ${setName}:`, err);
      setError(err.message || `Failed to fetch contents for set ${setName}.`);
      setSetContents([]); // Clear contents on error
    } finally {
      setIsLoadingContents(false);
    }
  };

  const handleDeleteSet = async (setName: string) => {
    if (!webSocketService || !webSocketService.isConnected) {
      setError("WebSocket not connected. Cannot delete set.");
      return;
    }
    if (window.confirm(`Are you sure you want to delete the image set "${setName}"? This action cannot be undone.`)) {
      setError(null);
      try {
        await webSocketService.sendCommand('delete_image_set', { set_name: setName }, true);
        // Refresh sets
        fetchImageSets();
        if (selectedSet === setName) {
          setSelectedSet(null);
          setSetContents([]);
          setSelectedImage(null);
        }
      } catch (err: any) {
        console.error(`Error deleting set ${setName}:`, err);
        setError(err.message || `Failed to delete set ${setName}.`);
      }
    }
  };

  const handleSelectImage = async (imageFile: ImageFile) => {
    if (!webSocketService || !webSocketService.isConnected) {
      setError("WebSocket not connected. Cannot fetch image data.");
      return;
    }
    setSelectedImage({ ...imageFile, b64Data: undefined, mimetype: undefined }); // Clear previous image data
    setIsLoadingImage(true);
    setError(null); // Clear general errors
    try {
      const response: ImageDataResponse = await webSocketService.sendCommand('get_image_data', { image_path: imageFile.path }, true);
      setSelectedImage({
        ...imageFile,
        b64Data: response.image_b64,
        mimetype: response.mimetype,
      });
    } catch (err: any) {
      console.error(`Error fetching data for image ${imageFile.filename}:`, err);
      // Keep selectedImage to show filename, but indicate error for image data
      setSelectedImage({ ...imageFile, b64Data: undefined }); 
      setError(`Failed to load image ${imageFile.filename}: ${err.message || "Unknown error"}`);
    } finally {
      setIsLoadingImage(false);
    }
  };

  const styles: { [key: string]: React.CSSProperties } = {
    container: { border: '1px solid #ccc', padding: '15px', margin: '10px', fontFamily: 'Arial, sans-serif' },
    setsList: { listStyle: 'none', padding: 0, maxHeight: '200px', overflowY: 'auto', border: '1px solid #eee' },
    setListItem: { padding: '8px', borderBottom: '1px solid #f0f0f0', cursor: 'pointer', display: 'flex', justifyContent: 'space-between', alignItems: 'center' },
    setListItemHover: { backgroundColor: '#f9f9f9' },
    selectedSetItem: { backgroundColor: '#e0e0e0' },
    deleteButton: { marginLeft: '10px', padding: '3px 8px', backgroundColor: '#ff4d4d', color: 'white', border: 'none', borderRadius: '3px', cursor: 'pointer' },
    contentsList: { display: 'flex', flexWrap: 'wrap', gap: '10px', marginTop: '10px', padding: '10px', border: '1px solid #eee' },
    imageThumbnail: { border: '1px solid #ddd', padding: '5px', cursor: 'pointer', textAlign: 'center', width: '120px' },
    imageThumbnailHover: { borderColor: '#aaa' },
    selectedImageContainer: { marginTop: '20px', padding: '10px', border: '1px solid #ccc', textAlign: 'center' },
    largeImage: { maxWidth: '100%', maxHeight: '70vh', border: '1px solid #eee' },
    loadingText: { fontStyle: 'italic', color: '#777' },
    errorText: { color: 'red', marginTop: '10px' },
    statusText: { color: 'orange', marginTop: '5px' },
    closeButton: { marginTop: '10px', padding: '5px 10px' },
  };

  return (
    <div style={styles.container}>
      <h3>Image Set Gallery</h3>

      {!webSocketService?.isConnected && <p style={styles.statusText}>WebSocket disconnected. Cannot manage images.</p>}
      {error && <p style={styles.errorText}>Error: {error}</p>}

      <h4>Available Sets <button onClick={fetchImageSets} disabled={isLoadingSets || !webSocketService?.isConnected}>Refresh Sets</button></h4>
      {isLoadingSets ? (
        <p style={styles.loadingText}>Loading sets...</p>
      ) : imageSets.length === 0 && webSocketService?.isConnected ? (
        <p>No image sets found.</p>
      ) : (
        <ul style={styles.setsList}>
          {imageSets.map((set) => (
            <li
              key={set.name}
              style={{
                ...styles.setListItem,
                ...(selectedSet === set.name ? styles.selectedSetItem : {}),
              }}
              onClick={() => handleSelectSet(set.name)}
              onMouseEnter={(e) => (e.currentTarget.style.backgroundColor = styles.setListItemHover.backgroundColor!)}
              onMouseLeave={(e) => (e.currentTarget.style.backgroundColor = selectedSet === set.name ? styles.selectedSetItem.backgroundColor! : 'transparent')}
            >
              <span>{set.name}</span>
              <button
                style={styles.deleteButton}
                onClick={(e) => { e.stopPropagation(); handleDeleteSet(set.name); }}
                disabled={!webSocketService?.isConnected}
              >
                Delete
              </button>
            </li>
          ))}
        </ul>
      )}

      {selectedSet && (
        <div style={{ marginTop: '20px' }}>
          <h4>Images in "{selectedSet}"</h4>
          {isLoadingContents ? (
            <p style={styles.loadingText}>Loading images in set...</p>
          ) : setContents.length === 0 ? (
            <p>No images found in this set, or set is empty.</p>
          ) : (
            <div style={styles.contentsList}>
              {setContents.map((imageFile) => (
                <div
                  key={imageFile.filename}
                  style={styles.imageThumbnail}
                  onClick={() => handleSelectImage(imageFile)}
                  onMouseEnter={(e) => (e.currentTarget.style.borderColor = styles.imageThumbnailHover.borderColor!)}
                  onMouseLeave={(e) => (e.currentTarget.style.borderColor = '#ddd')}
                >
                  <div style={{width: '100px', height: '80px', backgroundColor: '#f0f0f0', display:'flex', alignItems:'center', justifyContent:'center', marginBottom:'5px'}}>
                    <small>Click to load</small>
                  </div>
                  <small title={imageFile.filename} style={{display: 'block', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis'}}>
                    {imageFile.filename}
                  </small>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {selectedImage && (
        <div style={styles.selectedImageContainer}>
          <h5>{selectedImage.filename}</h5>
          {isLoadingImage ? (
            <p style={styles.loadingText}>Loading image data...</p>
          ) : selectedImage.b64Data && selectedImage.mimetype ? (
            <img
              src={`data:${selectedImage.mimetype};base64,${selectedImage.b64Data}`}
              alt={selectedImage.filename}
              style={styles.largeImage}
            />
          ) : (
            <p style={styles.errorText}>Could not load image data.</p>
          )}
          <br />
          <button style={styles.closeButton} onClick={() => setSelectedImage(null)}>Close Image</button>
        </div>
      )}
    </div>
  );
};

export default ImageGallery;
```
