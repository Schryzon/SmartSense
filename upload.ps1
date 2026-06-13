# upload.ps1
# Script to compile and upload smartsense.ino to ESP32

Param(
    [string]$Port = "COM7",
    [string]$Fqbn = "esp32:esp32:esp32"
)

$ErrorActionPreference = "Stop"

Write-Host "===============================================" -ForegroundColor Cyan
Write-Host "          SmartSense Firmware Uploader         " -ForegroundColor Cyan
Write-Host "===============================================" -ForegroundColor Cyan
Write-Host "Target Board : $Fqbn"
Write-Host "Target Port  : $Port"
Write-Host ""

Write-Host "[1/2] Compiling sketch..." -ForegroundColor Yellow
& arduino compile --fqbn $Fqbn smartsense.ino

if ($LASTEXITCODE -ne 0) {
    Write-Error "Compilation failed."
    Exit $LASTEXITCODE
}
Write-Host "✔ Compilation successful!`n" -ForegroundColor Green

Write-Host "[2/2] Uploading firmware to $Port..." -ForegroundColor Yellow
& arduino upload -p $Port --fqbn $Fqbn smartsense.ino

if ($LASTEXITCODE -ne 0) {
    Write-Error "Upload failed. Please check if the port is busy or the board is connected."
    Exit $LASTEXITCODE
}

Write-Host "✔ Upload successful! ESP32 reset and running." -ForegroundColor Green
Write-Host "===============================================" -ForegroundColor Cyan
