英語でthinkして、日本語でoutputして
`docs/Plans.md` にToDoリストをまとめています。適宜更新してください。
後任開発者などに引き継ぐことがあれば、この`AGENTS.md`にシンプルに記載してください。

- AWS CLI プロファイル `line-translate-bot` を `aws configure` で登録済み（リージョン ap-northeast-1）。`sam deploy --profile line-translate-bot` で利用可能。
- 2025-11-25: CloudWatch で `No module named 'translator'` を検知。原因は `src/reply_formatter.py` の import パス誤り。相対 import に修正し `sam build && sam deploy --profile line-translate-bot --stack-name translate-line-bot-stg` を実施、復旧済み。
- 2025-11-25: 翻訳結果に原文が残るケースがあり、Gemini プロンプトで原文エコー禁止を明示し、Lambda 側で原文エコーを除去するサニタイズ処理を追加。
- デプロイには`scripts/deploy.sh`を使用
- Neon 接続メモ：`.env` の `NEON_DATABASE_URL` を使用。ローカルで SQL 適用する場合は `python3 -m venv .venv && . .venv/bin/activate && pip install psycopg[binary]` で環境を作り、`python - <<'PY' ... psycopg.connect(NEON_DATABASE_URL) ...` のように実行する。
- Codex/CLI で `timeout` を短く設定すると SAM deploy 前にローカルで 124 終了する（AWS 側失敗ではない）。`./scripts/deploy.sh` を実行する際は実行環境のタイムアウトを 300–600 秒に設定しておくこと。
- 2025-11-26: 本番スタック `translate-line-bot-prod` を `STACK_NAME=translate-line-bot-prod STAGE=prod PROFILE=line-translate-bot ./scripts/deploy.sh` で更新。HttpApiEndpoint=`https://h2xf6dwz5e.execute-api.ap-northeast-1.amazonaws.com/prod`、FunctionArn=`arn:aws:lambda:ap-northeast-1:215896857123:function:translate-line-bot-prod-LineWebhookFunction-moXA62iCKlH3`。`sam deploy` は約 100 秒、ローカルのタイムアウトは 600 秒で実行。
- 2025-11-26: 新アーキテクチャ案を `src_new/` 配下に実装（Dispatcher/Handlers/Domain/Infra 分離）。切り替える場合は Handler 指定を `src_new.lambda_handler::lambda_handler` に変更し、CodeUri を合わせてデプロイする（現行 `src/` は無変更）。
- 2025-11-26: ステージング `translate-line-bot-stg` を `src_new.lambda_handler` ハンドラ構成でデプロイ済み（scripts/deploy.sh）。HttpApiEndpoint=`https://cbvko1l0ml.execute-api.ap-northeast-1.amazonaws.com/stg`。
- 2025-11-26: `scripts/deploy.sh` が STAGE に応じて Secrets Manager の名前を自動選択（prod→`prod/line-translate-bot-secrets`, その他→`stg/line-translate-bot-secrets`）。`RUNTIME_SECRET_ARN` を環境変数で渡せば上書き可能。
- 2025-11-30: メンション経由の機能コマンドを追加。環境変数 `BOT_MENTION_NAME` は Secrets Manager (`stg/prod line-translate-bot-secrets`) から取得する。Gemini で「言語設定変更/使い方説明/翻訳停止/翻訳再開/その他」を判定し、Lambda で実行。
  - 翻訳停止フラグを保持する `group_settings` テーブルを追加（`translation_enabled` boolean, PK: group_id）。未作成の場合はコード上で既定 true になるが、本番はテーブル追加が必要。
    - SQL: `sql/20251130_add_group_settings.sql`
  - 部分的な言語追加・削除に対応（Neon リポジトリに add/remove）。リセット時は既存フロー同様に再設定プロンプトを返す。
  - 使い方説明・未知指示は指示言語で返答。usage 文言は `USAGE_MESSAGE_JA` を設定済み。
