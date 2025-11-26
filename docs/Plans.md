# リリースまでの ToDo リスト

## 1. 企画・要件整理
- [x] 要件定義書の最新版をレビューし、抜け漏れ（非機能要件・運用要件）を最終確認する
- [x] 利用想定グループとメッセージトラフィックの規模を想定し、SLA/SLO を文書化する
- [x] LINE Developers・Gemini・Neon の契約/利用規約チェックとコスト試算を完了する

## 2. 技術設計
- [x] Lambda / API Gateway / Neon / Gemini / LINE API 間のインターフェース仕様書を作成する
- [x] 翻訳プロンプト・Structured Output JSON Schema を確定し、サンプルを含むドキュメント化を行う
- [x] DB スキーマ詳細（インデックス、制約、マイグレーション計画）を決定する

## 3. 実装
- [x] Lambda ハンドラー／Webhook 受信処理（署名検証含む）を実装
- [x] Neon とのデータアクセス層（言語設定・メッセージ履歴）を実装
- [x] Gemini クライアントと翻訳ロジック（文脈投入・レスポンス検証・再試行）を実装
- [x] LINE Messaging API 返信ロジック（翻訳結果フォーマット、部分成功ハンドリング）を実装
  - 2025-11-21: 翻訳結果の返信フォーマットから原文と言語コードを除去し、各翻訳を改行区切りで表示するよう更新。
  - 2025-11-25: Gemini へのプロンプトに「原文をエコーしない」条件を追加し、返信整形時にも原文エコーを除去するサニタイズ処理を挿入。
  - 2025-11-26: Gemini 429 (rate limit) を受けた場合はリトライせず、1回だけ「You have reached the rate limit. Please try again later.」を返信。直前に同一メッセージを送っていれば送信しないよう抑止。
  - 2025-11-26: Gemini へ送信する payload を CloudWatch Logs で確認できるよう INFO ログ出力を追加。
  - 2025-11-26: 言語設定を「ユーザー毎」から「グループ単位（一度設定すれば全員に適用）」へ変更。DB は `group_languages` に集約し、確認ポストバックでグループ全体の言語リストを保存するよう更新。
  - 2025-11-26: 言語設定確認テンプレートの重複押下で完了メッセージが何度も送られる問題を修正（初回以降は無視）。
  - 2025-11-26: 言語設定確認テンプレートで「完了」後に「変更する」を押しても（または逆順でも）応答しないよう postback を相互排他に変更。
- [x] 言語設定確認テンプレートが未対応言語を含む文面を出すことがあるため、Gemini 生成文を使わず対応言語だけで確認文を組み立てるサニタイズを追加する（2025-11-26: 日本語/アラビア語/絵文字/サンスクリット語入力で確認文にサンスクリット語が残存）。
- [x] デプロイ用スクリプト `scripts/deploy.sh` を追加（環境変数で stack/profile/parameters を上書き可。S3 バケット未指定時は --resolve-s3 を使用）。
- [x] 設定管理（環境変数, Secrets 管理）とデプロイ用スクリプトを整備
- [x] template.yaml を用いた AWS リソース定義を作成（SAM テンプレ）
- [x] join/memberJoined/follow イベントに対応した言語設定フロー（Gemini で言語抽出→確認テンプレ生成→`group_languages` 登録）を実装（2025-11-20: `src/lambda_handler.py` + `src/language_preferences/` + `src/db/repositories.py` 更新。postback で完了/変更を処理し、Neon へ多言語設定を保存。2025-11-26 にグループ単位管理へ移行。）
  - 2025-11-21: 確認テンプレのメッセージを入力言語の1文に絞り、postback data から多言語テキストを除去して LINE 制限（300文字）を超えないよう修正。
  - 2025-11-25: memberJoined イベントの歓迎文を「再招待で言語設定変更」案内に差し替え。ボット参加から10分以内に追加されたメンバーには歓迎メッセージを送らないよう制御し、`group_members` に `__bot_join__` レコードで参加時刻を記録。
- [x] Lambda のタイムアウト値を Gemini リクエストに合わせて再設定（2025-11-20: SAM パラメータ `FunctionTimeout=15` で `sam deploy --profile line-translate-bot --stack-name translate-line-bot-stg` を実施し、`translate-line-bot-stg-LineWebhookFunction` のタイムアウトを 15 秒へ引き上げ済み。CloudWatch Alarm は別途整備予定。）

