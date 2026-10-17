# ゲーム連携・募集機能

既存Botの `cogs/game_schedule.py` として追加した機能です。Python 3.12、discord.py 2.x、既存の `aiohttp` を使用し、`firestore_db.py` のFirestoreクライアントを共有して保存します。既存のBot起動処理がこのCogをロードします。

## 設定

`.env` にSteam Web APIキーを追加してください。現在の `.env` に `STEAM_API_KEY` がない場合は、以下を追記して実際のキーを設定しBotを再起動してください。既存のDiscord/Gemini設定はそのままです。

```env
STEAM_API_KEY=Steam_Web_API_Key
```

Steam APIキーはSteamのWeb API Keyページで発行します。Botにロールを作成・付与するには、Botロールに「ロールの管理」権限を与え、付与対象ロールより上に配置してください。Discord Developer PortalでServer Members Intentも有効にしてください。

Steam APIの`GetOwnedGames`は、ユーザーの所有ゲーム/ゲーム詳細がAPIから閲覧可能な場合にゲーム一覧を返します。非公開などで一覧が欠落・不完全な応答は同期失敗として扱い、既存のゲームキャッシュとタグ候補は削除しません。APIが返したゲームごとのSteamライブラリ「非表示」状態は公開APIに含まれないため、個別の非表示ゲームだけを判定して除外することはできません。Bot側で「非表示」にしたタグは通常の登録画面と募集画面から除外され、管理者画面には再表示できるよう残ります。

募集カードを投稿するチャンネルでは、Botに「チャンネルを見る」「メッセージを送信」「埋め込みリンク」が必要です。タグ選択時にロールメンバーを初期表示するため、Developer PortalのServer Members Intentとコード上のmembers intentも必要です。`/game-tag-add` と `/sync` には「ロールの管理」権限およびロール階層の設定も必要です。

Firestoreの接続設定は既存Botと共通です。`GOOGLE_CLOUD_PROJECT_ID` と `GOOGLE_APPLICATION_CREDENTIALS` または `GOOGLE_APPLICATION_CREDENTIALS_JSON` を使用し、既存のサービスアカウント権限でデータを書き込みます。

## コマンド

- `/link profile_url:<SteamプロフィールURL>`: `/id/名前` または `/profiles/17桁ID` からSteamID64を取得してDiscordユーザーと連携し、初回の所持ゲーム取得と表示対象タグのロール同期を実行
- `/sync`: 連携済みアカウントの所持ゲームと表示対象タグのロールを後から再同期
- `/game-tag-add tag_name match_keyword custom`: 管理ロール権限でタグを登録。`custom=true` またはキーワード空欄なら募集専用タグ。Steam自動ロールを使う場合は、ゲーム名に含まれる文字列をキーワードに設定
- `/game-tag-list`: サーバーのタグ一覧
- `/game-tag-manage`: ロール管理権限を持つ管理者向けのSteamゲーム編集画面。表示状態はコンボボックスで切り替え、表示タグ名（Discordロール名）は別画面から編集
- Steamタグを表示にすると、対応するDiscordロールを作成/確認するだけで、ユーザーには付与しない。非表示にすると紐づくロールを削除する。タグ名を変更すると紐づくロール名も更新。ユーザーへの登録はタグパネルで本人が選択した場合、またはSteam連携/同期で表示対象ゲームの所持を確認した場合のみ行う
- `/game-tag-manage` の「Steamキャッシュからタグを追加」ボタンで、サーバーメンバーがSteam連携で取得したキャッシュからゲームタグ候補を追加。新規候補は非表示で登録され、管理者が表示に切り替えたゲームだけがSteam連携時のロール自動割り当て対象
- 非表示にしたタグは管理画面からタグ設定を削除可能。削除時に確認を行い、Firestore上のタグ設定のみ削除する（Discordロール自体は残す）
- `/game-tag-manage` はスラッシュコマンドと、チャット本文に同じ文字列を入力して送信する方法の両方に対応。Steamゲーム一覧はページ表示し、設定変更時にも管理権限を再確認
- `/game-tag-panel`: ロール管理権限を持つ管理者が、コマンドを実行したチャンネルへ常設パネルを設置。「タグ登録・更新」と「Steam連携」の2ボタンを表示
- `/game-tag-panel` はスラッシュコマンドから選択して実行する方法に加え、チャット本文に `/game-tag-panel` と入力して送信する方法にも対応
- パネルのタグ登録・更新は、タグをボタンで選択して最後に一括反映。1ページ20タグでページ切り替え可能。登録済みタグを解除対象、未登録タグを追加対象として選べる。各グループ内はタグ名のA-Z順
- パネルのSteam連携ボタンはURL入力フォームを開き、SteamプロフィールURLを貼り付けるだけで連携
- Steam連携フォームのタイトルにURL取得手順（Steamを起動し、画面上の名前をクリックして表示されたURLをコピー）を表示
- `/recruit`: まず月・日・時・分を選択し、次に募集タグと通知先（タグメンションまたは`@everyone`）を選択。対象ロールのメンバーを初期選択した参加者セレクターで、ロール外のメンバーも追加可能

Steam連携例: `/game-tag-add tag_name:Minecraft custom:true`

タグ登録パネルを `#game-tags` に設置するには、そのチャンネル内で `/game-tag-panel` を実行します。

Steam同期・ロール例: `/game-tag-add tag_name:Stardew Valley match_keyword:Stardew Valley`

募集作成時は、最初の画面に月（01〜12）、日（01〜31）、時（01〜24）、分（00/15/30/45）の4つのドロップダウンを同時に表示します。Discordの選択肢上限が25件のため、日付のみ01〜25日／26〜31日の補助ボタンで切り替えます。続けて募集タグ、通知先（タグロール／`@everyone`）、参加者を選びます。参加者セレクターは最大25人で、対象ゲームのロール参加者を初期選択し、ロール外のメンバーも検索・追加できます。カードには `MM/DD HH:MM` 形式で表示します。タグをメンションするには、対象ロールがメンション可能であるか、Botに「@everyone、@here、すべてのロールにメンション」の権限が必要です。募集の参加ボタンは永続Viewで、参加状況はFirestoreトランザクションで排他的に更新されます。Bot再起動後も既存募集のボタンを利用できます。

募集のFirestore書き込みは15秒のタイムアウトを設定しています。保存に失敗した場合は募集メッセージに失敗理由の案内を出し、Botログに例外を記録します。カード編集がHTTP 403で失敗した場合は、チャンネルの「埋め込みリンク」権限を確認してください。

カード編集のHTTP 400 / Discord code 50035は権限エラーではなく、DiscordがEmbedまたはコンポーネントのリクエスト形式を拒否した状態です。Botログの`Invalid Form Body`に拒否されたフィールドが出ます。

## Firestoreコレクション

- `game_schedule_users`: DiscordユーザーとSteamID64の連携
- `game_schedule_games`: SteamID64、App ID、ゲーム名の所持ゲームキャッシュ
- `game_schedule_tags`: サーバー単位の募集タグとSteam名照合・ロール設定。Steam候補は`app_id`, `steam_game_name`, `tag_name`, `role_id`, `is_visible`を保持
- `game_schedules`: 募集メッセージと参加・未定・不参加の各ユーザーID
