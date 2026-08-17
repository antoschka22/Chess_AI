"""
===============================================================================
Module: Chess Vision AI
Description: A real-time computer vision chess interface connecting an ESP32 
             camera feed, a YOLOv8 object detection model, and the Stockfish 
             engine to automate physical-to-digital chess gameplay.
             
Features:
    - 4-point perspective warp for board alignment.
    - YOLOv8-based piece occupancy detection.
    - Physical move detection & validation via python-chess.
    - Fallback manual UI overrides.
    - Automated API calls to robotic/ESP32 components.
===============================================================================
"""

import cv2
import numpy as np
import urllib.request
from ultralytics import YOLO
from stockfish import Stockfish
import chess

# =============================================================================
# 1. SETUP & CONFIGURATION
# =============================================================================

# Network configuration for ESP32 endpoints
ESP32_VIDEO_URL = "http://192.168.0.54:81"   # MJPEG video stream endpoint
ESP32_MOVE_URL = "http://192.168.0.54"       # Actuator/Movement API endpoint

# Calibration state
board_corners = []           # Stores the 4 (x,y) coordinates for board warping
board_window_created = False # Flag to ensure perfect 2D UI window is only created once

# Application state
board = chess.Board()        # Internal digital representation of the chess game
selected_square = None       # Tracks UI manual square selection
manual_move = None           # Stores a move manually inputted via the UI
force_start = False          # Override flag to bypass initial board validation
show_red_dots = True         # Toggle for rendering YOLO detection markers (Tweak 3)

# =============================================================================
# 2. MODEL INITIALIZATION
# =============================================================================

print("[INFO] Loading YOLO Model...")
try:
    # Load custom-trained YOLOv8 model for piece detection
    model = YOLO("yolov8_chess.pt")
except Exception as e:
    print(f"[CRITICAL] Could not load 'yolov8_chess.pt'. Error: {e}")
    model = None

print("[INFO] Loading Stockfish Engine...")
try:
    # Initialize Stockfish chess engine for AI opponent
    stockfish = Stockfish(path="./stockfish.exe")
except Exception as e:
    print(f"[CRITICAL] Could not load Stockfish. Error: {e}")
    stockfish = None

# =============================================================================
# 3. CORE FUNCTIONS & EVENT HANDLERS
# =============================================================================

def click_event(event, x, y, flags, params):
    """
    OpenCV mouse callback for the raw video feed.
    Captures 4 distinct points to compute the perspective transform matrix.
    
    Args:
        event (int): OpenCV mouse event type.
        x (int): Mouse X coordinate.
        y (int): Mouse Y coordinate.
        flags (int): Any relevant flags passed by OpenCV.
        params (dict): Optional parameters.
    """
    global board_corners
    if event == cv2.EVENT_LBUTTONDOWN:
        if len(board_corners) < 4:
            board_corners.append([x, y])
            print(f"[CALIBRATION] Corner {len(board_corners)}/4 recorded at ({x}, {y}).")
        else:
            # If 4 corners already exist, a new click resets the calibration sequence
            print("[CALIBRATION] Resetting corners...")
            board_corners.clear()

def board_click_event(event, x, y, flags, params):
    """
    OpenCV mouse callback for the transformed "Perfect 2D Board".
    Allows the user to manually override physical detection by clicking squares.
    
    Args:
        event (int): OpenCV mouse event type.
        x (int): Mouse X coordinate.
        y (int): Mouse Y coordinate.
        flags (int): Any relevant flags passed by OpenCV.
        params (dict): Optional parameters.
    """
    global selected_square, manual_move, board
    if event == cv2.EVENT_LBUTTONDOWN:
        # Ignore clicks falling into the telemetry/UI status bar (bottom 60px)
        if y >= 400: return 
        
        # Map (X, Y) pixel coordinates to an 8x8 grid (50x50 pixels per square)
        col = x // 50
        row = y // 50
        
        if 0 <= col < 8 and 0 <= row < 8:
            # python-chess defines A1 as 0, H8 as 63. Row requires inversion.
            clicked_square = chess.square(col, 7 - row)
            
            if selected_square is None:
                # Select a piece if the square is occupied
                if board.piece_at(clicked_square):
                    selected_square = clicked_square
            else:
                # Attempt to construct a move from the previously selected square
                move = chess.Move(selected_square, clicked_square)
                
                # Intercept pawn promotion edge cases, defaulting to Queen
                if chess.Move(selected_square, clicked_square, promotion=chess.QUEEN) in board.legal_moves:
                    move = chess.Move(selected_square, clicked_square, promotion=chess.QUEEN)
                
                # Push the valid manual move to the global variable for processing
                if move in board.legal_moves:
                    manual_move = move
                selected_square = None  # Clear selection buffer

