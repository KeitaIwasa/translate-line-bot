# README.md

# LINE Multilingual Translation Bot

https://kotori-ai.com/

**A fast, minimal, serverless multilingual translation bot for LINE groups.**
Built with AWS Lambda (Python), Gemini 2.5 Flash, and Neon (PostgreSQL).

本プロジェクトは、LINE グループ内のユーザーが書いたメッセージを、
**グループ内の他メンバーの言語へ自動翻訳する高速翻訳ボット**です。

Gemini 2.5 Flash の Structured Output を用いて、
**文脈（最大20件）＋話者情報込みの高度な自然翻訳**を実現します。

---

## Features

* 多言語ユーザーが混在する LINE グループ向け
* グループで一度だけ希望言語を登録（全メンバーに共有、最大5言語）
* メッセージの「原文の言語以外」に翻訳を自動生成
* 個人チャットの質問に、ガードレール付きサポート応答を返却（履歴を文脈に利用）
* グループメンション操作を OpenAI Agent SDK + tools で柔軟に判定
* Gemini 2.5 Flash Structured Output による自然＋安定した JSON 生成
* AWS Lambda による完全サーバレス構成
* Neon (PostgreSQL) で言語設定とメッセージ履歴を管理
* 高速動作（平均 200〜350ms 程度）
* グループの翻訳対象言語は最大5件（環境変数 `MAX_GROUP_LANGUAGES` で変更可）。6件以上指定された場合は登録せず、5件以内で再指定を促す。

---

## Architecture (現在のモジュラー構成)

```
LINE → API Gateway → Lambda (Python)
                     ├ presentation : Webhookパース・署名検証・返信DTO生成
                     ├ app         : DI組み立て、Dispatcher、Handler薄層
                     ├ domain      : モデル/ポート + サービス
                     │               - TranslationFlowService: クオータ判定→翻訳実行→返信生成
                     │               - LanguageSettingsService: 言語設定プロンプト生成・確認/キャンセル
                     │               - QuotaService: 無料/有料の上限判定・通知要否
                     │               - SubscriptionService/Coordinator: Stripe連携とメニュー生成
                     │               - RetryPolicy: 翻訳系リトライを共通化
                     └ infra       : LINE API, Gemini 翻訳, Command Router, Neon Repository など
```

### レイヤー責務まとめ
- presentation: LINE Webhookをドメインイベントに変換し、返信メッセージのDTO (`ReplyBundle`) を扱う。
- app: 依存解決とハンドラ登録のみ。`MessageHandler`/`PostbackHandler` はサービス呼び出しと送信に専念。
- domain: ユースケースの本体。翻訳フロー/言語設定/クオータ判定/購読連携をここに集約し、ポート経由でinfraに依存。
- infra: 外部サービス（LINE, Gemini, Neon, Stripe）との通信をポート実装として提供。

### 主要コンポーネント
- **TranslationFlowService**: QuotaServiceで判定→Gemini翻訳→返信文生成を一括で実行。
- **LanguageSettingsService**: 入力解析→確認テンプレート生成→Postback確認/キャンセル→言語保存を一元管理。
- **QuotaService**: Free/Standard/Proごとの上限管理と通知要否を決定。
- **RetryPolicy**: 翻訳系のリトライを共通化（指数バックオフ簡易版）。
- **ReplyBuilder**: LINE送信用メッセージ辞書を組み立てるユーティリティ。

---

## Tech Stack

| Component       | Choice                               |
| --------------- | ------------------------------------ |
| Runtime         | AWS Lambda (Python 3.12)             |
| LLM             | Google Gemini 2.5 Flash              |
| Response Format | Structured Output (JSON Schema)      |
| Database        | Neon (PostgreSQL)                    |
| API Gateway     | HTTP API                             |
| LINE API        | Messaging API v2                     |
| Deploy          | 任意（AWS Console / Terraform / CDK など） |

---

## Current Production Topology (2026-03-25)

