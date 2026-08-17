#include "esp_camera.h"
#include <WiFi.h>
#include <Wire.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
#include "esp_http_server.h"

// 1. UPDATE YOUR WI-FI CREDENTIALS HERE
const char* ssid = "HH40_6CA7-2.4";
const char* password = "80028244";

// 2. OLED DISPLAY SETTINGS
#define SCREEN_WIDTH 128
#define SCREEN_HEIGHT 64
#define OLED_RESET -1
#define SDA_PIN 1
#define SCL_PIN 2
Adafruit_SSD1306 display(SCREEN_WIDTH, SCREEN_HEIGHT, &Wire, OLED_RESET);

// 3. FREENOVE ESP32-S3 CAMERA PINS
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

httpd_handle_t stream_httpd = NULL;

// 4. VIDEO STREAMING SERVER LOGIC
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

// --- NEW CODE: HTTP ENDPOINT TO RECEIVE MOVES ---
static esp_err_t move_handler(httpd_req_t *req) {
    char buf[100];
    int ret, len = httpd_req_get_url_query_len(req) + 1;
    if (len > 1) {
        if (httpd_req_get_url_query_str(req, buf, len) == ESP_OK) {
            char param[50];
            if (httpd_query_key_value(buf, "text", param, sizeof(param)) == ESP_OK) {
                // Wipe the bottom half of the screen and print the new move
                display.fillRect(0, 32, 128, 32, BLACK); 
                display.setCursor(0, 40);
                display.setTextSize(2);
                display.println(param);
                display.display();
                Serial.printf("Received move: %s\n", param);
            }
        }
    }
    httpd_resp_send(req, "OK", HTTPD_RESP_USE_STRLEN);
    return ESP_OK;
}

httpd_handle_t command_httpd = NULL; // Add this new handle

void startCameraServer() {
  httpd_config_t config = HTTPD_DEFAULT_CONFIG();
  
  // --- SERVER 1: TEXT COMMANDS (Port 80) ---
  config.server_port = 80;
  config.ctrl_port = 32768; // Default control port
  
  httpd_uri_t move_uri = {
    .uri       = "/move",
    .method    = HTTP_GET,
    .handler   = move_handler,
    .user_ctx  = NULL
  };
  
  if (httpd_start(&command_httpd, &config) == ESP_OK) {
    httpd_register_uri_handler(command_httpd, &move_uri);
  }

  // --- SERVER 2: VIDEO STREAM (Port 81) ---
  config.server_port = 81;
  config.ctrl_port = 32769; // Shift control port so they don't clash!
  
  httpd_uri_t stream_uri = {
    .uri       = "/",
    .method    = HTTP_GET,
    .handler   = stream_handler,
    .user_ctx  = NULL
  };
  
  if (httpd_start(&stream_httpd, &config) == ESP_OK) {
    httpd_register_uri_handler(stream_httpd, &stream_uri);
  }
}

// 5. MAIN SETUP
void setup() {
  Serial.begin(115200);
  Wire.begin(SDA_PIN, SCL_PIN);
  
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
  // Keep the faster 20MHz clock speed
  config.xclk_freq_hz = 20000000;
  
  config.frame_size = FRAMESIZE_VGA; 
  
  // YUV422 is faster for the ESP32 CPU to convert to JPEG than RGB565
  config.pixel_format = PIXFORMAT_YUV422; 
  
  config.grab_mode = CAMERA_GRAB_LATEST;
  config.fb_location = CAMERA_FB_IN_PSRAM;
  config.fb_count = 2;
  // (Note: config.jpeg_quality does nothing here since we aren't using PIXFORMAT_JPEG)

  esp_camera_init(&config);
  startCameraServer();

  // Print initial UI to OLED
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
  delay(10000); 
}