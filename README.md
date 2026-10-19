# my_website の2つの版

Git の管理ルートはこの `my_website` フォルダです。`main/` は従来の SQLite 版、`neon_version/` は Neon PostgreSQL 版です。コードとアップロードはそれぞれ独立して管理します。

## SQLite 版

Python 3.12 がインストールされている状態で、初回だけリポジトリのルートから仮想環境を作成して依存関係を入れます。

```powershell
$python312 = Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\python.exe"
& $python312 -m venv main\.venv
main\.venv\Scripts\python.exe -m pip install -r main\requirements-dev.txt
```

起動時は次を実行します。

```powershell
Set-Location main
.\.venv\Scripts\python.exe -m uvicorn main:app --reload
```

既存データは `main/database/database.db` に残っています。既存アップロードも `main/uploads/` にあります。

## Neon 版

`DATABASE_URL` を Neon の PostgreSQL 接続文字列に設定します。値をコード、`.env.example`、Git、チャットに記載しないでください。Neon 版も `main\.venv` を使用するため、同じ環境に Neon 用依存パッケージを追加してから、専用で空の Neon DB に対して初期化コマンドを実行します。

```powershell
$secureUrl = Read-Host "Neon DATABASE_URL" -AsSecureString
$env:DATABASE_URL = [System.Net.NetworkCredential]::new("", $secureUrl).Password
main\.venv\Scripts\python.exe -m pip install -r neon_version\requirements-dev.txt
Set-Location neon_version
..\main\.venv\Scripts\python.exe init_db.py
..\main\.venv\Scripts\python.exe verify_db.py
..\main\.venv\Scripts\python.exe create_admin.py
..\main\.venv\Scripts\python.exe -m uvicorn main:app --reload --port 8001
```

`init_db.py`、`verify_db.py`、`create_admin.py` を単独で起動して `DATABASE_URL` が未設定の場合は、接続 URL の非表示入力が出ます。Neon 版のアップロードは `neon_version/uploads/` に新規作成されます。SQLite データやファイルは Neon に移行しません。前日予約通知は各版の `scripts/create_reservation_reminders.py` で実行できます。

`git_save.bat` はリポジトリ全体を対象にします。2版の変更をまとめて保存する際に使用してください。

## テスト

```powershell
Set-Location main
.\.venv\Scripts\python.exe -m unittest discover -s tests -q
Set-Location ..\neon_version
..\main\.venv\Scripts\python.exe -m unittest discover -s tests -q
```