- 公開サイト: `https://kotori-ai.com`（CloudFront + S3、GitHub Pagesは無効化済み）
- 公開API: `https://kotori-ai.com/api/*`（CloudFront から API Gateway `/prod` へ転送）
- API Gateway（prod）: `https://h2xf6dwz5e.execute-api.ap-northeast-1.amazonaws.com/prod`
- API Gateway（stg）: `https://cbvko1l0ml.execute-api.ap-northeast-1.amazonaws.com/stg`
- `api_base` / `apiBase` クエリは廃止。フロントは相対パス `/api/...` 固定。

---

## Repository Structure
```
src/
├─ lambda_handler.py      # エントリポイント（署名検証→Dispatcher）
├─ app/
│   ├─ bootstrap.py       # 依存組み立て
│   ├─ dispatcher.py      # event.type ルーティング
│   └─ handlers/          # message / postback / join / memberJoined / follow
├─ domain/
│   ├─ models.py          # イベント/翻訳/言語設定/返信DTO
│   ├─ ports.py           # 抽象ポート（Line/Gemini/Neon/Subscription 等）
│   └─ services/          # TranslationFlow / LanguageSettings / Quota / RetryPolicy / Subscription
├─ infra/                 # LINE API, Gemini 翻訳, Command Router, Neon Repository など
├─ presentation/          # webhook parser, reply builder/formatter
└─ config.py              # 設定ローダ
```

---

## Setup

### 1. Clone

```
git clone https://github.com/yourname/line-multilang-bot.git
cd line-multilang-bot
```

---

### 2. Install dependencies (always in `.venv`)

テスト/ローカル実行は必ず `.venv` の Python を使います。

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

### 2-1. Run tests (always in `.venv`)

```bash
./scripts/test.sh
```

個別テストを実行する場合:

```bash
./scripts/test.sh tests/test_contact_form_handler.py -q
```

---

### 3. Create Neon Database

Neon で PostgreSQL のプロジェクトを作成し、
接続文字列（`postgres://...`）を取得します。

テーブル例（最小構成）：

```sql
CREATE TABLE group_members (
    group_id TEXT,
    user_id TEXT,
    preferred_lang VARCHAR(10),
    updated_at TIMESTAMP DEFAULT NOW(),
    PRIMARY KEY (group_id, user_id)
);

CREATE TABLE messages (
    id SERIAL PRIMARY KEY,
    group_id TEXT,
    user_id TEXT,
    text TEXT,
    timestamp TIMESTAMP DEFAULT NOW()
);
```

---

### 4. Set environment variables

`.env`（ローカル用）：

```
LINE_CHANNEL_SECRET=xxx
LINE_CHANNEL_ACCESS_TOKEN=xxx
NEON_DATABASE_URL=postgres://...
GEMINI_API_KEY=your_google_ai_key
OPENAI_API_KEY=your_openai_key
OPENAI_SUPPORT_MODEL=gpt-5.2
OPENAI_GROUP_MENTION_MODEL=gpt-5.2
OPENAI_GUARDRAIL_MODEL=gpt-4.1-mini
PRIVATE_CHAT_HISTORY_LIMIT=5
STRIPE_SECRET_KEY=sk_live_or_test
STRIPE_WEBHOOK_SECRET=whsec_xxx
STRIPE_PRICE_STANDARD_MONTHLY_ID=price_xxx
STRIPE_PRICE_STANDARD_YEARLY_ID=price_xxx
STRIPE_PRICE_PRO_MONTHLY_ID=price_xxx
STRIPE_PRICE_PRO_YEARLY_ID=price_xxx
STRIPE_PRICE_PRO_LEGACY_MONTHLY_ID=price_xxx
SUBSCRIPTION_TOKEN_SECRET=your_random_hmac_secret
MESSAGE_ENCRYPTION_KEY=your_base64_or_raw_encryption_key
CONTACT_TO_EMAIL=contact@iwasadigital.com
CONTACT_FROM_EMAIL=no-reply@iwasadigital.com
CONTACT_ALLOWED_ORIGINS=https://kotori-ai.com,http://localhost:5500
CONTACT_RATE_LIMIT_MAX=5
CONTACT_RATE_LIMIT_WINDOW_SECONDS=600
CONTACT_IP_HASH_SALT=your_random_salt
# Optional: override default quotas (Free=50, Standard=4000, Pro=40000 messages/month)
# FREE_QUOTA_PER_MONTH=50
# STANDARD_QUOTA_PER_MONTH=4000
# PRO_QUOTA_PER_MONTH=40000
```

