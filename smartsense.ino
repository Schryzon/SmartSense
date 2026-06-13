#include <WiFi.h>
#include <WiFiClientSecure.h>

#define MQTT_MAX_PACKET_SIZE 512

#include <PubSubClient.h>
#include <DHT.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
#include <time.h>
#include "secrets.h"

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

#define PIR_PIN 27

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

void setup() {
    Serial.begin(115200);

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

    connect_mqtt();

    delay(2000);
}

void loop() {
    if(
        WiFi.status() != WL_CONNECTED
    ) {
        connect_wifi();
    }

    if(
        !mqtt_client.connected()
    ) {
        connect_mqtt();
    }

    mqtt_client.loop();

    unsigned long now_ms =
        millis();

    if(
        now_ms - last_publish_ms >=
        PUBLISH_INTERVAL_MS
    ) {
        last_publish_ms =
            now_ms;

        publish_telemetry();
    }
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

        if(
            mqtt_client.connect(
                client_id.c_str(),
                MQTT_USER,
                MQTT_PASS
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

            delay(2000);
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

void publish_telemetry() {
    float temp =
        dht.readTemperature();

    float hum =
        dht.readHumidity();

    if(
        !isnan(temp) &&
        !isnan(hum)
    ) {
        last_temp = temp;
        last_hum = hum;
    }

    if(
        isnan(last_temp) ||
        isnan(last_hum)
    ) {
        Serial.println(
            "DHT failed"
        );

        return;
    }

    bool occupied =
        digitalRead(
            PIR_PIN
        );

    digitalWrite(
        LED_GREEN,
        occupied
    );

    if(
        last_temp >=
        TEMP_ALERT_THRESHOLD
    ) {
        red_led_state =
            !red_led_state;

        digitalWrite(
            LED_RED,
            red_led_state
        );
    }
    else {
        digitalWrite(
            LED_RED,
            LOW
        );
    }

    update_display(
        last_temp,
        last_hum,
        occupied
    );

    int wifi_rssi =
        WiFi.RSSI();

    unsigned long uptime_s =
        millis() / 1000UL;

    time_t now_ts =
        time(nullptr);

    if(
        now_ts <
        1700000000
    ) {
        now_ts =
            uptime_s;
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
        last_temp,
        last_hum,
        occupied,
        wifi_rssi,
        uptime_s,
        (
            last_temp >=
            TEMP_ALERT_THRESHOLD
        )
        ? "true"
        : "false",
        (unsigned long)
        now_ts
    );

    Serial.println(
        payload
    );

    bool ok =
        mqtt_client.publish(
            TOPIC_TELEMETRY,
            payload
        );

    Serial.println(
        ok
        ? "Publish OK"
        : "Publish FAILED"
    );
}