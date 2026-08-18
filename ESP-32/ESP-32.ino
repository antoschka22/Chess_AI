#include "esp_camera.h"
#include <WiFi.h>
#include <Wire.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
#include "esp_http_server.h"

// --- FREERTOS INCLUDES ---
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/queue.h"
#include "freertos/semphr.h"

// 1. WI-FI CREDENTIALS
const char* ssid = "HH40_6CA7-2.4";
const char* password = "80028244";

// 2. OLED DISPLAY SETTINGS
#define SCREEN_WIDTH 128
#define SCREEN_HEIGHT 64
#define OLED_RESET -1
#define SDA_PIN 1
#define SCL_PIN 2
Adafruit_SSD1306 display(SCREEN_WIDTH, SCREEN_HEIGHT, &Wire, OLED_RESET);

// 3. CAMERA PINS (Freenove ESP32-S3)
#define PWDN_GPIO_NUM  -1
#define RESET_GPIO_NUM -1
#define XCLK_GPIO_NUM  15
#define SIOD_GPIO_NUM  4
#define SIOC_GPIO_NUM  5
#define Y9_GPIO_NUM    16
#define Y8_GPIO_NUM    17
#define Y7_GPIO_NUM    18
#define Y6_GPIO_NUM    12
#define Y5_GPIO_NUM    10
#define Y4_GPIO_NUM    8
#define Y3_GPIO_NUM    9
#define Y2_GPIO_NUM    11
#define VSYNC_GPIO_NUM 6
#define HREF_GPIO_NUM  7
#define PCLK_GPIO_NUM  13

// --- FREERTOS GLOBALS ---
QueueHandle_t moveQueue; 
SemaphoreHandle_t manualMoveMutex; // Mutex to protect the manual move string
#define HEALTH_LED_PIN 2 

char pendingManualMove[50] = ""; // Stores the move entered on the phone

httpd_handle_t stream_httpd = NULL;
httpd_handle_t command_httpd = NULL;


