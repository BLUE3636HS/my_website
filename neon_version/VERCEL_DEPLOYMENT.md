# Vercel 起動修正と公開前の対応

## 原因と修正

main.py の import 中に uploads/profile、uploads/study-images、uploads/mentor-profile
を mkdir し、mentor_reservations.build_router でも mkdir していた。
Vercel のアプリ配置領域は読み取り専用のため、この処理で起動が失敗する。
VERCEL=1 では mkdir を実行せず、存在しないアップロードディレクトリの配信は404にする。
ローカルでは従来通りディレクトリを作成する。例外を握りつぶす処理や /tmp への永続保存は行わない。

永続保存先を導入するまで、Vercel でのプロフィール画像変更、メンター画像変更、
研究PDF投稿、画像付きテンプレート投稿は503を返す。保存成功やDBへのファイル登録は行わない。
文字のみのテンプレート投稿、画像を変更しないメンター情報更新は維持する。

## 調査結果

- neon_version のDB処理は pg_compat 経由の PostgreSQL。DATABASE_PATH の database.db は
  互換APIへの引数であり、SQLiteファイルを開かない。sqlite_master / PRAGMA 等はアダプターが変換する。
- main/ は別のSQLite版であり、起動時のスキーマ更新も含めVercel運用には未対応。今回の修正対象外。
- 画像/PDF保存は main.py と mentor_reservations.py に存在する。
  一時ファイル作成後の replace、削除時の unlink、PDF配信の FileResponse、
  テンプレートPDF生成時の画像パス読み込みもローカルファイル前提。
- study_pdf.py のPDF生成は BytesIO 上で行う。フォントは assets/fonts から読み取り、書き込まない。
- CSV、テンプレート、static は読み取り。import先に os.mkdir / os.makedirs はない。
- Neonスキーマ作成は init_db.py による明示実行。アプリ import ではスキーマを作成しない。

## 公開前に必要な対応

1. Vercel Root Directory を neon_version に設定し、main:app をエントリーポイントにする。
   DATABASE_URL を対象環境に設定する。Vercelのシステム環境変数 VERCEL=1 が利用できることを確認する。
2. 信頼できる環境から init_db.py、verify_db.py を実行し、Neonのスキーマを準備・検証する。
3. 永続ファイル保存先を選ぶ。Blob/S3等に保存・取得・削除を移し、既存ファイルも移行する。
   研究PDFと画像は現在の認可を維持し、無制限な公開URLに置き換えない。
   PDF生成の画像読み込みもバイト列または一時キャッシュに対応させる。
   Neonにファイル名を保存するだけではファイル本体は永続化されない。
4. SessionMiddleware の固定 secret_key="TEKNE" を環境変数の十分に長い秘密値へ変更し、
   公開環境のCookie設定を確認する（今回の起動修正には含まない）。
5. static、templates、CSV、assets/fonts をデプロイに含める。
   Google Calendar設定と定期通知スクリプトの外部スケジュールも必要に応じて設定する。

Vercel の /tmp は一時領域であり、アップロードの永続保存先として使用できない。
参照: https://vercel.com/docs/functions/runtimes

## ユーザー確認項目

- 再デプロイ後にトップページとログイン画面が表示できる。
- Neonを使ったログイン・予約・文字のみの研究投稿が正常に動作する。
- 保存先導入前の画像/PDF保存操作では503の保存先未設定メッセージが返る。
