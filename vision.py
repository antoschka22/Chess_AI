import cv2
import numpy as np
import urllib.request
from ultralytics import YOLO
from stockfish import Stockfish

# ==========================================
# SETUP & CONFIGURATION
# ==========================================
ESP32_URL = "http://192.168.0.54" 
board_corners = []

print("Loading YOLO AI Model...")
try:
    model = YOLO("yolov8_chess.pt")
    print("YOLO Model loaded successfully!")
except Exception as e:
    print(f"WARNING: Could not load 'yolov8_chess.pt'. Error: {e}")
    model = None

print("Loading Stockfish Engine...")
try:
    stockfish = Stockfish(path="./stockfish.exe")
    print("Stockfish loaded successfully!")
except Exception as e:
    print(f"WARNING: Could not load Stockfish. Error: {e}")
    stockfish = None

def get_fen_char(class_name):
    """Translates YOLO labels into standard FEN notation."""
    name = class_name.lower()
    if 'knight' in name: piece = 'n'
    elif 'bishop' in name: piece = 'b'
    elif 'rook' in name: piece = 'r'
    elif 'queen' in name: piece = 'q'
    elif 'king' in name: piece = 'k'
    elif 'pawn' in name: piece = 'p'
    else: return None
    
    if 'white' in name: return piece.upper()
    return piece

def click_event(event, x, y, flags, params):
    global board_corners
    if event == cv2.EVENT_LBUTTONDOWN:
        if len(board_corners) < 4:
            board_corners.append([x, y])
            print(f"Corner {len(board_corners)} recorded.")
        else:
            print("Resetting corners...")
            board_corners.clear()

# ==========================================
# MAIN VIDEO LOOP
# ==========================================
def main():
    print(f"\nConnecting to ESP32 stream at {ESP32_URL}...")
    
    try:
        stream = urllib.request.urlopen(ESP32_URL)
        bytes_data = b''
        
        cv2.namedWindow("Chess AI - Live Feed")
        cv2.setMouseCallback("Chess AI - Live Feed", click_event)
        
        print("\n--- INSTRUCTIONS ---")
        print("1. Click the 4 corners of your physical board.")
        print("2. Press SPACEBAR to analyze the pieces and get a move!")
        print("3. Press 'q' to quit.\n")
        
        last_move = "Waiting..."

        while True:
            bytes_data += stream.read(4096)
            a = bytes_data.find(b'\xff\xd8')
            b = bytes_data.find(b'\xff\xd9')
            
            if a != -1 and b != -1:
                jpg = bytes_data[a:b+2]
                bytes_data = bytes_data[b+2:]
                
                # The raw, unwarped frame straight from the ESP32
                frame = cv2.imdecode(np.frombuffer(jpg, dtype=np.uint8), cv2.IMREAD_COLOR)
                
                if frame is not None:
                    for pt in board_corners:
                        cv2.circle(frame, (pt[0], pt[1]), 5, (0, 0, 255), -1)
                        
                    cv2.imshow("Chess AI - Live Feed", frame)
                    
                    key = cv2.waitKey(1) & 0xFF
                    if key == ord('q'):
                        print("Quitting...")
                        break
                    
                    if len(board_corners) == 4:
                        pts1 = np.float32(board_corners)
                        pts2 = np.float32([[0, 0], [400, 0], [400, 400], [0, 400]])
                        matrix = cv2.getPerspectiveTransform(pts1, pts2)
                        
                        # We still create the visual flattened board for your second window
                        warped_board = cv2.warpPerspective(frame, matrix, (400, 400))
                        
                        for i in range(1, 8):
                            cv2.line(warped_board, (i * 50, 0), (i * 50, 400), (255, 0, 0), 1)
                            cv2.line(warped_board, (0, i * 50), (400, i * 50), (255, 0, 0), 1)

                        if key == 32 and model is not None: 
                            print("\nAnalyzing board...")
                            
                            # --- THE FIX: Pass the RAW frame to YOLO, not the distorted one ---
                            # We lower the confidence slightly to 0.15 to easily catch pieces from an angle
                            results = model(frame, verbose=False, conf=0.15)[0]
                            grid = [['1' for _ in range(8)] for _ in range(8)]
                            
                            for box in results.boxes:
                                class_id = int(box.cls[0].cpu().numpy())
                                class_name = model.names[class_id]
                                fen_char = get_fen_char(class_name)
                                
                                # If it's a piece (and not the "board" class)
                                if fen_char:
                                    x1, y1, x2, y2 = box.xyxy[0].cpu().numpy()
                                    center_x = (x1 + x2) / 2
                                    bottom_y = y2  # The very base of the bounding box
                                    
                                    # Mathematically warp just this single (x, y) coordinate
                                    pt = np.array([[[center_x, bottom_y]]], dtype=np.float32)
                                    warped_pt = cv2.perspectiveTransform(pt, matrix)
                                    
                                    warp_x = int(warped_pt[0][0][0])
                                    warp_y = int(warped_pt[0][0][1])
                                    
                                    col = warp_x // 50
                                    row = warp_y // 50
                                    
                                    if 0 <= row < 8 and 0 <= col < 8:
                                        grid[row][col] = fen_char
                                        # Draw a green dot on the 2D UI to show exactly where the piece landed
                                        cv2.circle(warped_board, (warp_x, warp_y), 5, (0, 255, 0), -1)
                                        cv2.putText(warped_board, fen_char, (warp_x - 10, warp_y - 10), 
                                                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
                            
                            # Convert grid to standard FEN string
                            fen_rows = []
                            for r in grid:
                                empty_count = 0
                                row_str = ""
                                for square in r:
                                    if square == '1':
                                        empty_count += 1
                                    else:
                                        if empty_count > 0:
                                            row_str += str(empty_count)
                                            empty_count = 0
                                        row_str += square
                                if empty_count > 0:
                                    row_str += str(empty_count)
                                fen_rows.append(row_str)
                                
                            current_fen = "/".join(fen_rows) + " w - - 0 1"
                            print(f"Detected FEN: {current_fen}")
                            
                            # Ask Stockfish for the optimal move
                            if stockfish:
                                if stockfish.is_fen_valid(current_fen):
                                    stockfish.set_fen_position(current_fen)
                                    best_move = stockfish.get_best_move()
                                    if best_move:
                                        last_move = f"Move: {best_move}"
                                        print(f"Stockfish suggests: {best_move}")
                                        
                                        # Blast the move over Wi-Fi to the ESP32 OLED
                                        try:
                                            urllib.request.urlopen(f"{ESP32_URL}/move?text={best_move}", timeout=2)
                                            print("Move sent to OLED successfully!")
                                        except Exception as e:
                                            print(f"Could not send to OLED: {e}")
                                    else:
                                        last_move = "Checkmate/Error"
                                else:
                                    last_move = "Invalid Board State"
                                    print("Invalid FEN. Make sure the board is clear of hands/shadows.")

                        # UI Overlay for the second window
                        cv2.rectangle(warped_board, (0, 360), (400, 400), (0, 0, 0), -1)
                        cv2.putText(warped_board, "Press SPACE to Analyze", (5, 375), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1)
                        cv2.putText(warped_board, last_move, (5, 395), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)
                        
                        cv2.imshow("Perfect 2D Board", warped_board)
                    
    except Exception as e:
        print(f"ERROR: Connection failed: {e}")
        
    cv2.destroyAllWindows()

if __name__ == "__main__":
    main()