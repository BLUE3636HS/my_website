# Google Calendar / Google Meet 初期設定

大学生メンターのオフライン予約は、TEKNE共通GoogleアカウントのOAuth 2.0 refresh tokenを使ってCalendarイベントと専用Meetを作成します。サービスアカウントやメンター個別連携は使用しません。

## Google Cloud側

1. Google Cloud Consoleで専用プロジェクトを作成します。
2. Google Calendar APIを有効化します。
3. OAuth同意画面を設定し、TEKNE共通Googleアカウントをテストユーザーに追加します。組織内運用なら組織ポリシーに合わせてInternalを選択します。
4. OAuth 2.0クライアントを作成します。初回認証をWebアプリで行う場合はWeb applicationを選び、実際に使用するHTTPSのコールバックURLを完全一致で登録します。
5. 次のscopeだけを要求し、`access_type=offline` と `prompt=consent` を指定してTEKNE共通Googleアカウントで一度認証します。

   `https://www.googleapis.com/auth/calendar.events`

6. 認可コードをサーバー側で交換して得たrefresh tokenを、ソースやDBではなく本番環境のsecret管理機能へ保存します。refresh tokenが返らない場合は、共通アカウントの当該アプリへの許可を取り消してから、`prompt=consent` 付きで再認証します。

公式資料:

- https://developers.google.com/identity/protocols/oauth2/web-server
- https://developers.google.com/workspace/calendar/api/auth
- https://developers.google.com/workspace/calendar/api/v3/reference/events/insert

## サーバー側

1. `requirements.txt` を本番用仮想環境へインストールします。
2. `.env.example`を参照し、次の値を実行環境のsecretとして設定します。アプリ自体は`.env`を自動読込しないため、サービスマネージャー等から環境変数として渡してください。

   - `GOOGLE_CALENDAR_CLIENT_ID`
   - `GOOGLE_CALENDAR_CLIENT_SECRET`
   - `GOOGLE_CALENDAR_REFRESH_TOKEN`
   - `GOOGLE_CALENDAR_ID`（共通アカウントの主Calendarなら`primary`）

3. サービスを再起動します。
4. テスト用のオフライン予約を1件作成し、Calendar上のJST日時、Meet URL、生徒通知、担当メンター通知を確認します。確認後はサイトからキャンセルし、Calendarイベントも削除されることを確認します。

client secret、refresh token、access tokenをGit、HTML、通知本文、ログへ記録しないでください。refresh tokenが無効化された場合は同じ手順で再発行し、secretだけを更新します。
