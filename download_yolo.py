import urllib.request
import os

# Direct download link for a reliable YOLOv8 medium chess model
MODEL_URL = "https://huggingface.co/NAKSTStudio/yolov8m-chess-piece-detection/resolve/main/best.pt"
FILE_NAME = "yolov8_chess.pt"

print("Downloading YOLOv8 Chess Model... (This is ~52MB and might take a minute)")

try:
    # Adding a User-Agent header as some servers block Python's default urllib agent
    req = urllib.request.Request(MODEL_URL, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req) as response, open(FILE_NAME, 'wb') as out_file:
        out_file.write(response.read())
    print(f"Success! '{FILE_NAME}' has been saved to your folder.")
    print("You can now run your main vision script!")
except Exception as e:
    print(f"Error downloading the file: {e}")