# my_website 作業引き継ぎ

2026-11-09 時点。リポジトリ直下の [AGENTS.md](AGENTS.md) も参照すること。

Neon 版（旧 `neon_version/`）は使用しないことになり、2026-11-09 にリポジトリから削除した。必要になった場合は Git 履歴（`982c5d4` 以前）から参照できる。

## プロジェクトと構成

学校向けの FastAPI アプリ。生徒・教員・管理者のログイン、研究提出、設備・工房・メンターの予約、通知、コミュニティなどを提供する。画面は Jinja2 と CSS・JavaScript で構成する。アプリ本体は `main/` ディレクトリ（SQLite 版）にあり、Git の `main` ブランチとは別物。

| パス | 役割 |
| --- | --- |
| `main/main.py` | ASGI エントリーポイント（`main:app`）。認証、ルート登録、起動時の SQLite スキーマ更新を担当。 |
| `main/database/database.db`、`main/uploads/` | 既存データとアップロード。ローカル起動はこのデータを使う。 |
| `main/admin_management.py`、`school_registration.py`、`school_features.py`、`student_custom_fields.py`（いずれも `main/` 配下） | 管理者権限、学校登録、学校別の機能設定、生徒の追加項目。 |
| `main/tekne_chat.py`、`main/static/tekne_chat/chat.js` | TEKNE チャットの HTTP API、WebSocket、画面側の同期。 |
| `main/group_reports.py`、`main/static/group_report.js` | グループレポートの共同編集、編集ロック、版保存、PDF。 |
| `main/studies.py`、`study_pdf.py`、`study_images.py`、`template_management.py` | 研究提出のデータ、PDF 生成、画像処理、研究テンプレート管理。 |
| `main/mentor_reservations.py`、`notifications.py`、`mypage_calendar.py`、`google_calendar.py` | メンター予約、通知、マイページの予定、Google Calendar 連携。 |
| `main/templates/`、`static/`、`csv/`、`assets/fonts/` | HTML テンプレート、CSS・JS、設備等の定義、PDF 生成用フォント。 |
| `main/tests/`、`main/scripts/create_reservation_reminders.py` | unittest と、外部から定期実行する前日予約通知。 |

## 起動とテスト（Windows PowerShell）

各コードブロックはリポジトリ直下から実行する。Python の基準は `main/runtime.txt`（3.12.10）。`py -3.12` で選べない場合は該当バージョンの `python.exe` のフルパスに置き換える。既存の venv を使う場合は先に `--version` と依存関係（`pypdf` など）を確認する。

```powershell
py -3.12 -m venv .\main\.venv
.\main\.venv\Scripts\python.exe -m pip install -r .\main\requirements-dev.txt
Set-Location .\main
.\.venv\Scripts\python.exe -m uvicorn main:app --reload --port 8000
```

### 自動テスト

テストには `main.py` を import するものがあり、その時点で DB 初期化が走る。既存の DB・アップロードを含まない作業コピーを用意し、そのコピーの `main/database/` に空のテスト DB を作らせる。下記コマンドは作業コピーから実行する。

```powershell
Push-Location .\main
New-Item -ItemType Directory -Force .\database | Out-Null
.\.venv\Scripts\python.exe -m unittest discover -s tests -q
# TEKNE チャットとグループレポートの確認
.\.venv\Scripts\python.exe -m unittest discover -s tests -p 'test_tekne_chat.py' -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -p 'test_group_reports.py' -v
Pop-Location
```

## 今後の検討事項

Neon 版の削除に伴い、以下の方針は未定。

| 項目 | 現状 |
| --- | --- |
| 公開先（Vercel など） | SQLite ファイルとローカルの `uploads/` に永続化しているため、サーバーレス環境ではそのまま運用できない。DB と保存先を決める必要がある。 |
| ファイル保存先（Vercel Blob など） | 未実装。現行はローカルの `main/uploads/`。 |
| WebSocket → HTTP ポーリング | 未実装。チャットは `/ws/tekne-chat/{group_id}`、グループレポートは `/ws/group-reports/{group_id}` を使う。チャットの `GET /tekne-chat/api/groups/{group_id}/messages` は `before_id` / `after_id` に対応済み。新着 ID だけでは編集・削除を拾えない。グループレポートの編集ロックは DB 管理だが、解除は WebSocket 切断時に行うため、ポーリング化するなら期限切れによる解除が必要。 |

## 既知の問題

- `main/.test-tmp/` と `main/tmp/` にテストの残骸（DB、`.session_secret` を含む）がコミットされている。
- 2026-11-09 時点で一部の unittest が失敗する（後発機能のテーブル・テンプレート変数にテストが未対応、Windows で一時 DB を削除できない、など）。JS テストの一部は DOM スタブが古く、`test_template_builder.cjs` は `playwright` が必要。

## 作業上の制約

- 既存 SQLite の DB・ファイルを上書き、削除しない。
- 既存の機能とデータを保護する。既存 API の URL・メソッド・入力・応答、認証・認可、セッション、CSRF、予約の在庫と重複防止を維持する。既存 WebSocket 利用者への影響も確認する。
- 添付は非公開のまま扱い、認可済みの配信経路を維持する。保存失敗時に成功表示や参照切れを残さない。
- 本番 DB・本番 Storage を開発用テストに使わない。環境変数の値、資格情報、接続文字列をリポジトリやログに書かない。
- DB・API・競合処理は自動テストで確認する。ブラウザでの表示や共同編集の操作確認は、実装後に短い「ユーザー確認項目」にまとめる。

## 環境変数名

| 用途 | 名前 |
| --- | --- |
| Google Calendar 連携を有効にする場合 | `GOOGLE_CALENDAR_CLIENT_ID`、`GOOGLE_CALENDAR_CLIENT_SECRET`、`GOOGLE_CALENDAR_REFRESH_TOKEN`、`GOOGLE_CALENDAR_ID` |
| セッション鍵を明示する場合 | `SESSION_SECRET` |
