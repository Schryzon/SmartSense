#include <WiFi.h>
#include <WiFiClientSecure.h>

#define MQTT_MAX_PACKET_SIZE 512

#include <PubSubClient.h>
#include <DHT.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
#include <time.h>
#include "secrets.h"

class KalmanFilter {
private:
    float q; // Process noise covariance
    float r; // Measurement noise covariance
    float x; // Estimated value
    float p; // Estimation error covariance
    float k; // Kalman gain

public:
    KalmanFilter(float process_noise, float sensor_noise, float estimated_error, float initial_value) {
        q = process_noise;
        r = sensor_noise;
        p = estimated_error;
        x = initial_value;
    }

    float update(float measurement) {
        k = p / (p + r);
        x = x + k * (measurement - x);
        p = (1.0f - k) * p + q;
        return x;
    }
};

struct TelemetryData {
    float temperature;
    float humidity;
    bool occupied;
};

QueueHandle_t telemetry_queue = NULL;
TaskHandle_t NetworkTaskHandle = NULL;
TaskHandle_t SensorTaskHandle = NULL;

/*
 * SmartSense
 *
 * DHT11  -> GPIO14
 * PIR    -> GPIO27
 *
 * GREEN  -> GPIO2
 * RED    -> GPIO4
 *
 * OLED SDA -> GPIO21
 * OLED SCL -> GPIO22
 */

#define DHT_PIN 14
#define DHT_TYPE DHT11

#define PIR_PIN 5

#define LED_GREEN 2
#define LED_RED 4

#define TEMP_ALERT_THRESHOLD 29.0

#define SCREEN_WIDTH 128
#define SCREEN_HEIGHT 64

const char* WIFI_SSID = SECRET_WIFI_SSID;
const char* WIFI_PASS = SECRET_WIFI_PASS;

const char* MQTT_HOST = SECRET_MQTT_HOST;

const uint16_t MQTT_PORT = SECRET_MQTT_PORT;

const char* MQTT_USER = SECRET_MQTT_USER;
const char* MQTT_PASS = SECRET_MQTT_PASS;

const char* TOPIC_TELEMETRY = SECRET_TOPIC_TELEMETRY;

const char* TOPIC_CMD = SECRET_TOPIC_CMD;

const char* DEVICE_ID = SECRET_DEVICE_ID;
const char* ROOM_NAME = SECRET_ROOM_NAME;

const unsigned long PUBLISH_INTERVAL_MS = 5000;

unsigned long last_publish_ms = 0;
bool red_led_state = false;

DHT dht(
    DHT_PIN,
    DHT_TYPE
);

Adafruit_SSD1306 display(
    SCREEN_WIDTH,
    SCREEN_HEIGHT,
    &Wire,
    -1
);

WiFiClientSecure secure_client;
PubSubClient mqtt_client(secure_client);

float last_temp = NAN;
float last_hum = NAN;

void connect_wifi();
void sync_time();
void connect_mqtt();

void mqtt_callback(
    char* topic,
    byte* payload,
    unsigned int length
);

void publish_telemetry();

void update_display(
    float temp,
    float hum,
    bool occupied
);

void sensor_task_fn(void* pvParameters);
void network_task_fn(void* pvParameters);
void publish_telemetry_data(TelemetryData data);

