# TEKNE 生徒向けUI試用版

リポジトリ直下から以下を実行します。このPCでは Python 3.14.5 を使って動作を確認しています。仮想環境がある場合は起動手順に進んでください。

```powershell
Set-Location .\UI_codex
$previewBasePython = Join-Path $env:LOCALAPPDATA 'Python\pythoncore-3.14-64\python.exe'
& $previewBasePython -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
```

別の場所に Python 3.14 をインストールした場合は、上の `$previewBasePython` にそのパスを指定してください。起動時に架空のデモデータを準備します。

```powershell
powershell -ExecutionPolicy Bypass -File .\preview.ps1
```

ブラウザで [生徒ログイン](http://127.0.0.1:8010/login) を開きます。生徒のデモIDは `demo_student`、パスワードは `TekneDemo2026` です。比較用に同じパスワードの `demo_teacher` と `demo_admin` も用意しています。管理者の入口は [管理者ログイン](http://127.0.0.1:8010/admin/login) です。

新規登録を試すときは `database/demo-school-id.txt` の学校IDを使います。デモには架空の提出物、通知、工作室・実験器具予約、メンター枠、チャットグループと成果レポートのテンプレートが入っています。

DB・添付・セッション鍵はこのフォルダ内に保存されます。起動スクリプトは環境変数のセッション鍵とGoogle Calendar設定を試用プロセスから除外します。

## 自動テスト

アプリを停止し、コードをコピーした検証用フォルダに空の `database` ディレクトリを作ってから実行します。テストにはアプリの読み込み時にDBを初期化するものがあります。

```powershell
python -B -m unittest discover -s tests -v
node tests/test_mypage_calendar.cjs
```