Lambda では **環境変数として設定**してください。

お問い合わせフォーム API (`POST /contact`) を有効化する場合は、SES の Identity 検証（`no-reply@iwasadigital.com` または `iwasadigital.com`）が必要です。

Neon のレート制限テーブルも事前作成してください。

```sql
-- sql/20260211_add_contact_rate_limits.sql
CREATE TABLE IF NOT EXISTS contact_rate_limits (
    ip_hash TEXT NOT NULL,
    window_start TIMESTAMPTZ NOT NULL,
    count INTEGER NOT NULL DEFAULT 0,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (ip_hash, window_start)
);
```

SES Identity の CLI 例（ap-northeast-1）:

```bash
# ドメイン Identity 作成（推奨）
aws sesv2 create-email-identity \
  --profile line-translate-bot \
  --region ap-northeast-1 \
  --email-identity iwasadigital.com

# ステータス確認
aws sesv2 get-email-identity \
  --profile line-translate-bot \
  --region ap-northeast-1 \
  --email-identity iwasadigital.com
```

---

### 5. Enable LINE Webhook

1. LINE Developers の Messaging API チャネルを作成
2. Webhook URL を API Gateway のエンドポイントに設定
3. Webhook を ON

---

## Deployment

本番/ステージングとも AWS SAM でデプロイします。現在は `scripts/deploy.sh` を正規手順として運用します。

### 前提
- AWS CLI / SAM CLI / Python 3.12 がローカルにインストール済み
- `aws configure --profile line-translate-bot` で ap-northeast-1 の資格情報を設定済み
- Secrets Manager のシークレットを環境ごとに用意済み
  - `stg/line-translate-bot-secrets`
  - `prod/line-translate-bot-secrets`
  - 主要キー:
  - `LINE_CHANNEL_SECRET`
  - `LINE_CHANNEL_ACCESS_TOKEN`
  - `GEMINI_API_KEY`
  - `NEON_DATABASE_URL`
  - `STRIPE_SECRET_KEY`
  - `STRIPE_WEBHOOK_SECRET`
  - `STRIPE_PRICE_STANDARD_MONTHLY_ID`
  - `STRIPE_PRICE_STANDARD_YEARLY_ID`
  - `STRIPE_PRICE_PRO_MONTHLY_ID`
  - `STRIPE_PRICE_PRO_YEARLY_ID`
  - `STRIPE_PRICE_PRO_LEGACY_MONTHLY_ID`
  - `SUBSCRIPTION_FRONTEND_BASE_URL`（本番: `https://kotori-ai.com`）
  - `CHECKOUT_API_BASE_URL`（同一オリジン運用では通常空）
  - `SUBSCRIPTION_TOKEN_SECRET`
  - `CHECKOUT_SESSION_SECRET`
  - `LINE_LOGIN_CHANNEL_ID`
  - `LINE_LOGIN_CHANNEL_SECRET`
  - `LINE_LOGIN_REDIRECT_URI`
  - `MESSAGE_ENCRYPTION_KEY`
  - `CONTACT_IP_HASH_SALT`
- `.env` にはローカル検証用の `NEON_DATABASE_URL` / `GEMINI_API_KEY` を入れてテスト可能

### 1. 依存パッケージの準備

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. SAM ビルド

```bash
sam build
```

### 3. デプロイ（推奨: `scripts/deploy.sh`）

```bash
# ステージング
STACK_NAME=translate-line-bot-stg STAGE=stg PROFILE=line-translate-bot GEMINI_MODEL=gemini-2.5-flash ./scripts/deploy.sh

# 本番
STACK_NAME=translate-line-bot-prod STAGE=prod PROFILE=line-translate-bot GEMINI_MODEL=gemini-2.5-flash ./scripts/deploy.sh
```

### 3-1. SAM 直接実行の例（必要時のみ）

