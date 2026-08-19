import cv2
import requests
import time
import chess
import chess.engine
import numpy as np

# --- CONFIGURATION ---
ESP_IP = "" # ENTER YOUR ESP32 IP ADDRESS
STREAM_URL = f"http://{ESP_IP}:81/"
COMMAND_URL = f"http://{ESP_IP}:80"
STOCKFISH_PATH = "./jstockfish.exe" # Path to your Stockfish binary

class ChessEdgeController:
    def __init__(self):
        self.board = chess.Board()
        self.engine = chess.engine.SimpleEngine.popen_uci(STOCKFISH_PATH)
        
        # Hardware interfaces
        self.cap = None
        
        # State Machine Variables
        self.state = "CAMERA_ACTIVE"
        self.last_valid_frame_time = time.time()
        self.last_reconnect_attempt = 0.0
        self.manual_cmd_sent = False

    def send_to_esp32(self, text):
        """Sends a string to the ESP32 OLED display via HTTP Queue."""
        try:
            requests.get(f"{COMMAND_URL}/move", params={"text": text[:50]}, timeout=2)
        except requests.exceptions.RequestException as e:
            print(f"Failed to reach ESP32: {e}")

    def process_engine_move(self):
        """Calculates the engine's response and updates the hardware."""
        result = self.engine.play(self.board, chess.engine.Limit(time=0.5))
        engine_move = result.move
        self.board.push(engine_move)
        
        # Send the counter-move to the OLED
        move_str = f"Move: {engine_move.uci()}"
        print(f"Engine played: {move_str}")
        self.send_to_esp32(move_str)

    def mock_detect_board(self, frame):
        """
        Placeholder for your actual YOLO/OpenCV logic.
        Returns True if the board is clearly visible, False if obscured/failed.
        """
        if frame is None:
            return False
        mean_brightness = np.mean(frame)
        return mean_brightness > 20.0 

    def connect_camera(self):
        """Helper function to initialize the camera stream."""
        if self.cap is not None:
            self.cap.release()
        self.cap = cv2.VideoCapture(STREAM_URL)
        self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

    def run(self):
        print("Connecting to ESP32 Camera Stream...")
        self.connect_camera()

        while True:
            # =========================================================
            # STATE 1: NORMAL CAMERA OPERATION
            # =========================================================
            if self.state == "CAMERA_ACTIVE":
                ret, frame = self.cap.read()
                
                if ret and self.mock_detect_board(frame):
                    # We have a good frame! Reset the watchdog timer.
                    self.last_valid_frame_time = time.time()
                    
                    # [YOUR YOLO/OPENCV LOGIC GOES HERE]
                    
                    cv2.imshow("Chess Vision", frame)
                    if cv2.waitKey(1) & 0xFF == ord('q'):
                        break
                else:
                    # Watchdog: If we haven't seen the board clearly for 8 seconds, degrade.
                    if time.time() - self.last_valid_frame_time > 8.0:
                        print("\n[!] Camera feed lost or obstructed. Degrading to Manual Fallback.")
                        self.state = "MANUAL_FALLBACK"
                        # Release the frozen stream so it doesn't block memory
                        self.cap.release() 
            
            # =========================================================
            # STATE 2: GRACEFUL DEGRADATION (MANUAL FALLBACK)
            # =========================================================
            elif self.state == "MANUAL_FALLBACK":
                # Step 1: Tell the ESP32 to switch its UI (only do this once per fallback)
                if not self.manual_cmd_sent:
                    self.send_to_esp32("CMD:MANUAL")
                    self.manual_cmd_sent = True
                
                # Step 2: Poll the ESP32 for manual moves from the phone
                try:
                    resp = requests.get(f"{COMMAND_URL}/get_move", timeout=2)
                    manual_move = resp.text.strip()
                    
                    if manual_move != "NONE" and manual_move != "":
                        print(f"User entered manual move: {manual_move}")
                        try:
                            move = chess.Move.from_uci(manual_move)
                            if move in self.board.legal_moves:
                                self.board.push(move)
                                self.process_engine_move()
                            else:
                                self.send_to_esp32("Invalid Move!")
                        except ValueError:
                            self.send_to_esp32("Format Error!")
                            
                except requests.exceptions.RequestException:
                    pass # Ignore timeouts to keep the loop running smoothly
                
                # Step 3: AUTO-RECOVERY (The Self-Healing Logic)
                # Every 5 seconds, attempt to ping the camera to see if it is back online
                if time.time() - self.last_reconnect_attempt > 5.0:
                    print("[*] Attempting to reconnect to camera stream...")
                    self.last_reconnect_attempt = time.time()
                    
                    self.connect_camera()
                    ret, frame = self.cap.read()
                    
                    if ret and self.mock_detect_board(frame):
                        print("[+] Camera restored successfully! Resuming Edge AI vision.")
                        self.state = "CAMERA_ACTIVE"
                        self.manual_cmd_sent = False # Reset UI trigger
                        self.last_valid_frame_time = time.time()
                        self.send_to_esp32("CAM RESTORED")
                    else:
                        print("[-] Reconnect failed. Staying in manual mode.")
                
                # Brief sleep to prevent hammering the ESP32 web server
                time.sleep(0.5)

        self.cap.release()
        cv2.destroyAllWindows()
        self.engine.quit()

if __name__ == "__main__":
    controller = ChessEdgeController()
    controller.run()