def get_yolo_occupancy(results, matrix):
    """
    Translates raw YOLO bounding boxes into an 8x8 boolean occupancy grid.
    
    Args:
        results: The inference payload from ultralytics YOLO.
        matrix (np.ndarray): The 3x3 perspective transformation matrix.
        
    Returns:
        list[list[bool]]: An 8x8 grid where True indicates physical piece presence.
    """
    grid = [[False for _ in range(8)] for _ in range(8)]
    for box in results.boxes:
        # Extract bottom-center of the bounding box (piece base)
        x1, y1, x2, y2 = box.xyxy[0].cpu().numpy()
        pt = np.array([[[ (x1 + x2) / 2, y2 ]]], dtype=np.float32)
        
        # Warp the point to match the top-down 2D grid
        warped_pt = cv2.perspectiveTransform(pt, matrix)
        warp_x, warp_y = int(warped_pt[0][0][0]), int(warped_pt[0][0][1])
        
        # Map transformed pixel to 8x8 grid
        col, row = warp_x // 50, warp_y // 50
        if 0 <= row < 8 and 0 <= col < 8:
            grid[row][col] = True
            
    return grid

def get_board_occupancy(board_state):
    """
    Generates an 8x8 boolean occupancy grid from the digital game state.
    
    Args:
        board_state (chess.Board): The current python-chess board object.
        
    Returns:
        list[list[bool]]: An 8x8 grid where True indicates a logical piece presence.
    """
    grid = [[False for _ in range(8)] for _ in range(8)]
    for row in range(8):
        for col in range(8):
            if board_state.piece_at(chess.square(col, 7 - row)):
                grid[row][col] = True
    return grid

# =============================================================================
# 4. MAIN APPLICATION LOOP
# =============================================================================

