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

### 管理ウィンドウから操作する

リポジトリ直下の `menu.bat` をダブルクリックすると、ボタン付きの管理ウィンドウ（本体は `menu.ps1`）が開きます。

* サーバーの状態を「起動中 / 停止中」で表示します（ポート 8000 を2秒ごとに確認）。
* サーバー起動：`--reload` 付きで起動し、ログは「サーバーログ」タブに表示します（別ウィンドウは開きません）。ブラウザは開かないので http://127.0.0.1:8000 に自分でアクセスします。
* サーバー停止：サーバーのプロセスをまとめて止めます。
* テスト実行：結果を「テスト・保存」タブに表示します。成功は緑、失敗は赤で結果の行を出します。
* GitHubへ保存：変更の一覧を確認してから `git add` → `git commit` → `git push` を行い、出力をウィンドウ内に表示します（`git_save.bat` と同じ処理）。

管理ウィンドウから起動したサーバーは、ウィンドウを閉じると一緒に停止します（閉じる前に確認します）。

## テスト

```powershell
Set-Location main
.\.venv\Scripts\python.exe -m unittest discover -s tests -q
```
