import cv2
import numpy as np
import urllib.request
from ultralytics import YOLO
from stockfish import Stockfish
import chess

# ==========================================
# SETUP & CONFIGURATION
# ==========================================
ESP32_VIDEO_URL = "http://192.168.0.54:81" 
ESP32_MOVE_URL = "http://192.168.0.54"       
board_corners = []

print("Loading YOLO AI Model...")
try:
    model = YOLO("yolov8_chess.pt")
except Exception as e:
    print(f"WARNING: Could not load 'yolov8_chess.pt'. Error: {e}")
    model = None

print("Loading Stockfish Engine...")
try:
    stockfish = Stockfish(path="./stockfish.exe")
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
    print("\n===================================")
    print("      CHESS AI - AUTO MODE         ")
    print("===================================")
    
    # 1. Ask the user what color they want to play
    user_input = input("Do you want to play as White (w) or Black (b)? ").strip().lower()
    ai_color = chess.BLACK if user_input == 'w' else chess.WHITE
    print(f"Great! The AI will play as {'Black' if ai_color == chess.BLACK else 'White'}.")
    
    # 2. Setup the logical game state
    board = chess.Board()
    game_started = False
    stable_fen = ""
    stable_count = 0
    REQUIRED_STABLE_FRAMES = 5  
    last_move_text = "Click the 4 corners..."

    print(f"\nConnecting to ESP32 stream at {ESP32_VIDEO_URL}...")
    
    try:
        stream = urllib.request.urlopen(ESP32_VIDEO_URL)
        bytes_data = b''
        
        cv2.namedWindow("Chess AI - Live Feed")
        cv2.setMouseCallback("Chess AI - Live Feed", click_event)
        
        print("\n--- INSTRUCTIONS ---")
        print("1. Set up all 32 pieces on the board.")
        print("2. Click the 4 corners.")
        print("3. The game will start automatically once setup is verified!")
        print("4. Press 'q' to quit.\n")

        while True:
            bytes_data += stream.read(4096)
            a = bytes_data.find(b'\xff\xd8')
            b = bytes_data.find(b'\xff\xd9')
            
            if a != -1 and b != -1:
                jpg = bytes_data[a:b+2]
                bytes_data = bytes_data[b+2:]
                
                frame = cv2.imdecode(np.frombuffer(jpg, dtype=np.uint8), cv2.IMREAD_COLOR)
                
                if frame is not None:
                    for pt in board_corners:
                        cv2.circle(frame, (pt[0], pt[1]), 5, (0, 0, 255), -1)
                        
                    cv2.imshow("Chess AI - Live Feed", frame)
                    
                    if cv2.waitKey(1) & 0xFF == ord('q'):
                        print("Quitting...")
                        break
                    
                    if len(board_corners) == 4 and model is not None:
                        pts1 = np.float32(board_corners)
                        pts2 = np.float32([[0, 0], [400, 0], [400, 400], [0, 400]])
                        matrix = cv2.getPerspectiveTransform(pts1, pts2)
                        warped_board = cv2.warpPerspective(frame, matrix, (400, 400))
                        
                        # Draw gridlines
                        for i in range(1, 8):
                            cv2.line(warped_board, (i * 50, 0), (i * 50, 400), (255, 0, 0), 1)
                            cv2.line(warped_board, (0, i * 50), (400, i * 50), (255, 0, 0), 1)

                        # YOLO Vision
                        results = model(frame, verbose=False, conf=0.15)[0]
                        grid = [['1' for _ in range(8)] for _ in range(8)]
                        
                        for box in results.boxes:
                            class_id = int(box.cls[0].cpu().numpy())
                            class_name = model.names[class_id]
                            fen_char = get_fen_char(class_name)
                            
                            if fen_char:
                                x1, y1, x2, y2 = box.xyxy[0].cpu().numpy()
                                pt = np.array([[[ (x1 + x2) / 2, y2 ]]], dtype=np.float32)
                                warped_pt = cv2.perspectiveTransform(pt, matrix)
                                warp_x, warp_y = int(warped_pt[0][0][0]), int(warped_pt[0][0][1])
                                col, row = warp_x // 50, warp_y // 50
                                
                                if 0 <= row < 8 and 0 <= col < 8:
                                    grid[row][col] = fen_char
                                    cv2.putText(warped_board, fen_char, (warp_x - 10, warp_y - 10), 
                                                cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
                        
                        # Build the YOLO FEN (Board state only, no rules attached)
                        fen_rows = []
                        for r in grid:
                            empty_count = 0
                            row_str = ""
                            for square in r:
                                if square == '1': empty_count += 1
                                else:
                                    if empty_count > 0:
                                        row_str += str(empty_count)
                                        empty_count = 0
                                    row_str += square
                            if empty_count > 0: row_str += str(empty_count)
                            fen_rows.append(row_str)
                            
                        yolo_fen = "/".join(fen_rows)
                        
                        # ==========================================
                        # STATE MACHINE & GAME LOGIC
                        # ==========================================
                        if yolo_fen == stable_fen:
                            stable_count += 1
                        else:
                            stable_fen = yolo_fen
                            stable_count = 1
                            
                        # If the camera feed has been perfectly still for a fraction of a second
                        if stable_count >= REQUIRED_STABLE_FRAMES:
                            
                            # PHASE 1: Waiting to start
                            if not game_started:
                                if yolo_fen == board.board_fen():
                                    game_started = True
                                    last_move_text = "Game Started!"
                                    print("\n[!] Game Started! All pieces detected in starting position.")
                                else:
                                    last_move_text = "Please set up the pieces..."
                                    
                            # PHASE 2: Playing the Game
                            if game_started:
                                # Is it the AI's turn?
                                if board.turn == ai_color:
                                    last_move_text = "AI Thinking..."
                                    cv2.putText(warped_board, last_move_text, (5, 395), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)
                                    cv2.imshow("Perfect 2D Board", warped_board)
                                    cv2.waitKey(1)
                                    
                                    # Calculate move
                                    stockfish.set_fen_position(board.fen())
                                    best_move = stockfish.get_best_move()
                                    print(f"\n[AI] Stockfish suggests: {best_move}")
                                    
                                    # Update physical OLED
                                    try:
                                        urllib.request.urlopen(f"{ESP32_MOVE_URL}/move?text={best_move}", timeout=2)
                                    except Exception:
                                        pass
                                        
                                    # Update logical board so Python knows what the board SHOULD look like next
                                    board.push(chess.Move.from_uci(best_move))
                                    last_move_text = f"AI moved: {best_move}"
                                    
                                # Is it the human's turn?
                                else:
                                    # If the physical board doesn't match the digital board
                                    if yolo_fen != board.board_fen():
                                        move_found = False
                                        # Test every legal chess move against the camera feed
                                        for move in board.legal_moves:
                                            board.push(move)
                                            if board.board_fen() == yolo_fen:
                                                print(f"\n[HUMAN] Valid move detected: {move.uci()}")
                                                last_move_text = f"Human: {move.uci()}"
                                                move_found = True
                                                break # Keep the move applied to the digital board!
                                            board.pop() # Not a match, undo it
                                            
                                        if not move_found:
                                            last_move_text = "Waiting for move..."

                        # UI Overlay
                        cv2.rectangle(warped_board, (0, 360), (400, 400), (0, 0, 0), -1)
                        cv2.putText(warped_board, "Status:", (5, 375), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1)
                        cv2.putText(warped_board, last_move_text, (5, 395), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)
                        
                        cv2.imshow("Perfect 2D Board", warped_board)
                    
    except Exception as e:
        print(f"ERROR: Connection failed: {e}")
        
    cv2.destroyAllWindows()

if __name__ == "__main__":
    main()