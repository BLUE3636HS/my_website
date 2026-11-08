# my_website 作業引き継ぎ

2026-11-09 時点。記載の基準はローカルの `main` ブランチ（`5dd5367`）。リポジトリ直下の [AGENTS.md](AGENTS.md) も参照すること。

## プロジェクトと構成

学校向けの FastAPI アプリ。生徒・教員・管理者のログイン、研究提出、設備・工房・メンターの予約、通知、コミュニティなどを提供する。画面は Jinja2 と CSS・JavaScript で構成する。1つの Git リポジトリに独立して起動する2版があり、Git の `main` ブランチと `main/` ディレクトリは別物。

| パス | 役割 |
| --- | --- |
| `main/main.py` | SQLite 版の ASGI エントリーポイント（`main:app`）。認証、ルート登録、起動時の SQLite スキーマ更新を担当。 |
| `main/database/database.db`、`main/uploads/` | SQLite 版の既存データとアップロード。ローカル起動はこのデータを使う。 |
| `main/admin_management.py`、`school_registration.py`、`school_features.py`、`student_custom_fields.py`（いずれも `main/` 配下） | 管理者権限、学校登録、学校別の機能設定、生徒の追加項目。 |
| `main/tekne_chat.py`、`main/static/tekne_chat/chat.js` | TEKNE チャットの HTTP API、WebSocket、画面側の同期。 |
| `main/group_reports.py`、`main/static/group_report.js` | グループレポートの共同編集、編集ロック、版保存、PDF。 |
| `neon_version/main.py`、`neon_version/pg_compat.py` | Neon 版の ASGI エントリーポイント（こちらも `main:app`）と PostgreSQL 接続。互換層が SQLite 風のプレースホルダー、スキーマ照会、`lastrowid` などを扱う。 |
| `neon_version/init_db.py`、`verify_db.py`、`create_admin.py`（いずれも `neon_version/` 配下） | 専用 DB のスキーマ初期化、実 DB 検証、初期管理者作成。 |
| `neon_version/file_storage.py` | Private Neon Storage のオブジェクトキー生成と検証、boto3 による保存・取得・削除。 |
| 両版の `studies.py`、`study_pdf.py`、`study_images.py`、`template_management.py` | 研究提出のデータ、PDF 生成、画像処理、研究テンプレート管理。 |
| 両版の `mentor_reservations.py`、`notifications.py`、`mypage_calendar.py`、`google_calendar.py` | メンター予約、通知、マイページの予定、Google Calendar 連携。 |
| 両版の `templates/`、`static/`、`csv/`、`assets/fonts/` | HTML テンプレート、CSS・JS、設備等の定義、PDF 生成用フォント。 |
| 両版の `tests/`、`scripts/create_reservation_reminders.py` | unittest と、外部から定期実行する前日予約通知。 |

## 起動とテスト（Windows PowerShell）

各コードブロックはリポジトリ直下から実行する。両版に同名の Python モジュールがあるため、起動・テスト時は対象版のディレクトリへ移動する。Python の基準は各版の `runtime.txt`（SQLite 版は 3.12.10、Neon 版は 3.14.5）。ルートの README には共有 venv の手順があるが、ここではランタイムと依存関係を分ける。以下の `py -3.12` / `py -3.14` で対象 Python を選べない場合は、該当バージョンの `python.exe` のフルパスに置き換える。既存の venv を使う場合は先に `--version` で確認する。

### SQLite 版

```powershell
py -3.12 -m venv .\main\.venv
.\main\.venv\Scripts\python.exe -m pip install -r .\main\requirements-dev.txt
Set-Location .\main
.\.venv\Scripts\python.exe -m uvicorn main:app --reload --port 8000
```

### Neon 版

`DATABASE_URL` は専用の開発 DB を指定する。アプリはプロセスの環境変数を参照する。Storage を使う場合は後述の Storage 用環境変数も同じシェルに設定する。初回のみ空の開発 DB で下記の初期化と管理者作成を行う。

```powershell
py -3.14 -m venv .\neon_version\.venv
.\neon_version\.venv\Scripts\python.exe -m pip install -r .\neon_version\requirements-dev.txt
Set-Location .\neon_version
$neonUrl = Read-Host 'DATABASE_URL' -AsSecureString
$env:DATABASE_URL = [System.Net.NetworkCredential]::new('', $neonUrl).Password
.\.venv\Scripts\python.exe .\init_db.py
.\.venv\Scripts\python.exe .\verify_db.py
.\.venv\Scripts\python.exe .\create_admin.py
.\.venv\Scripts\python.exe -m uvicorn main:app --reload --port 8001
```

`init_db.py` はスキーマを書き込み、`create_admin.py` は管理者を登録する。`verify_db.py` は実 DB のテーブル、CRUD と予約の排他ロックを検査し、テストレコードのトランザクションをロールバックする。

### 自動テスト

SQLite 版のテストには `main.py` を import するものがあり、その時点で DB 初期化が走る。SQLite 版は既存の DB・アップロードを含まない作業コピーを用意し、そのコピーの `main/database/` に空のテスト DB を作らせる。下記の SQLite コマンドは作業コピーから実行する。Neon 版の unittest は DB・Storage をモックし、実環境の統合確認には別途、専用の開発環境を使う。

