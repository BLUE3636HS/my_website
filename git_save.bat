@echo off
chcp 65001 > nul

echo ==============================
echo GitHubへ保存します
echo ==============================

git status

echo.
echo GitHubへ保存しています...

git add .

for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMddHHmmss"') do set COMMIT_MESSAGE=%%i

echo.
echo コミットメッセージ: %COMMIT_MESSAGE%

git commit -m "%COMMIT_MESSAGE%"

if errorlevel 1 (
    echo.
    echo コミットに失敗しました。
    pause
    exit /b
)

git push

echo.
echo ==============================
echo GitHubへの保存が完了しました
echo ==============================

pause