# Google Calendar / Google Meet 連携設定手順

この手順では、大学生メンターの**オンライン予約**時に、TEKNE共通Googleアカウントのカレンダーへ予定を作成し、専用のGoogle Meet URLを発行できるようにします。オフライン予約ではGoogle CalendarやMeetを使いません。

設定は次の順で進めます。

1. Google CloudプロジェクトとCalendar APIを用意する
2. OAuth同意画面とOAuthクライアントを設定する
3. 共通アカウントを認可し、refresh tokenを取得する
4. 4つの値を本番環境のSecret管理へ登録する
5. オンライン予約とキャンセルをテストする

> **重要:** OAuthクライアントのJSONファイル、client secret、refresh tokenは認証情報です。Git、ソースコード、DB、チャット、メール、チケット、ログへ貼り付けないでください。認可操作はTEKNE共通Googleアカウントを管理できる担当者が行ってください。

## 1. Google CloudプロジェクトとCalendar API

1. [Google Cloud Console](https://console.cloud.google.com/)を開き、設定に使うGoogleアカウントでログインします。
2. 画面上部のプロジェクト選択メニューから「新しいプロジェクト」を選びます。
3. 例としてプロジェクト名を `TEKNE Mentor Calendar` とし、組織を選択できる場合はTEKNEの管理対象を選び、「作成」を押します。作成後、そのプロジェクトが選択されていることを確認します。
4. 「APIとサービス」→「ライブラリ」を開き、`Google Calendar API` を検索して選択し、「有効にする」を押します。
5. 「APIとサービス」→「有効なAPIとサービス」に Google Calendar API が表示されることを確認します。

## 2. OAuth同意画面とクライアント

Google Cloud Consoleの「Google Auth Platform」を開きます。コンソール表示によっては「APIとサービス」→「OAuth同意画面」にあります。

### 2.1 アプリ情報と対象ユーザー

1. 「ブランディング」または初期設定画面で、アプリ名（例: `TEKNE Mentor Calendar`）、ユーザーサポートメール、開発者連絡先メールを入力して保存します。
2. 「対象」または「Audience」で利用対象を選びます。
   - TEKNE共通アカウントが、このGoogle Cloudプロジェクトを所有するGoogle Workspace組織に属する場合は、組織ポリシーを確認したうえで `Internal` を選びます。組織外のアカウントは認可できません。
   - 組織内に限定できないGoogleアカウントを使う場合は `External` を選びます。まず `Testing` で設定を確認し、「テストユーザー」にTEKNE共通アカウントを追加します。
3. `External` の `Testing` では、登録したテストユーザーだけが認可できます。Calendar scopeを使う認可は通常7日で期限切れになり、refresh tokenも使えなくなるため、継続運用には適しません。本番運用では `In production` への公開が必要です。`calendar.events` はCalendarイベントの参照・編集を許可するscopeです。Externalアプリでこのscopeを使う場合、GoogleのOAuth確認が必要になることがあります。画面に表示される提出・確認要件に従ってください。InternalでもWorkspace管理者のAPI制限を確認してください。

### 2.2 Calendar scope

1. 「データアクセス」または「スコープを追加または削除」を開きます。
2. 次のscope **だけ**を追加し、保存します。

   ```text
   https://www.googleapis.com/auth/calendar.events
   ```

アプリはカレンダーイベントを作成・削除します。別のscopeを追加しないでください。

### 2.3 Desktop app OAuthクライアント

1. 「クライアント」→「クライアントを作成」を選びます。
2. アプリケーションの種類は `Desktop app` を選び、名前を `TEKNE Calendar Token Setup` などとします。
3. 作成後、クライアントのJSONファイルをダウンロードします。ローカルPCの一時作業用フォルダーだけに置いてください。
4. このDesktop app方式では、通常、Cloud ConsoleにコールバックURLを手入力しません。後述のライブラリがPC内の一時的なlocalhost受け口を使います。Web application型クライアントを作った場合は、ダウンロードしたJSONをこの手順のスクリプトにそのまま使えないため、Desktop app型を作り直してください。

## 3. TEKNE共通アカウントの認可とrefresh token取得

この作業は本番サーバーではなく、ブラウザを使える管理者PCで一度だけ行います。スクリプトとJSONは一時フォルダーに置き、作業完了後に削除します。

### 3.1 一時作業フォルダーとライブラリ

Windows PowerShellの例です。`$env:TEMP` 配下に専用フォルダーを作り、隔離したPython環境を用意します。

```powershell
$work = Join-Path $env:TEMP "tekne-google-calendar-oauth"
New-Item -ItemType Directory -Force -Path $work | Out-Null
python -m venv (Join-Path $work ".venv")
& (Join-Path $work ".venv\Scripts\python.exe") -m pip install --upgrade google-auth-oauthlib
```

ダウンロードしたOAuthクライアントJSONを、このフォルダーへ `client_secret.json` という名前で保存します。JSONをGit管理下のプロジェクトフォルダーへ置かないでください。

### 3.2 一時スクリプトを実行

次の内容をメモ帳などで一時ファイル `$work\get_refresh_token.py` に保存します。ファイルにclient secretやrefresh tokenを直接書き込む必要はありません。

```python
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/calendar.events"]

flow = InstalledAppFlow.from_client_secrets_file(
    "client_secret.json",
    scopes=SCOPES,
)
credentials = flow.run_local_server(
    host="127.0.0.1",
    port=0,
    access_type="offline",
    prompt="consent",
)

if not credentials.refresh_token:
    raise RuntimeError(
        "refresh tokenが返りませんでした。Testing状態、対象ユーザー、scopeを確認して再認可してください。"
    )

print("GOOGLE_CALENDAR_CLIENT_ID=" + credentials.client_id)
print("GOOGLE_CALENDAR_CLIENT_SECRET=" + credentials.client_secret)
print("GOOGLE_CALENDAR_REFRESH_TOKEN=" + credentials.refresh_token)
```

PowerShellでスクリプトを実行します。

```powershell
Push-Location $work
try {
    & (Join-Path $work ".venv\Scripts\python.exe") .\get_refresh_token.py
}
finally {
    Pop-Location
}
```

1. ブラウザが開いたら、必ずTEKNE共通Googleアカウントを選択します。個人アカウントで認可しないでください。
2. アプリ名・scopeを確認し、Calendarイベントへのアクセスを許可します。未確認アプリの警告が出る場合、認可前にプロジェクト名・対象ユーザー・scopeを担当者間で確認します。
3. ブラウザに認可完了のメッセージが表示されたらPowerShellへ戻ります。
4. `GOOGLE_CALENDAR_CLIENT_ID`、`GOOGLE_CALENDAR_CLIENT_SECRET`、`GOOGLE_CALENDAR_REFRESH_TOKEN` の3行が表示されることを確認します。値を共有文書やシェル履歴へ貼り付けず、本番Secret管理へ安全に登録します。

`redirect_uri_mismatch` が表示される場合は、OAuthクライアントが `Desktop app` 型か、スクリプトがそのクライアントJSONを読んでいるかを確認します。この方式では固定URLを登録しません。Web application型クライアントや別クライアントのJSONを混在させないでください。

### 3.3 一時ファイルを削除

Secret管理へ値を登録し、値が欠けていないことを確認した後、PowerShellで一時フォルダーを削除します。

```powershell
Remove-Item -LiteralPath $work -Recurse -Force
```

refresh tokenが取得できなかった場合、トークン行を手作業で作らないでください。TEKNE共通アカウントがテストユーザーに登録されているか、正しいアカウントで認可したかを確認し、必要ならGoogleアカウントの「サードパーティ製アプリとサービス」からこのアプリのアクセスを取り消して、`prompt="consent"` のまま再認可します。

## 4. 本番サーバーのSecret設定

アプリは `.env` ファイルを自動読込しません。ホスティングサービス、サービスマネージャー、またはコンテナ基盤のSecret／環境変数設定画面に、次の4つを登録します。

| 環境変数 | 設定する値 |
| --- | --- |
| `GOOGLE_CALENDAR_CLIENT_ID` | 一時スクリプト出力のclient ID |
| `GOOGLE_CALENDAR_CLIENT_SECRET` | 一時スクリプト出力のclient secret |
| `GOOGLE_CALENDAR_REFRESH_TOKEN` | 一時スクリプト出力のrefresh token |
| `GOOGLE_CALENDAR_ID` | 使用するカレンダーID。共通アカウントのメインカレンダーなら `primary` |

別の共有カレンダーを使う場合は、Google Calendarの設定で「カレンダーの統合」→「カレンダーID」を確認して設定します。認可したTEKNE共通アカウントに、そのカレンダーで予定を変更できる権限が必要です。値の前後に引用符や余分な空白を付けないでください。

4つの値を設定したらサービスを再起動し、実行環境に環境変数が渡されていることをSecret値を表示せずに確認します。値を確認する目的でログへ出力しないでください。

## 5. 動作確認

本番利用前に、運用担当者が確認できるテスト日時で一度ずつ予約を作成します。

### オンライン予約

1. 生徒アカウントから大学生メンターの**オンライン**予約を作成します。
2. Google Calendarに予定が作成され、日時が日本時間で表示されることを確認します。
3. 予定にGoogle Meetがあり、Meet URLを開けることを確認します。
4. 生徒は通知詳細とマイページ予約詳細、担当メンターは通知または管理画面の予約一覧からMeetを開けることを確認します。
5. サイトから予約をキャンセルし、Google Calendarの予定が削除されることを確認します。

### オフライン予約

オフライン予約を作成し、Calendarイベント・Meetが作成されず、通知や予約詳細にMeetリンクが表示されないことを確認します。確認用予約は後でキャンセルしてください。

## 6. よくある問題

| 症状 | 確認・対処 |
| --- | --- |
| `redirect_uri_mismatch` | OAuthクライアントが `Desktop app` 型であること、対応するJSONをスクリプトが開いていることを確認します。Web application型を使っている場合はDesktop app型を作り直します。 |
| refresh tokenが空 | 正しい共通アカウントを選んだか、External/Testingの場合はテストユーザーに登録済みか、Calendar scopeを許可したかを確認します。アプリの許可を取り消した後、`prompt="consent"` で再認可します。 |
| 数日後に認証エラーになる | OAuth同意画面がExternal/Testingのままでは、Calendar scopeの認可とrefresh tokenは通常7日で期限切れになります。本番ステータスまたはWorkspace内のInternal設定を確認し、必要なGoogle確認・管理者承認を済ませてから再認可します。 |
| 予約時に「Google Meetの準備に必要な設定が完了していません」と表示 | サーバーSecretに4つの環境変数がすべて登録されているか、名前の大文字小文字や値の空白、サービス再起動を確認します。 |
| API無効・アクセス拒否エラー | 正しいCloudプロジェクトでGoogle Calendar APIが有効か、OAuth scopeが `calendar.events` か、Workspace管理者がアプリやAPIを制限していないかを確認します。 |
| カレンダーへの権限エラー | `GOOGLE_CALENDAR_ID` が正しいか、共通アカウントが対象カレンダーで予定を変更できる権限を持つかを確認します。メインカレンダーを使う場合は `primary` を指定します。 |
| refresh tokenが後日無効になった | 共通アカウントのアプリ許可が取り消されていないか、External/Testingのままになっていないかを確認します。再認可で新しいrefresh tokenを取得し、Secret管理の `GOOGLE_CALENDAR_REFRESH_TOKEN` を更新してサービスを再起動します。 |

## 公式資料

- [Google Calendar APIを有効にする](https://developers.google.com/workspace/guides/enable-apis)
- [Google OAuth 2.0 Desktopアプリ](https://developers.google.com/identity/protocols/oauth2/native-app)
- [Google OAuth Webサーバーアプリとオフラインアクセス](https://developers.google.com/identity/protocols/oauth2/web-server)
- [Google Calendar APIの認証scope](https://developers.google.com/workspace/calendar/api/auth)
- [Google Auth Platformの対象ユーザーとTesting状態](https://support.google.com/cloud/answer/15549945)
