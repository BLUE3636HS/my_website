# neon_version Neon Storage と Vercel 運用

## 原因と修正

neon_version のユーザーアップロードは、ローカル実行・Vercel実行のどちらも同じ Private Neon Storage バケットへ保存する。
アプリ領域や `/tmp` にはアップロードを永続保存しない。旧ローカル uploads ファイルは移行せず、旧DB値は新Storageの配信対象にしない。

## 調査結果

- neon_version のDB処理は pg_compat 経由の PostgreSQL。DATABASE_PATH の database.db は
  互換APIへの引数であり、SQLiteファイルを開かない。sqlite_master / PRAGMA 等はアダプターが変換する。
- main/ は別のSQLite版であり、起動時のスキーマ更新も含めVercel運用には未対応。今回の修正対象外。
- 生徒・メンター画像、研究PDF、テンプレート研究画像を `file_storage.py` 経由で S3互換Storageへ保存・取得・削除する。
- study_pdf.py は Storage画像を BytesIO で受け取る。フォントは assets/fonts から読み取り専用で使う。
- CSV、テンプレート、static は読み取り。import先に os.mkdir / os.makedirs はない。
- Neonスキーマ作成は init_db.py による明示実行。アプリ import ではスキーマを作成しない。

## 公開前に必要な対応

1. Vercel Root Directory を neon_version に設定し、main:app をエントリーポイントにする。
   DATABASE_URL を対象環境に設定する。Vercelのシステム環境変数 VERCEL=1 が利用できることを確認する。
2. 信頼できる環境から init_db.py、verify_db.py を実行し、Neonのスキーマを準備・検証する。
3. Vercelとローカルの両方に `AWS_ENDPOINT_URL_S3`、`AWS_ACCESS_KEY_ID`、
   `AWS_SECRET_ACCESS_KEY`、`AWS_REGION`、`STORAGE_BUCKET=uploads` を設定する。
   Credentialはサーバー環境変数に限り、`uploads` バケットを Private のまま保つ。
4. SessionMiddleware の固定 secret_key="TEKNE" を環境変数の十分に長い秘密値へ変更し、
   公開環境のCookie設定を確認する（今回の起動修正には含まない）。
5. static、templates、CSV、assets/fonts をデプロイに含める。
   Google Calendar設定と定期通知スクリプトの外部スケジュールも必要に応じて設定する。

Vercel の /tmp は一時領域であり、アップロードの永続保存先として使用できない。
参照: https://vercel.com/docs/functions/runtimes

## ユーザー確認項目

- 再デプロイ後にトップページとログイン画面が表示できる。
- Neonを使ったログイン・予約・文字のみの研究投稿が正常に動作する。
- 生徒画像、メンター画像、研究PDF、画像付きテンプレートの登録と表示/生成/削除を確認する。
- Storage環境変数不足時は保存操作が失敗し、成功表示やDB上の新規ファイル参照が残らないことを確認する。