```powershell
Push-Location .\main
New-Item -ItemType Directory -Force .\database | Out-Null
.\.venv\Scripts\python.exe -m unittest discover -s tests -q
# TEKNE チャットとグループレポートの確認
.\.venv\Scripts\python.exe -m unittest discover -s tests -p 'test_tekne_chat.py' -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -p 'test_group_reports.py' -v
Pop-Location

Push-Location .\neon_version
.\.venv\Scripts\python.exe -m unittest discover -s tests -q
Pop-Location
```

## 移行の進捗

| 作業 | 完了済み（コード上） | 未完了・未確認 |
| --- | --- | --- |
| SQLite → Neon / Vercel | `neon_version/` を分離し、`pg_compat.py` 経由で PostgreSQL に接続。明示的なスキーマ初期化と DB 検証を用意。Vercel の Root Directory を `neon_version` とする手順を文書化。 | Vercel 側の設定・公開状態と実サービスでの動作は未確認。SQLite 版の後続機能（TEKNE チャット、グループレポート、学校別機能設定など）の同等機能が Neon 版に揃っていない。共通ルートの認可・応答と予約の競合を実環境で確認する。 |
| Vercel Blob | 現行の永続化先は S3 互換の Private Neon Storage。生徒・メンター画像、研究 PDF、テンプレート研究画像を Storage 経由で扱う。 | Vercel Blob への接続・切り替えは未実装。読み書き・削除とアクセス制御を実装する前に、既存の Neon Storage オブジェクトの扱いを決める。 |
| WebSocket → HTTP ポーリング | SQLite 版のチャットには HTTP の送信と履歴 API があり、`GET /tekne-chat/api/groups/{group_id}/messages` は `before_id` / `after_id` に対応する。グループレポートには HTTP の取得・保存 API がある。 | 定期ポーリングは未実装。チャットは `/ws/tekne-chat/{group_id}` を使い、切断時の再接続や送信の HTTP フォールバックがある。グループレポートは `/ws/group-reports/{group_id}` で在席、編集ロック、更新を同期する。両機能とも Neon 版にはルーターがない。チャットの新着 ID だけでは編集・削除を拾えないため、これらの同期方式も決める。 |

## 既知の問題と確認事項

- Neon 版の `SessionMiddleware` は固定の署名鍵を使う。公開前に秘密の環境変数を参照するようにし、Cookie 設定とログイン・ログアウトを確認する。現状 `SESSION_SECRET` を読むのは SQLite 版。
- ルートの README には Neon 版のアップロード先を `neon_version/uploads/` とする古い記述がある。現行の実装は `neon_version/file_storage.py`、運用手順は [neon_version/VERCEL_DEPLOYMENT.md](neon_version/VERCEL_DEPLOYMENT.md) を参照。旧ローカルアップロードを指す DB 値は現行 Storage の配信対象外。
- 研究提出の添付仕様に版差がある。SQLite 版は PDF 以外のファイルも受け付けるが、Neon 版の通常のファイル提出は PDF を検証して保存する。互換性の差分として扱う。
- この引き継ぎではコードと手順を照合した。テスト実行結果、Vercel の公開状態、実 Neon DB・Storage の疎通は未確認。

## 作業上の制約

- SQLite の既存レコードとアップロードを Neon、Vercel Blob などへ移行しない。Neon の新規スキーマ作成と SQLite のデータ移行は別作業。既存 SQLite の DB・ファイルを上書き、削除しない。
- 既存の機能とデータを保護する。既存 API の URL・メソッド・入力・応答、認証・認可、セッション、CSRF、予約の在庫と重複防止を維持する。SQLite 版の既存 WebSocket 利用者への影響も確認する。
- 添付は非公開のまま扱い、認可済みの配信経路を維持する。保存失敗時に成功表示や参照切れを残さない。Vercel の一時ファイル領域を永続化先にしない。
- 本番 DB・本番 Storage を開発用テストに使わない。環境変数の値、資格情報、接続文字列をリポジトリやログに書かない。
- DB・API・競合処理は自動テストで確認する。ブラウザでの表示や共同編集の操作確認は、実装後に短い「ユーザー確認項目」にまとめる。

## 環境変数名

| 用途 | 名前 |
| --- | --- |
| Neon 版の DB 接続 | `DATABASE_URL` |
| Neon 版の現行 Private Neon Storage | `AWS_ENDPOINT_URL_S3`、`AWS_ACCESS_KEY_ID`、`AWS_SECRET_ACCESS_KEY`、`AWS_REGION`、`STORAGE_BUCKET` |
| 両版の Google Calendar 連携を有効にする場合 | `GOOGLE_CALENDAR_CLIENT_ID`、`GOOGLE_CALENDAR_CLIENT_SECRET`、`GOOGLE_CALENDAR_REFRESH_TOKEN`、`GOOGLE_CALENDAR_ID` |
| SQLite 版のセッション鍵を明示する場合 | `SESSION_SECRET` |
| Vercel のシステム環境変数（デプロイ手順で言及。現行アプリからの参照なし） | `VERCEL` |

Vercel Blob 用の環境変数は現行コードから参照されていない。