// ==========================================
// FALLBACK WEB APP HTML (Stored in Flash)
// ==========================================
const char* fallback_html = R"rawliteral(
<!DOCTYPE html><html><head><meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  body { font-family: Arial, sans-serif; text-align: center; margin-top: 50px; background-color: #222; color: white; }
  input, button { font-size: 24px; padding: 15px; margin: 10px; border-radius: 8px; border: none; }
  button { background-color: #4CAF50; color: white; font-weight: bold; cursor: pointer; }
  input { text-align: center; width: 60%; }
</style>
</head><body>
  <h2>Chess Fallback Mode</h2>
  <p>Camera offline. Enter move manually:</p>
  <input type="text" id="move" placeholder="e.g. e2e4" maxlength="5">
  <br><button onclick="sendMove()">Submit Move</button>
  <p id="status"></p>
  <script>
    function sendMove() {
      var m = document.getElementById("move").value;
      if(m.length < 4) return;
      document.getElementById("status").innerText = "Sending...";
      fetch("/submit_move?text=" + m).then(r => r.text()).then(t => {
        document.getElementById("status").innerText = "Move " + m + " sent to Engine!";
        document.getElementById("move").value = "";
      });
    }
  </script>
</body></html>
)rawliteral";


// ==========================================
// CORE 0: NETWORKING & STREAMING TASKS
// ==========================================

#define PART_BOUNDARY "123456789000000000000987654321"
static const char* _STREAM_CONTENT_TYPE = "multipart/x-mixed-replace;boundary=" PART_BOUNDARY;
static const char* _STREAM_BOUNDARY = "\r\n--" PART_BOUNDARY "\r\n";
static const char* _STREAM_PART = "Content-Type: image/jpeg\r\nContent-Length: %u\r\n\r\n";

static esp_err_t stream_handler(httpd_req_t *req) {
  camera_fb_t * fb = NULL;
  esp_err_t res = OK;
  size_t _jpg_buf_len = 0;
  uint8_t * _jpg_buf = NULL;
  char * part_buf[64];

  res = httpd_resp_set_type(req, _STREAM_CONTENT_TYPE);
  if (res != ESP_OK) return res;

  while (true) {
    fb = esp_camera_fb_get();
    if (!fb) {
      Serial.println("Camera capture failed");
      res = ESP_FAIL;
    } else {
      if (fb->format != PIXFORMAT_JPEG) {
        bool jpeg_converted = frame2jpg(fb, 60, &_jpg_buf, &_jpg_buf_len);
        esp_camera_fb_return(fb);
        fb = NULL;
        if (!jpeg_converted) {
          Serial.println("JPEG compression failed");
          res = ESP_FAIL;
        }
      } else {
        _jpg_buf_len = fb->len;
        _jpg_buf = fb->buf;
      }
    }
    
    if (res == ESP_OK) {
      size_t hlen = snprintf((char *)part_buf, 64, _STREAM_PART, _jpg_buf_len);
      res = httpd_resp_send_chunk(req, (const char *)part_buf, hlen);
    }
    if (res == ESP_OK) {
      res = httpd_resp_send_chunk(req, (const char *)_jpg_buf, _jpg_buf_len);
    }
    if (res == ESP_OK) {
      res = httpd_resp_send_chunk(req, _STREAM_BOUNDARY, strlen(_STREAM_BOUNDARY));
    }
    
    if (fb) {
      esp_camera_fb_return(fb);
      fb = NULL;
      _jpg_buf = NULL;
    } else if (_jpg_buf) {
      free(_jpg_buf);
      _jpg_buf = NULL;
    }
    if (res != ESP_OK) break;
  }
  return res;
}

// Endpoint: Python sends moves or commands to the ESP32
static esp_err_t move_handler(httpd_req_t *req) {
    char buf[100];
    int len = httpd_req_get_url_query_len(req) + 1;
    if (len > 1) {
        if (httpd_req_get_url_query_str(req, buf, len) == ESP_OK) {
            char param[50];
            if (httpd_query_key_value(buf, "text", param, sizeof(param)) == ESP_OK) {
                xQueueSend(moveQueue, param, (TickType_t)10);
            }
        }
    }
    httpd_resp_send(req, "OK", HTTPD_RESP_USE_STRLEN);
    return ESP_OK;
}

// Endpoint: Serves the HTML Fallback App to the phone
static esp_err_t root_handler(httpd_req_t *req) {
    httpd_resp_set_type(req, "text/html");
    httpd_resp_send(req, fallback_html, HTTPD_RESP_USE_STRLEN);
    return ESP_OK;
}

// Endpoint: Phone submits a manual move
static esp_err_t submit_handler(httpd_req_t *req) {
    char buf[100];
    int len = httpd_req_get_url_query_len(req) + 1;
    if (len > 1) {
        if (httpd_req_get_url_query_str(req, buf, len) == ESP_OK) {
            char param[50];
            if (httpd_query_key_value(buf, "text", param, sizeof(param)) == ESP_OK) {
                
                // Lock the mutex, write the move, unlock the mutex
                if(xSemaphoreTake(manualMoveMutex, (TickType_t)10) == pdTRUE) {
                    strcpy(pendingManualMove, param);
                    xSemaphoreGive(manualMoveMutex);
                }

                // Show the user's manual move on the OLED
                char displayMsg[50];
                snprintf(displayMsg, sizeof(displayMsg), "Manual: %s", param);
                xQueueSend(moveQueue, displayMsg, (TickType_t)10);
            }
        }
    }
    httpd_resp_send(req, "Move Received", HTTPD_RESP_USE_STRLEN);
    return ESP_OK;
}

// Endpoint: Python polls this to ask "Did the user type a move on their phone?"
static esp_err_t get_move_handler(httpd_req_t *req) {
    char response[50] = "NONE";
    
    if(xSemaphoreTake(manualMoveMutex, (TickType_t)10) == pdTRUE) {
        if(strlen(pendingManualMove) > 0) {
            strcpy(response, pendingManualMove);
            pendingManualMove[0] = '\0'; // Clear it after reading
        }
        xSemaphoreGive(manualMoveMutex);
    }
    
    httpd_resp_send(req, response, HTTPD_RESP_USE_STRLEN);
    return ESP_OK;
}


void startCameraServer() {
  httpd_config_t config = HTTPD_DEFAULT_CONFIG();
  config.core_id = 0; 
  
  // --- SERVER 1: TEXT COMMANDS & WEB APP (Port 80) ---
  config.server_port = 80;
  config.ctrl_port = 32768; 
  
  httpd_uri_t move_uri = { .uri = "/move", .method = HTTP_GET, .handler = move_handler, .user_ctx = NULL };
  httpd_uri_t root_uri = { .uri = "/", .method = HTTP_GET, .handler = root_handler, .user_ctx = NULL };
  httpd_uri_t submit_uri = { .uri = "/submit_move", .method = HTTP_GET, .handler = submit_handler, .user_ctx = NULL };
  httpd_uri_t get_uri = { .uri = "/get_move", .method = HTTP_GET, .handler = get_move_handler, .user_ctx = NULL };

  if (httpd_start(&command_httpd, &config) == ESP_OK) {
    httpd_register_uri_handler(command_httpd, &move_uri);
    httpd_register_uri_handler(command_httpd, &root_uri);
    httpd_register_uri_handler(command_httpd, &submit_uri);
    httpd_register_uri_handler(command_httpd, &get_uri);
  }

  // --- SERVER 2: VIDEO STREAM (Port 81) ---
  config.server_port = 81;
  config.ctrl_port = 32769; 
  
  httpd_uri_t stream_uri = { .uri = "/", .method = HTTP_GET, .handler = stream_handler, .user_ctx = NULL };
  if (httpd_start(&stream_httpd, &config) == ESP_OK) {
    httpd_register_uri_handler(stream_httpd, &stream_uri);
  }
}

// ==========================================
// CORE 1: HARDWARE & UI TASKS
// ==========================================

void oledUpdateTask(void *pvParameters) {
    char incomingMove[50];

    while(1) {
        if (xQueueReceive(moveQueue, &incomingMove, portMAX_DELAY) == pdPASS) {
            display.fillRect(0, 32, 128, 32, BLACK); 
            display.setCursor(0, 40);
            
            // Check if Python sent the "Camera Offline" command
            if (strncmp(incomingMove, "CMD:MANUAL", 10) == 0) {
                display.setTextSize(1);
                display.println("CAM OFFLINE. Go to:");
                display.println(WiFi.localIP()); // Tells the user where to point their phone
            } else {
                display.setTextSize(2);
                display.println(incomingMove);
            }
            display.display();
        }
    }
}

void healthLedTask(void *pvParameters) {
    pinMode(HEALTH_LED_PIN, OUTPUT);
    while(1) {
        digitalWrite(HEALTH_LED_PIN, HIGH);
        vTaskDelay(pdMS_TO_TICKS(100));
        digitalWrite(HEALTH_LED_PIN, LOW);
        vTaskDelay(pdMS_TO_TICKS(1900));
    }
}


// ==========================================
// MAIN SETUP
// ==========================================

void setup() {
  Serial.begin(115200);
  Wire.begin(SDA_PIN, SCL_PIN);

  // Initialize FreeRTOS Objects
  moveQueue = xQueueCreate(5, sizeof(char) * 50);
  manualMoveMutex = xSemaphoreCreateMutex();

  // Start Hardware UI Tasks pinned to Core 1
  xTaskCreatePinnedToCore(oledUpdateTask, "OLED Task", 4096, NULL, 1, NULL, 1);
  xTaskCreatePinnedToCore(healthLedTask, "Health LED Task", 1024, NULL, 1, NULL, 1);

  if(!display.begin(SSD1306_SWITCHCAPVCC, 0x3C)) { 
    Serial.println("OLED allocation failed");
    for(;;);
  }

  display.clearDisplay();
  display.setTextSize(1);
  display.setTextColor(WHITE);
  display.setCursor(0,0);
  display.println("Starting up...");
  display.display();

  WiFi.begin(ssid, password);
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
  }

  camera_config_t config;
  config.ledc_channel = LEDC_CHANNEL_0;
  config.ledc_timer = LEDC_TIMER_0;
  config.pin_d0 = Y2_GPIO_NUM;
  config.pin_d1 = Y3_GPIO_NUM;
  config.pin_d2 = Y4_GPIO_NUM;
  config.pin_d3 = Y5_GPIO_NUM;
  config.pin_d4 = Y6_GPIO_NUM;
  config.pin_d5 = Y7_GPIO_NUM;
  config.pin_d6 = Y8_GPIO_NUM;
  config.pin_d7 = Y9_GPIO_NUM;
  config.pin_xclk = XCLK_GPIO_NUM;
  config.pin_pclk = PCLK_GPIO_NUM;
  config.pin_vsync = VSYNC_GPIO_NUM;
  config.pin_href = HREF_GPIO_NUM;
  config.pin_sccb_sda = SIOD_GPIO_NUM;
  config.pin_sccb_scl = SIOC_GPIO_NUM;
  config.pin_pwdn = PWDN_GPIO_NUM;
  config.pin_reset = RESET_GPIO_NUM;
  config.xclk_freq_hz = 20000000;
  config.frame_size = FRAMESIZE_VGA; 
  config.pixel_format = PIXFORMAT_YUV422; 
  config.grab_mode = CAMERA_GRAB_LATEST;
  config.fb_location = CAMERA_FB_IN_PSRAM;
  config.fb_count = 2;

  esp_camera_init(&config);
  startCameraServer();

  display.clearDisplay();
  display.setCursor(0,0);
  display.setTextSize(1);
  display.println("System Ready!");
  display.println(WiFi.localIP());
  display.drawLine(0, 25, 128, 25, WHITE);
  display.setCursor(0, 40);
  display.setTextSize(2);
  display.println("Waiting...");
  display.display();
}

void loop() {
  vTaskDelete(NULL); 
}