```bash
sam deploy \
  --stack-name translate-line-bot-stg \
  --region ap-northeast-1 \
  --profile line-translate-bot \
  --capabilities CAPABILITY_IAM \
  --resolve-s3 \
  --parameter-overrides \
    StageName=stg \
    FunctionMemorySize=512 \
    FunctionTimeout=60 \
    GeminiModel=gemini-2.5-flash \
    MaxContextMessages=8 \
    TranslationRetry=2 \
    RuntimeSecretArn=stg/line-translate-bot-secrets \
    EnableStripe=true
```

初回のみ `--guided` で `StageName` や `RuntimeSecretArn` を対話入力し、`samconfig.toml` に保存すると便利です。`--resolve-s3` を付けると SAM が管理 S3 バケットを自動で用意します。

### 3-2. デプロイスクリプト (`scripts/deploy.sh`) の利用

手元の環境変数で上書きしつつ、以下を一括実行できます。
- `sql/*.sql` の DB マイグレーション適用
- SAM ビルド
- SAM デプロイ

`STAGE=stg` のときは `stg/line-translate-bot-secrets` の `NEON_DATABASE_URL` にだけ、
`STAGE=prod` のときは `prod/line-translate-bot-secrets` の `NEON_DATABASE_URL` にだけ適用されます。

```bash
# ステージング（既定値のまま）
./scripts/deploy.sh

# 任意のデプロイ用 S3 バケットを指定
S3_BUCKET=my-sam-artifacts ./scripts/deploy.sh

# 本番スタックなど別環境にデプロイ
STACK_NAME=translate-line-bot-prod STAGE=prod PROFILE=line-translate-bot ./scripts/deploy.sh
```

主な上書き可能変数:
- `STACK_NAME` (既定: translate-line-bot-stg)
- `PROFILE` (既定: line-translate-bot)
- `REGION` (既定: ap-northeast-1)
- `STAGE` (既定: stg)
- `S3_BUCKET`（未指定なら `--resolve-s3` で自動バケット利用）
- `RUNTIME_SECRET_ARN` ほか Lambda パラメータ
- `PYTHON_BIN`（既定: `python3`。`.venv/bin/python` があれば自動で優先）

#### デプロイ時のよくあるミス
- `--stack-name` を付け忘れると SAM がどのスタックを更新するか判断できず即失敗します。常に `translate-line-bot-stg` を指定してください。
- Lambda コードを S3 にアップロードするため `--resolve-s3` か `--s3-bucket` のどちらかが必須です。付け忘れると「S3 Bucket not specified」で止まります。
- IAM 権限を含むスタックなので `--capabilities CAPABILITY_IAM` が必要です。これを付けないと CloudFormation の ChangeSet 作成が `Requires capabilities : [CAPABILITY_IAM]` で失敗します。

### 4. デプロイ確認

```bash
aws cloudformation describe-stacks \
  --stack-name translate-line-bot-stg \
  --profile line-translate-bot \
  --query "Stacks[0].Outputs"
```

`HttpApiEndpoint` が表示されたら、LINE Developers の Webhook URL を更新し、`sam logs -n LineWebhookFunction --stack-name translate-line-bot-stg --profile line-translate-bot` で CloudWatch Logs も確認してください。

### 5. 補足（同一オリジン）

- ホームページ側 API は `https://kotori-ai.com/api/*` に固定。
- CloudFront 側で `/api` プレフィックスを除去して API Gateway に転送。
- そのため GitHub Pages 直配信だけでは `/api/*` は解決できない（現在は GH Pages を廃止）。

---

## Message Flow

1. LINE から Webhook イベント受信
2. Lambda が署名検証と処理を実行
3. Lambda 内で以下を実行：

   * Neon からグループの翻訳対象言語を取得
   * Neon から過去20件の文脈を取得
   * 翻訳先言語リストを決定
   * Gemini 2.5 Flash に1回だけ Structured Output で翻訳要求
   * LINE Messaging API へ翻訳を送信
4. 処理完了後に `200 OK` を返却

---

## Example Output

```
これめっちゃおいしい！

This is super delicious!
อันนี้อร่อยมาก!
```

---

## Future Enhancements

* キャッシュによる高速化（LRU or in-memory）
* SQS による非同期処理の安定化

---
