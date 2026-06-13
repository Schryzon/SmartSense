# monitor_serial.ps1
# Helper script to monitor ESP32 serial output on Windows

$ports = [System.IO.Ports.SerialPort]::GetPortNames()

if ($ports.Count -eq 0) {
    Write-Warning "No active COM ports detected. Please plug in your ESP32."
    Exit
}

$port_name = $ports[0]
if ($ports.Count -gt 1) {
    Write-Host "Multiple COM ports detected:"
    for ($i = 0; $i -lt $ports.Count; $i++) {
        Write-Host "  [$i] $($ports[$i])"
    }
    $choice = Read-Host "Select COM port index (default 0)"
    if ($choice -match '^\d+$' -and [int]$choice -lt $ports.Count) {
        $port_name = $ports[[int]$choice]
    }
}

Write-Host "Opening $port_name at 115200 baud..."
Write-Host "Press Ctrl + C to stop monitoring."

$port = New-Object System.IO.Ports.SerialPort $port_name, 115200, None, 8, one

try {
    $port.Open()
    # clean out any initial garbage data
    $port.DiscardInBuffer()
    
    while ($port.IsOpen) {
        if ($port.BytesToRead -gt 0) {
            $line = $port.ReadLine()
            Write-Host $line
        }
        # keep CPU utilization low
        Start-Sleep -Milliseconds 10
    }
}
catch {
    Write-Error "Error communicating with serial port: $_"
}
finally {
    if ($port -and $port.IsOpen) {
        $port.Close()
        Write-Host "`nClosed port $port_name cleanly. Port is now free."
    }
}
