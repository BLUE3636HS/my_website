# my_website

Git の管理ルートはこの `my_website` フォルダです。アプリ本体は `main/`（SQLite 版）にあります。

## セットアップ

Python 3.12 がインストールされている状態で、初回だけリポジトリのルートから仮想環境を作成して依存関係を入れます。

```powershell
$python312 = Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\python.exe"
& $python312 -m venv main\.venv
main\.venv\Scripts\python.exe -m pip install -r main\requirements-dev.txt
```

## 起動

```powershell
Set-Location main
.\.venv\Scripts\python.exe -m uvicorn main:app --reload
```

既存データは `main/database/database.db` に、既存アップロードは `main/uploads/` にあります。前日予約通知は `main/scripts/create_reservation_reminders.py` で実行できます。

`git_save.bat` はリポジトリ全体を対象にします。

## テスト

```powershell
Set-Location main
.\.venv\Scripts\python.exe -m unittest discover -s tests -q
```
