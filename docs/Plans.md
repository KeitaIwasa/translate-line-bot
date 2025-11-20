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
- [x] 設定管理（環境変数, Secrets 管理）とデプロイ用スクリプトを整備
- [x] template.yaml を用いた AWS リソース定義を作成（SAM テンプレ）

## 4. テスト
- [ ] 単体テスト：翻訳ロジック／DB リポジトリ／LINE API ラッパー（translator・Webhook までは実施済み）
- [ ] （2025-11-20 メモ）Secrets Manager 反映後の Lambda を `aws lambda invoke` で疎通確認済み（署名付き follow イベント → 200 OK）。今後は message イベントでの E2E テストを追加実施する。
- [ ] 結合テスト：Webhook 受信から返信までのエンドツーエンド動作
- [ ] 性能テスト：平均 200–350ms を満たすか検証し、ボトルネックを洗い出す
- [ ] エラーハンドリングテスト：Gemini エラー、Neon 接続失敗、壊れたレスポンスなどの再試行挙動

## 5. デプロイ準備
- [x] AWS 環境（IAM, Lambda, API Gateway, CloudWatch）を IaC（template.yaml）で構築し、ステージングにデプロイ（2025-11-20: Secrets Manager `line-translate-bot-secrets` を参照するよう SAM テンプレ更新→ `sam build`/`sam deploy --profile line-translate-bot` で `translate-line-bot-stg` スタックを ap-northeast-1 に作成。API エンドポイント：`https://cbvko1l0ml.execute-api.ap-northeast-1.amazonaws.com/stg`。Lambda ARN：`arn:aws:lambda:ap-northeast-1:215896857123:function:translate-line-bot-stg-LineWebhookFunction-a1Thoi5FRgnv`。）
- [x] Neon プロジェクトを本番用に作成し、接続情報を Lambda に設定
  - 2025-11-20: `sql/001_init_schema.sql` で `group_members` / `messages` / `idx_messages_group_ts` を作成済み（psycopg 経由で Neon に適用）。
- [x] Gemini API キーおよび LINE チャネル設定を本番用に切り替え、Webhook URL を登録
- [ ] デプロイ手順書とロールバック手順をまとめる

### メモ: ステージング AWS デプロイ手順
1. AWS CLI / SAM CLI / Python 3.12 をローカルに揃え、`aws configure --profile translate-line-bot-stg` でステージング用 IAM 認証情報を設定。
2. `template.yaml` のパラメータ（Line/Gemini/Neon などのシークレット）を `.env.stg` などで管理し、CLI にエクスポートできる状態にする。
3. `sam build` → `sam deploy --guided` で初回デプロイを実施し、`StageName=stg`、`--stack-name translate-line-bot-stg`、`--capabilities CAPABILITY_IAM` などを設定。以降は `sam deploy --config-env staging` で再利用。
4. デプロイ完了後に `sam describe stack` もしくは出力の `HttpApiEndpoint` を確認し、LINE Developers の Webhook URL をステージングエンドポイントに更新、テストイベントを送信して CloudWatch Logs を確認。
5. Lambda コードのみのホットデプロイは `scripts/deploy.sh`（`LAMBDA_FUNCTION_NAME`/`AWS_REGION` 必須）で差分反映し、構成変更は必ず SAM で実施する。

## 6. リリース / 運用
- [ ] ステージング環境で最終受け入れテストを完了し、Go/No-Go 判定を実行
- [ ] 本番反映後、初回数日の監視体制（当番表・連絡方法）を決める
- [ ] 運用ドキュメント（障害対応フロー、問い合わせ対応）を整備
- [ ] リリースノートとユーザー向けアナウンスを作成・配信
