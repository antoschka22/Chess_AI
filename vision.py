import cv2
import numpy as np
import urllib.request

# ==========================================
# UPDATE IP IF NECESSARY
# ==========================================
ESP32_URL = "http://192.168.0.54" 

def main():
    print(f"Connecting to ESP32 stream at {ESP32_URL}...")
    
    try:
        # Open the live network stream
        stream = urllib.request.urlopen(ESP32_URL)
        bytes_data = b''
        print("Stream connected! Press 'q' on your keyboard to quit.")
        
        while True:
            # Read the stream in chunks
            bytes_data += stream.read(4096)
            
            # A JPEG file always starts with 'ff d8' and ends with 'ff d9' in hex
            a = bytes_data.find(b'\xff\xd8')
            b = bytes_data.find(b'\xff\xd9')
            
            if a != -1 and b != -1:
                # Extract the exact bytes of one full frame
                jpg = bytes_data[a:b+2]
                bytes_data = bytes_data[b+2:]
                
                # Convert the raw bytes into a format OpenCV can display
                frame = cv2.imdecode(np.frombuffer(jpg, dtype=np.uint8), cv2.IMREAD_COLOR)
                
                if frame is not None:
                    cv2.imshow("Chess AI - Live Feed", frame)
                
                # Wait 1 millisecond. If 'q' is pressed, break the loop.
                if cv2.waitKey(1) & 0xFF == ord('q'):
                    print("Quitting...")
                    break
                    
    except Exception as e:
        print(f"ERROR: Connection failed: {e}")
        
    cv2.destroyAllWindows()

if __name__ == "__main__":
    main()