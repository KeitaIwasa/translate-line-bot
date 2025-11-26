# README.md

# LINE Multilingual Translation Bot

**A fast, minimal, serverless multilingual translation bot for LINE groups.**
Built with AWS Lambda (Python), Gemini 2.5 Flash, and Neon (PostgreSQL).

本プロジェクトは、LINE グループ内のユーザーが書いたメッセージを、
**グループ内の他メンバーの言語へ自動翻訳する高速翻訳ボット**です。

Gemini 2.5 Flash の Structured Output を用いて、
**文脈（最大20件）＋話者情報込みの高度な自然翻訳**を実現します。

---

## Features

* 多言語ユーザーが混在する LINE グループ向け
* 各ユーザーが希望言語を登録
* メッセージの「原文の言語以外」に翻訳を自動生成
* Gemini 2.5 Flash Structured Output による自然＋安定した JSON 生成
* AWS Lambda による完全サーバレス構成
* Neon (PostgreSQL) で言語設定とメッセージ履歴を管理
* 高速動作（平均 200〜350ms 程度）

---

## Architecture (Minimal Version)

```
LINE → API Gateway → Lambda(Python)
                     ├→ NeonDB (language settings / context 20 messages)
                     ├→ Gemini 2.5 Flash (translation)
                     └→ LINE Messaging API (reply)
```

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

## Repository Structure

```
src/
├─ lambda_handler.py         # Lambdaエントリポイント
├─ line_webhook.py           # LINE Webhook処理
├─ line_api.py               # LINE Messaging API呼び出し
│
├─ translator/
│   ├─ gemini_client.py      # Gemini 2.5 Flash呼び出し
│   └─ schema.py             # Structured Output JSON Schema
│
├─ db/
│   ├─ neon_client.py        # Neon用DB接続クライアント
│   └─ repositories.py       # 言語設定/文脈取得処理
│
├─ config.py                 # 環境変数管理
└─ utils/ (必要なら)
```

---

## Setup

### 1. Clone

```
git clone https://github.com/yourname/line-multilang-bot.git
cd line-multilang-bot
```

---

### 2. Install dependencies

Lambda 用の依存を入れます。

```
pip install -r requirements.txt
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
```

Lambda では **環境変数として設定**してください。

---

### 5. Enable LINE Webhook

1. LINE Developers の Messaging API チャネルを作成
2. Webhook URL を API Gateway のエンドポイントに設定
3. Webhook を ON

---

## Deployment

本番/ステージングとも AWS SAM でデプロイします。以下はステージング (`translate-line-bot-stg`) の例です。

### 前提
- AWS CLI / SAM CLI / Python 3.12 がローカルにインストール済み
- `aws configure --profile line-translate-bot` で ap-northeast-1 の資格情報を設定済み
- Secrets Manager `line-translate-bot-secrets` に以下キーを保存済み（`RuntimeSecretArn` パラメータで参照）
  - `LINE_CHANNEL_SECRET`
  - `LINE_CHANNEL_ACCESS_TOKEN`
  - `LINE_BOT_USER_ID`
  - `GEMINI_API_KEY`
  - `NEON_DATABASE_URL`
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

### 3. デプロイ

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
    FunctionTimeout=15 \
    GeminiModel=gemini-2.5-flash \
    MaxContextMessages=20 \
    TranslationRetry=3 \
    RuntimeSecretArn=arn:aws:secretsmanager:ap-northeast-1:215896857123:secret:line-translate-bot-secrets-Uqg35U
```

初回のみ `--guided` で `StageName` や `RuntimeSecretArn` を対話入力し、`samconfig.toml` に保存すると便利です。`--resolve-s3` を付けると SAM が管理 S3 バケットを自動で用意します。

### 3-1. デプロイスクリプト (`scripts/deploy.sh`) の利用

手元の環境変数で上書きしつつ、ビルドからデプロイまで一括実行できます。

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

### 5. Lambda コードのみを差し替えたい場合

SAM 全体を更新せずコードだけ入れ替える場合は、`scripts/deploy.sh` を利用します。

```bash
export LAMBDA_FUNCTION_NAME=translate-line-bot-stg-LineWebhookFunction
export AWS_REGION=ap-northeast-1
./scripts/deploy.sh
```

Dependencies（`requirements.txt`）と `src/` を zip 化して `aws lambda update-function-code` を呼び出します。構成値を変える場合は SAM で再デプロイしてください。

---

## Message Flow

1. LINE から Webhook イベント受信
2. Lambda が `200 OK` を即返信（高速化）
3. Lambda 内で非同期的に以下を実行：

   * Neon からユーザー言語設定を取得
   * Neon から過去20件の文脈を取得
   * 翻訳先言語リストを決定
   * Gemini 2.5 Flash に1回だけ Structured Output で翻訳要求
   * LINE Messaging API へ翻訳を送信

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