void setup() {
    Serial.begin(115200);
    delay(1000); // Wait for serial to stabilize

    Serial.println("\n--- SmartSense Diagnostics Boot ---");

    // Scan I2C bus
    Wire.begin();
    Serial.println("Scanning I2C...");
    byte error, address;
    int nDevices = 0;
    for(address = 1; address < 127; address++) {
        Wire.beginTransmission(address);
        error = Wire.endTransmission();
        if (error == 0) {
            Serial.printf("I2C device found at address 0x%02X\n", address);
            nDevices++;
        }
    }
    if (nDevices == 0) {
        Serial.println("No I2C devices found!");
    }

    pinMode(
        PIR_PIN,
        INPUT
    );

    pinMode(
        LED_GREEN,
        OUTPUT
    );

    pinMode(
        LED_RED,
        OUTPUT
    );

    digitalWrite(
        LED_GREEN,
        LOW
    );

    digitalWrite(
        LED_RED,
        LOW
    );

    dht.begin();
    Serial.printf("DHT Pin (GPIO %d) state on boot: %d\n", DHT_PIN, digitalRead(DHT_PIN));
    Serial.println("----------------------------------\n");

    if(
        !display.begin(
            SSD1306_SWITCHCAPVCC,
            0x3C
        )
    ) {
        Serial.println(
            "SSD1306 init failed"
        );

        while(true);
    }

    display.clearDisplay();
    display.setTextColor(WHITE);

    display.setTextSize(2);
    display.setCursor(0, 0);
    display.println("Smart");

    display.setCursor(0, 20);
    display.println("Sense");

    display.setTextSize(1);
    display.setCursor(0, 52);
    display.println("Booting...");

    display.display();

    WiFi.mode(WIFI_STA);

    connect_wifi();
    sync_time();

    secure_client.setInsecure();

    mqtt_client.setServer(
        MQTT_HOST,
        MQTT_PORT
    );

    mqtt_client.setCallback(
        mqtt_callback
    );

    // Create queue to hold telemetry packets (up to 5)
    telemetry_queue = xQueueCreate(5, sizeof(TelemetryData));

    // Spawn network task on Core 0 (handles Wi-Fi, MQTT loop, keepalive)
    xTaskCreatePinnedToCore(
        network_task_fn,
        "NetworkTask",
        8192,
        NULL,
        1,
        &NetworkTaskHandle,
        0
    );

    // Spawn sensor task on Core 1 (handles DHT, PIR, OLED, LEDs, Kalman Filter)
    xTaskCreatePinnedToCore(
        sensor_task_fn,
        "SensorTask",
        4096,
        NULL,
        1,
        &SensorTaskHandle,
        1
    );
}

void loop() {
    // Arduino loop task sleeps to yield Core 1 execution time
    vTaskDelay(pdMS_TO_TICKS(1000));
}

void connect_wifi() {
    Serial.print(
        "Connecting WiFi"
    );

    WiFi.begin(
        WIFI_SSID,
        WIFI_PASS
    );

    while(
        WiFi.status() != WL_CONNECTED
    ) {
        delay(500);
        Serial.print(".");
    }

    Serial.println();

    Serial.print("IP: ");
    Serial.println(
        WiFi.localIP()
    );
}

void sync_time() {
    configTime(
        0,
        0,
        "pool.ntp.org",
        "time.nist.gov"
    );

    struct tm timeinfo;

    for(
        int i = 0;
        i < 20;
        ++i
    ) {
        if(
            getLocalTime(
                &timeinfo
            )
        ) {
            Serial.println(
                "Time synced"
            );

            return;
        }

        delay(500);
    }

    Serial.println(
        "Time sync failed"
    );
}

void connect_mqtt() {
    while(
        !mqtt_client.connected()
    ) {
        String client_id =
            "esp32-";

        client_id +=
            String(
                (uint32_t)
                ESP.getEfuseMac(),
                HEX
            );

        Serial.print(
            "MQTT..."
        );

        char will_msg[128];
        snprintf(
            will_msg,
            sizeof(will_msg),
            "{\"device_id\":\"%s\",\"status\":\"offline\"}",
            DEVICE_ID
        );

        // Connect with Last Will and Testament Will parameters
        if(
            mqtt_client.connect(
                client_id.c_str(),
                MQTT_USER,
                MQTT_PASS,
                TOPIC_TELEMETRY,
                1,
                true,
                will_msg
            )
        ) {
            Serial.println(
                "connected"
            );

            mqtt_client.subscribe(
                TOPIC_CMD
            );
        }
        else {
            Serial.print(
                "failed rc="
            );

            Serial.println(
                mqtt_client.state()
            );

            // yield Core 0 while waiting
            vTaskDelay(pdMS_TO_TICKS(2000));
        }
    }
}

void mqtt_callback(
    char* topic,
    byte* payload,
    unsigned int length
) {
    String msg;

    for(
        unsigned int i = 0;
        i < length;
        ++i
    ) {
        msg +=
            (char)payload[i];
    }

    Serial.print(
        "CMD: "
    );

    Serial.println(
        msg
    );
}