## 5. デプロイ準備
- [x] AWS 環境（IAM, Lambda, API Gateway, CloudWatch）を IaC（template.yaml）で構築し、ステージングにデプロイ（2025-11-20: Secrets Manager `line-translate-bot-secrets` を参照するよう SAM テンプレ更新→ `sam build`/`sam deploy --profile line-translate-bot` で `translate-line-bot-stg` スタックを ap-northeast-1 に作成。API エンドポイント：`https://cbvko1l0ml.execute-api.ap-northeast-1.amazonaws.com/stg`。Lambda ARN：`arn:aws:lambda:ap-northeast-1:215896857123:function:translate-line-bot-stg-LineWebhookFunction-a1Thoi5FRgnv`。）
- [x] Neon プロジェクトを本番用に作成し、接続情報を Lambda に設定
  - 2025-11-20: `sql/001_init_schema.sql`/`sql/002_group_user_languages.sql` を適用し、`group_members` メタデータ＋ `group_user_languages`（多言語設定・現在は非推奨）＋ `messages` を整備済み。
- [x] Gemini API キーおよび LINE チャネル設定を本番用に切り替え、Webhook URL を登録
- [ ] デプロイ手順書とロールバック手順をまとめる

### メモ: ステージング AWS デプロイ手順
1. AWS CLI / SAM CLI / Python 3.12 をローカルに揃え、`aws configure --profile translate-line-bot-stg` でステージング用 IAM 認証情報を設定。
2. `template.yaml` のパラメータ（Line/Gemini/Neon などのシークレット）を `.env.stg` などで管理し、CLI にエクスポートできる状態にする。
3. `sam build` → `sam deploy --guided` で初回デプロイを実施し、`StageName=stg`、`--stack-name translate-line-bot-stg`、`--capabilities CAPABILITY_IAM` などを設定。以降は `sam deploy --config-env staging` で再利用。
4. デプロイ完了後に `sam describe stack` もしくは出力の `HttpApiEndpoint` を確認し、LINE Developers の Webhook URL をステージングエンドポイントに更新、テストイベントを送信して CloudWatch Logs を確認。
5. Lambda コードのみのホットデプロイは `scripts/deploy.sh`（`LAMBDA_FUNCTION_NAME`/`AWS_REGION` 必須）で差分反映し、構成変更は必ず SAM で実施する。

## 6. リリース / 運用
- [x] 本番スタック `translate-line-bot-prod` にデプロイ（2025-11-26: `./scripts/deploy.sh` を `STACK_NAME=translate-line-bot-prod STAGE=prod PROFILE=line-translate-bot` で実行。HttpApiEndpoint=`https://h2xf6dwz5e.execute-api.ap-northeast-1.amazonaws.com/prod`, FunctionArn=`arn:aws:lambda:ap-northeast-1:215896857123:function:translate-line-bot-prod-LineWebhookFunction-moXA62iCKlH3`）
- [x] 障害対応（2025-11-25: Lambda が `translator` モジュールを読み込めず起動失敗 → `src/reply_formatter.py` の絶対 import を相対 import へ修正し、`sam build && sam deploy --stack-name translate-line-bot-stg --profile line-translate-bot` で再デプロイ。`No module named 'translator'` は解消済み）
- [ ] ステージング環境で最終受け入れテストを完了し、Go/No-Go 判定を実行
- [ ] 本番反映後、初回数日の監視体制（当番表・連絡方法）を決める
- [ ] 運用ドキュメント（障害対応フロー、問い合わせ対応）を整備
- [ ] リリースノートとユーザー向けアナウンスを作成・配信
- [x] Gemini 言語設定解析のタイムアウト時にフォールバック返信（リトライ案内＆既定言語候補提示）を行い、無返信を防ぐ
- [x] LINE Reply API 400 エラーのレスポンス本文と replyToken を CloudWatch に出力し、4xx/5xx を検知するメトリクス・アラームを追加（ログ出力まで対応、メトリクス/アラームは今後追加検討）
- [ ] `pytest` 全体実行時に `tests/test_reply_formatting.py` でパッケージ import エラー（`.translator` 相対 import）。パス設定またはモジュール構成を修正しテストが通る状態にする。
