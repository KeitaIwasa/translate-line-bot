英語でthinkして、日本語でoutputして
`docs/Plans.md` にToDoリストをまとめています。適宜更新してください。
後任開発者などに引き継ぐことがあれば、この`AGENTS.md`にシンプルに記載してください。

- AWS CLI プロファイル `line-translate-bot` を `aws configure` で登録済み（リージョン ap-northeast-1）。`sam deploy --profile line-translate-bot` で利用可能。
- 2025-11-25: CloudWatch で `No module named 'translator'` を検知。原因は `src/reply_formatter.py` の import パス誤り。相対 import に修正し `sam build && sam deploy --profile line-translate-bot --stack-name translate-line-bot-stg` を実施、復旧済み。
- 2025-11-25: 翻訳結果に原文が残るケースがあり、Gemini プロンプトで原文エコー禁止を明示し、Lambda 側で原文エコーを除去するサニタイズ処理を追加。
- デプロイには`scripts/deploy.sh`を使用
- Neon 接続メモ：`.env` の `NEON_DATABASE_URL` を使用。ローカルで SQL 適用する場合は `python3 -m venv .venv && . .venv/bin/activate && pip install psycopg[binary]` で環境を作り、`python - <<'PY' ... psycopg.connect(NEON_DATABASE_URL) ...` のように実行する。
- Codex/CLI で `timeout` を短く設定すると SAM deploy 前にローカルで 124 終了する（AWS 側失敗ではない）。`./scripts/deploy.sh` を実行する際は実行環境のタイムアウトを 300–600 秒に設定しておくこと。