void update_display(
    float temp,
    float hum,
    bool occupied
) {
    display.clearDisplay();

    display.setTextSize(1);

    display.setCursor(0, 0);
    display.println("SmartSense");

    display.drawLine(
        0,
        10,
        127,
        10,
        WHITE
    );

    display.setTextSize(2);

    display.setCursor(0, 16);
    display.print(temp, 1);
    display.print("C");

    display.setTextSize(1);

    display.setCursor(0, 40);

    display.print("Hum: ");
    display.print(hum, 0);
    display.print("%");

    display.setCursor(0, 52);

    if(occupied) {
        display.print("USED");
    }
    else {
        display.print("EMPTY");
    }

    if(
        temp >=
        TEMP_ALERT_THRESHOLD
    ) {
        display.setCursor(
            60,
            52
        );

        display.print(
            "HOT!"
        );
    }

    display.display();
}

void publish_telemetry_data(TelemetryData data) {
    int wifi_rssi = WiFi.RSSI();
    unsigned long uptime_s = millis() / 1000UL;
    time_t now_ts = time(nullptr);

    if (now_ts < 1700000000) {
        now_ts = uptime_s;
    }

    char payload[512];
    snprintf(
        payload,
        sizeof(payload),
        "{"
        "\"device_id\":\"%s\","
        "\"room\":\"%s\","
        "\"temperature\":%.1f,"
        "\"humidity\":%.1f,"
        "\"occupied\":%d,"
        "\"wifi_rssi\":%d,"
        "\"uptime\":%lu,"
        "\"temp_alert\":%s,"
        "\"ts\":%lu"
        "}",
        DEVICE_ID,
        ROOM_NAME,
        data.temperature,
        data.humidity,
        data.occupied ? 1 : 0,
        wifi_rssi,
        uptime_s,
        (data.temperature >= TEMP_ALERT_THRESHOLD) ? "true" : "false",
        (unsigned long)now_ts
    );

    Serial.println(payload);
    bool ok = mqtt_client.publish(TOPIC_TELEMETRY, payload);
    Serial.println(ok ? "Publish OK" : "Publish FAILED");
}

void sensor_task_fn(void* pvParameters) {
    // Local filter instances with appropriate noise parameters
    // Process noise q for temp is low (slow change); sensor noise r for DHT11 is high (jittery)
    KalmanFilter temp_filter(0.005f, 1.0f, 1.0f, 25.0f);
    KalmanFilter hum_filter(0.05f, 4.0f, 4.0f, 50.0f);

    while (true) {
        float raw_temp = dht.readTemperature();
        float raw_hum = dht.readHumidity();

        if (!isnan(raw_temp) && !isnan(raw_hum)) {
            last_temp = temp_filter.update(raw_temp);
            last_hum = hum_filter.update(raw_hum);
        } else {
            Serial.println("DHT failed to read");
        }

        bool occupied = digitalRead(PIR_PIN);

        // Green LED tracks occupancy
        digitalWrite(LED_GREEN, occupied);

        // Red LED alert on high temperature (toggle alert state)
        if (!isnan(last_temp)) {
            if (last_temp >= TEMP_ALERT_THRESHOLD) {
                red_led_state = !red_led_state;
                digitalWrite(LED_RED, red_led_state);
            } else {
                digitalWrite(LED_RED, LOW);
            }
        }

        // Render to SSD1306 display
        if (!isnan(last_temp) && !isnan(last_hum)) {
            update_display(last_temp, last_hum, occupied);
        }

        // Push formatted data to the network queue
        if (!isnan(last_temp) && !isnan(last_hum)) {
            TelemetryData data;
            data.temperature = last_temp;
            data.humidity = last_hum;
            data.occupied = occupied;
            xQueueSend(telemetry_queue, &data, 0); // non-blocking push
        }

        // poll sensors at 2-second intervals
        vTaskDelay(pdMS_TO_TICKS(2000));
    }
}

void network_task_fn(void* pvParameters) {
    while (true) {
        if (WiFi.status() != WL_CONNECTED) {
            connect_wifi();
        }

        if (!mqtt_client.connected()) {
            connect_mqtt();
        }

        mqtt_client.loop();

        TelemetryData data;
        // Wait up to 5 seconds for incoming telemetry packets from queue
        if (xQueueReceive(telemetry_queue, &data, pdMS_TO_TICKS(5000)) == pdPASS) {
            publish_telemetry_data(data);
        }

        vTaskDelay(pdMS_TO_TICKS(50));
    }
}