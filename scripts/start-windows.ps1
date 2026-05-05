$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Backend = Join-Path $Root "backend"
$Frontend = Join-Path $Root "frontend"

Write-Host "Starting MeetingMind Agent Pro on Windows..."

if (!(Test-Path (Join-Path $Backend ".venv"))) {
  Write-Host "Creating backend virtual environment..."
  python -m venv (Join-Path $Backend ".venv")
}

Write-Host "Installing backend dependencies..."
Set-Location $Backend
& .\.venv\Scripts\python.exe -m pip install --upgrade pip
& .\.venv\Scripts\pip.exe install -r requirements.txt

Write-Host "Installing frontend dependencies..."
Set-Location $Frontend
npm install

Write-Host "Launching backend at http://127.0.0.1:8000"
Start-Process powershell -ArgumentList "-NoExit", "-Command", "Set-Location '$Backend'; .\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000"

Write-Host "Launching frontend at http://127.0.0.1:5173"
Start-Process powershell -ArgumentList "-NoExit", "-Command", "Set-Location '$Frontend'; npm run dev"

Write-Host ""
Write-Host "Open http://127.0.0.1:5173 in your browser."
Write-Host "Default admin account: admin / admin123"
