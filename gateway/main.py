import os
import ssl
import json
import sqlite3
import threading
from datetime import datetime
from dotenv import load_dotenv

import paho.mqtt.client as mqtt
import pandas as pd
import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

# import custom PyTorch model pipeline
from model import ForecastingPipeline, calculate_comfort_score

# load environment variables from .env
load_dotenv()

MQTT_HOST = os.getenv("MQTT_HOST")
MQTT_PORT = int(os.getenv("MQTT_PORT", 8883))
MQTT_USER = os.getenv("MQTT_USER")
MQTT_PASS = os.getenv("MQTT_PASS")
MQTT_CLIENT_ID = os.getenv("MQTT_CLIENT_ID", "gateway_bot")
MQTT_TELEMETRY_TOPIC = os.getenv("MQTT_TELEMETRY_TOPIC", "iot/classA/group01/telemetry")

if not MQTT_HOST or not MQTT_USER or not MQTT_PASS:
    raise ValueError("Missing required MQTT configurations (MQTT_HOST, MQTT_USER, or MQTT_PASS) in .env file.")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

# local SQLite path
DB_PATH = os.path.join(os.path.dirname(__file__), "telemetry.db")

# global state variables
recent_buffer = []  # format: [[temp, hum, occupied]]
last_received_payload = {}
new_data_counter = 0
training_lock = threading.Lock()

# instantiate PyTorch model pipeline
forecaster = ForecastingPipeline(window_size=10)

# SQLite Database Helper Functions
def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS telemetry (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            temperature REAL NOT NULL,
            humidity REAL NOT NULL,
            occupied INTEGER NOT NULL,
            comfort_score REAL NOT NULL
        )
    """)
    conn.commit()
    conn.close()

def insert_telemetry(temp: float, hum: float, occupied: int, comfort: float):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO telemetry (timestamp, temperature, humidity, occupied, comfort_score)
        VALUES (?, ?, ?, ?, ?)
    """, (datetime.now().isoformat(), temp, hum, occupied, comfort))
    conn.commit()
    conn.close()