def main():
    global board_window_created, board, manual_move, force_start, selected_square, show_red_dots
    
    print("\n=======================================================")
    print("                CHESS INTERFACE READY                  ")
    print("=======================================================")
    
    # --- Game Setup ---
    user_input = input("Do you want to play as White (w) or Black (b)? ").strip().lower()
    ai_color = chess.BLACK if user_input == 'w' else chess.WHITE
    
    # Tweak 2: Assign descriptive player tags for the UI renderer
    player1_color_str = "White" if ai_color == chess.BLACK else "Black"
    player2_color_str = "Black" if ai_color == chess.BLACK else "White"
    print(f"[INFO] Player 1 (Local) is {player1_color_str}.")
    print(f"[INFO] Player 2 (Stockfish) is {player2_color_str}.")
    
    # State tracking variables
    game_started = False
    stable_occupancy = None
    stable_count = 0
    REQUIRED_STABLE_FRAMES = 5  # Debounce mechanism: requires 5 consecutive frames of identical occupancy
    last_move_text = "Click the 4 corners..."

    print(f"\n[NETWORK] Connecting to ESP32 stream at {ESP32_VIDEO_URL}...")
    
    try:
        # Establish MJPEG stream connection
        stream = urllib.request.urlopen(ESP32_VIDEO_URL)
        bytes_data = b''
        
        # Initialize primary calibration window
        cv2.namedWindow("Chess AI - Live Feed")
        cv2.setMouseCallback("Chess AI - Live Feed", click_event)

        # --- Primary Frame Loop ---
        while True:
            bytes_data += stream.read(4096)
            
            # Find JPEG boundary markers in the bytestream
            a = bytes_data.find(b'\xff\xd8') # Start of Image (SOI)
            b = bytes_data.find(b'\xff\xd9') # End of Image (EOI)
            
            if a != -1 and b != -1:
                # Extract and decode a single JPEG frame
                jpg = bytes_data[a:b+2]
                bytes_data = bytes_data[b+2:]
                frame = cv2.imdecode(np.frombuffer(jpg, dtype=np.uint8), cv2.IMREAD_COLOR)
                
                if frame is not None:
                    # Overlay calibration markers on the raw feed
                    for pt in board_corners:
                        cv2.circle(frame, (pt[0], pt[1]), 5, (0, 0, 255), -1)
                        
                    cv2.imshow("Chess AI - Live Feed", frame)
                    
                    # --- Global Keyboard Listeners ---
                    key = cv2.waitKey(1) & 0xFF
                    if key == ord('q'):
                        print("[SYSTEM] Quitting application...")
                        break
                    if key == ord('s') and not game_started:
                        force_start = True
                    if key == ord('t'): 
                        show_red_dots = not show_red_dots # Toggle sensor overlay
                    
                    # --- Core Processing (Triggered after calibration) ---
                    if len(board_corners) == 4 and model is not None:
                        # Instantiate 2D tracking UI once
                        if not board_window_created:
                            cv2.namedWindow("Perfect 2D Board")
                            cv2.setMouseCallback("Perfect 2D Board", board_click_event)
                            board_window_created = True

                        # Compute perspective transform map (400x400 normalized board)
                        pts1 = np.float32(board_corners)
                        pts2 = np.float32([[0, 0], [400, 0], [400, 400], [0, 400]])
                        matrix = cv2.getPerspectiveTransform(pts1, pts2)
                        warped_board = cv2.warpPerspective(frame, matrix, (400, 400))
                        
                        # Render Gridlines
                        for i in range(1, 8):
                            cv2.line(warped_board, (i * 50, 0), (i * 50, 400), (255, 0, 0), 1)
                            cv2.line(warped_board, (0, i * 50), (400, i * 50), (255, 0, 0), 1)

                        # Highlight the manually selected square (if any)
                        if selected_square is not None:
                            sq_col = chess.square_file(selected_square)
                            sq_row = 7 - chess.square_rank(selected_square)
                            cv2.rectangle(warped_board, (sq_col*50, sq_row*50), ((sq_col+1)*50, (sq_row+1)*50), (0, 255, 255), 3)

                        # Run YOLO inference
                        results = model(frame, verbose=False, conf=0.15)[0]
                        camera_occupancy = get_yolo_occupancy(results, matrix)
                        board_occupancy = get_board_occupancy(board)
                        
                        # Render logical pieces and YOLO detections
                        for row in range(8):
                            for col in range(8):
                                # Draw piece symbols
                                piece = board.piece_at(chess.square(col, 7 - row))
                                if piece:
                                    cv2.putText(warped_board, piece.symbol(), (col*50 + 15, row*50 + 35), 
                                                cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)
                                
                                # Tweak 3: Draw YOLO occupancy indicators (Red dots) based on toggle
                                if camera_occupancy[row][col] and show_red_dots:
                                    cv2.circle(warped_board, (col*50 + 40, row*50 + 10), 3, (0, 0, 255), -1)

                        # ==========================================
                        # STATE MACHINE and GAME LOGIC
                        # ==========================================
                        
                        # Debounce logic: wait for vision system to stabilize
                        if camera_occupancy == stable_occupancy:
                            stable_count += 1
                        else:
                            stable_occupancy = camera_occupancy
                            stable_count = 1
                            
                        # --- Initialization and Start Setup ---
                        if not game_started:
                            # Verify all pieces are in starting position OR start was forced via 's'
                            if force_start or (stable_count >= REQUIRED_STABLE_FRAMES and camera_occupancy == board_occupancy):
                                game_started = True
                                last_move_text = "Game Started!"
                                print("\n[STATUS] Game Started! (Verified or Forced)")
                            else:
                                last_move_text = "Wait for setup OR press 's' to Force Start"
                                
                        # --- Main Gameplay Loop ---
                        if game_started:
                            
                            # UI Override: Process manual moves
                            if manual_move is not None:
                                print(f"\n[UI EVENT] Move accepted: {manual_move.uci()}")
                                board.push(manual_move)
                                last_move_text = f"Player 1 moved {manual_move.uci()}"
                                manual_move = None 
                            
                            # AI Turn: Process Stockfish logic
                            elif board.turn == ai_color:
                                last_move_text = "Player 2 Thinking..."
                                
                                # Render transitional "Thinking" UI frame so main thread doesn't appear frozen
                                ui_board = cv2.copyMakeBorder(warped_board, 0, 60, 0, 0, cv2.BORDER_CONSTANT, value=[0, 0, 0])
                                cv2.putText(ui_board, f"Player 1 ({player1_color_str}) | Status: {last_move_text}", (5, 425), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1)
                                cv2.putText(ui_board, "Controls: [s] Start | [t] Toggle Sensors | [q] Quit", (5, 450), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (200, 200, 200), 1)
                                cv2.imshow("Perfect 2D Board", ui_board)
                                cv2.waitKey(1) 
                                
                                # Query engine
                                stockfish.set_fen_position(board.fen())
                                best_move = stockfish.get_best_move()
                                print(f"\n[ENGINE] Player 2 suggests: {best_move}")
                                
                                # Send movement payload to physical ESP32 actuator/bot
                                try:
                                    urllib.request.urlopen(f"{ESP32_MOVE_URL}/move?text={best_move}", timeout=2)
                                except Exception:
                                    # Expected to fail if bot is offline or API format changes.
                                    pass
                                    
                                # Commit move to digital state
                                board.push(chess.Move.from_uci(best_move))
                                last_move_text = f"Player 2 moved {best_move}"
                                
                            # Local Player Turn: Auto-detect physical piece movement
                            elif stable_count >= REQUIRED_STABLE_FRAMES:
                                # Look for a delta between YOLO state and digital state
                                if camera_occupancy != board_occupancy:
                                    # Iterate legal moves to find one that results in the current physical occupancy
                                    for move in board.legal_moves:
                                        board.push(move)
                                        if camera_occupancy == get_board_occupancy(board):
                                            print(f"\n[VISION] Valid move detected: {move.uci()}")
                                            last_move_text = f"Player 1 moved {move.uci()}"
                                            break # Leave the valid move on the board stack
                                        board.pop() # Revert move if it didn't match physical state

                        # ==========================================
                        # FINAL UI GENERATION
                        # ==========================================
                        
                        # Add a 60px black bar at the bottom for telemetry/UI text
                        ui_board = cv2.copyMakeBorder(warped_board, 0, 60, 0, 0, cv2.BORDER_CONSTANT, value=[0, 0, 0])
                        
                        # Render Status Text
                        status_str = f"Player 1 ({player1_color_str}) | Status: {last_move_text}"
                        cv2.putText(ui_board, status_str, (5, 425), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 255, 255), 1)
                        
                        # Render Controls Legend
                        controls_str = "Controls: [s] Start | [t] Toggle Sensors | [q] Quit"
                        cv2.putText(ui_board, controls_str, (5, 450), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (200, 200, 200), 1)
                        
                        cv2.imshow("Perfect 2D Board", ui_board)
                    
    except Exception as e:
        print(f"[CRITICAL] Connection to stream failed: {e}")
        
    finally:
        # Guarantee resource cleanup on exit
        cv2.destroyAllWindows()

if __name__ == "__main__":
    main()