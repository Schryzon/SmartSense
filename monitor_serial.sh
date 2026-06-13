#!/usr/bin/env bash
# monitor_serial.sh
# Helper script to monitor ESP32 serial output on Linux / macOS

# find matching serial ports
ports=($(ls /dev/ttyUSB* /dev/ttyACM* /dev/tty.usbserial-* /dev/tty.usbmodem* 2>/dev/null))

if [ ${#ports[@]} -eq 0 ]; then
    echo "Warning: No active USB serial ports found in /dev/."
    exit 1
fi

port_name=${ports[0]}

if [ ${#ports[@]} -gt 1 ]; then
    echo "Multiple serial ports detected:"
    for i in "${!ports[@]}"; do
        echo "  [$i] ${ports[$i]}"
    done
    read -p "Select port index (default 0): " choice
    if [[ "$choice" =~ ^[0-9]+$ ]] && [ "$choice" -lt "${#ports[@]}" ]; then
        port_name=${ports[$choice]}
    fi
fi

echo "Monitoring $port_name at 115200 baud."

# Fallback sequence of serial tools
if python3 -c "import serial" 2>/dev/null; then
    echo "Using python miniterm..."
    python3 -m serial.tools.miniterm "$port_name" 115200
elif command -v tio &> /dev/null; then
    echo "Using tio..."
    tio -b 115200 "$port_name"
elif command -v screen &> /dev/null; then
    echo "Using screen (Press Ctrl+A then Ctrl+K to exit)..."
    screen "$port_name" 115200
else
    echo "No specialized serial monitors found. Falling back to cat (requires stty)..."
    stty -F "$port_name" 115200 raw -echo
    cat "$port_name"
fi
