$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot

foreach ($name in @('SESSION_SECRET', 'GOOGLE_CALENDAR_CLIENT_ID', 'GOOGLE_CALENDAR_CLIENT_SECRET', 'GOOGLE_CALENDAR_REFRESH_TOKEN', 'GOOGLE_CALENDAR_ID')) {
    Remove-Item "Env:$name" -ErrorAction SilentlyContinue
}
$env:PYTHONDONTWRITEBYTECODE = '1'
$previewPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (!(Test-Path -LiteralPath $previewPython)) {
    throw 'Create the UI_codex virtual environment first. See README.md.'
}
& $previewPython seed_demo.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $previewPython -m uvicorn main:app --host 127.0.0.1 --port 8010
exit $LASTEXITCODE