def fetch_history(limit: int = 100) -> list:
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT timestamp, temperature, humidity, occupied, comfort_score 
        FROM telemetry 
        ORDER BY id DESC LIMIT ?
    """, (limit,))
    rows = cursor.fetchall()
    conn.close()

    history = []
    for r in rows:
        history.append({
            "timestamp": r[0],
            "temperature": r[1],
            "humidity": r[2],
            "occupied": r[3],
            "comfort_score": r[4]
        })
    return history

def get_history_df() -> pd.DataFrame:
    conn = sqlite3.connect(DB_PATH)
    try:
        df = pd.read_sql_query("SELECT temperature, humidity, occupied FROM telemetry ORDER BY id ASC", conn)
        return df
    finally:
        conn.close()

# load historical data into memory buffer at startup
init_db()
try:
    history_records = fetch_history(limit=100)
    # reverse because they were fetched DESC
    history_records.reverse()
    for row in history_records:
        recent_buffer.append([row["temperature"], row["humidity"], row["occupied"]])
    print(f"warmed up memory buffer with {len(recent_buffer)} records from SQLite database.")
except Exception as e:
    print(f"could not load telemetry history from SQLite: {e}")

# Async training trigger
def train_model_async():
    def train_task():
        if not training_lock.acquire(blocking=False):
            return
        try:
            df = get_history_df()
            forecaster.train(df)
        finally:
            training_lock.release()

    threading.Thread(target=train_task, daemon=True).start()

# MQTT Callbacks
def on_connect(client, userdata, flags, rc, properties=None):
    if rc == 0:
        print("MQTT Connected successfully!")
        client.subscribe(MQTT_TELEMETRY_TOPIC)
    else:
        print(f"MQTT Connection failed with code {rc}")

def on_message(client, userdata, msg):
    global last_received_payload, new_data_counter, recent_buffer
    try:
        payload = json.loads(msg.payload.decode("utf-8"))
        last_received_payload = payload

        temp = float(payload.get("temperature", 25.0))
        hum = float(payload.get("humidity", 50.0))
        occupied = int(payload.get("occupied", 0))
        comfort = calculate_comfort_score(temp, hum)

        # save directly to SQLite datastore
        insert_telemetry(temp, hum, occupied, comfort)

        # update rolling memory buffer
        recent_buffer.append([temp, hum, occupied])
        if len(recent_buffer) > 100:
            recent_buffer.pop(0)

        # periodically retrain the model on the GPU (every 15 new payloads)
        new_data_counter += 1
        if new_data_counter >= 15:
            new_data_counter = 0
            print("triggering scheduled PyTorch training on GPU...")
            train_model_async()

    except Exception as e:
        # let it crash to output logs, Jay values truth over safety
        print(f"error parsing incoming MQTT payload: {e}")

# FastAPI web app setup
web_app = FastAPI()

@web_app.get("/", response_class=HTMLResponse)
async def get_dashboard():
    html_path = os.path.join(os.path.dirname(__file__), "index.html")
    with open(html_path, "r", encoding="utf-8") as f:
        return f.read()

@web_app.get("/api/current")
async def get_current():
    if not last_received_payload:
        return {}
    
    temp = last_received_payload.get("temperature", 25.0)
    hum = last_received_payload.get("humidity", 50.0)
    return {
        "temperature": temp,
        "humidity": hum,
        "occupied": last_received_payload.get("occupied", 0),
        "comfort_score": calculate_comfort_score(temp, hum),
        "room": last_received_payload.get("room", "Unknown"),
        "uptime": last_received_payload.get("uptime", 0)
    }

@web_app.get("/api/history")
async def get_history_api():
    return fetch_history(limit=50)

@web_app.get("/api/forecast")
async def get_forecast_api():
    if len(recent_buffer) < 10:
        return {"model_trained": False}
    try:
        pred_temp, pred_hum = forecaster.predict_next(recent_buffer)
        pred_comfort = calculate_comfort_score(pred_temp, pred_hum)
        return {
            "model_trained": forecaster.is_trained,
            "predicted_temperature": pred_temp,
            "predicted_humidity": pred_hum,
            "predicted_comfort_score": pred_comfort
        }
    except Exception as e:
        return {"model_trained": False, "error": str(e)}

@web_app.post("/api/train")
async def train_model_api():
    df = get_history_df()
    if df is None or len(df) < 15:
        return {"status": "error", "message": f"Need at least 15 historical points (currently {0 if df is None else len(df)})"}
    try:
        # train model on GPU
        forecaster.train(df)
        return {"status": "success"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

# Telegram Command Handlers
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "👋 *Welcome to SmartSense Control Panel!*\n\n"
        "Available commands:\n"
        "• `/status` - View current room environment & comfort score.\n"
        "• `/predict` - Predict room conditions using PyTorch on CUDA.\n"
        "• `/comfort` - Explanation of the Comfort Score rating."
    )
    await update.message.reply_text(msg, parse_mode="Markdown")

async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not last_received_payload:
        await update.message.reply_text("No telemetry received from the ESP32 yet. 🐟")
        return

    temp = last_received_payload.get("temperature", 25.0)
    hum = last_received_payload.get("humidity", 50.0)
    occupied = "YES" if last_received_payload.get("occupied", 0) else "NO"
    comfort = calculate_comfort_score(temp, hum)

    msg = (
        f"🏠 *SmartSense Status* (Room: {last_received_payload.get('room', 'Unknown')}):\n"
        f"• *Temperature*: {temp:.1f}°C\n"
        f"• *Humidity*: {hum:.1f}%\n"
        f"• *Occupied*: {occupied}\n"
        f"• *Comfort Score*: {comfort:.1f}/10.0\n"
        f"• *Uptime*: {last_received_payload.get('uptime', 0)}s"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")

async def predict_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(recent_buffer) < 10:
        await update.message.reply_text("Insufficient local history. Need at least 10 entries. 🐟")
        return

    try:
        pred_temp, pred_hum = forecaster.predict_next(recent_buffer)
        pred_comfort = calculate_comfort_score(pred_temp, pred_hum)

        warning_suffix = ""
        if pred_comfort < 5.0:
            warning_suffix = "\n\n⚠️ *Warning*: Uncomfortable conditions predicted soon!"

        msg = (
            f"🔮 *PyTorch GPU Forecast (LSTM)*:\n"
            f"• *Predicted Temp*: {pred_temp:.1f}°C\n"
            f"• *Predicted Humidity*: {pred_hum:.1f}%\n"
            f"• *Predicted Comfort Score*: {pred_comfort:.1f}/10.0"
            f"{warning_suffix}"
        )
        await update.message.reply_text(msg, parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"could not forecast: {e}")

async def comfort_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "📊 *Comfort Score Scale (0.0 to 10.0)*\n\n"
        "• *9.0 - 10.0*: Optimal Room Comfort.\n"
        "• *7.0 - 8.9*: Comfortable.\n"
        "• *5.0 - 6.9*: Mild discomfort (slightly warm/cold/humid).\n"
        "• *Below 5.0*: Highly Uncomfortable. Action recommended!"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")

def start_web_server():
    # run uvicorn web server in background thread silently
    uvicorn.run(web_app, host="0.0.0.0", port=8000, log_level="warning")

def main():
    # 1. Start SQLite DB
    init_db()

    # 2. Start MQTT background client loop
    mqtt_client = mqtt.Client(
        client_id=MQTT_CLIENT_ID, 
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2
    )
    mqtt_client.tls_set(cert_reqs=ssl.CERT_NONE)
    mqtt_client.username_pw_set(MQTT_USER, MQTT_PASS)
    
    mqtt_client.on_connect = on_connect
    mqtt_client.on_message = on_message

    print(f"Connecting to HiveMQ Cloud at {MQTT_HOST}:{MQTT_PORT}...")
    mqtt_client.connect(MQTT_HOST, MQTT_PORT)
    mqtt_client.loop_start()

    # Try training once initially if data exists in DB
    train_model_async()

    # 3. Start FastAPI Web Server in a daemon background thread
    print("Launching FastAPI Web Server on http://localhost:8000...")
    threading.Thread(target=start_web_server, daemon=True).start()

    # 4. Start Telegram Bot polling (runs blocking loop in main thread)
    if not TELEGRAM_BOT_TOKEN:
        print("TELEGRAM_BOT_TOKEN not found in environment. Telegram Bot is disabled.")
        import time
        while True:
            time.sleep(1)
    else:
        app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
        app.add_handler(CommandHandler("start", start_cmd))
        app.add_handler(CommandHandler("status", status_cmd))
        app.add_handler(CommandHandler("predict", predict_cmd))
        app.add_handler(CommandHandler("comfort", comfort_cmd))

        print("Telegram Bot listener started... wiggle wiggle! 🐟")
        app.run_polling()

if __name__ == "__main__":
    main()
