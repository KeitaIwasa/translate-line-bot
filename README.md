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
- **QuotaService**: 無料/有料ごとの上限管理と通知要否を決定。
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
STRIPE_SECRET_KEY=sk_live_or_test
STRIPE_WEBHOOK_SECRET=whsec_xxx
STRIPE_PRICE_MONTHLY_ID=price_xxx
# Optional: override default quotas (Free=50, Pro=8000 messages/month)
# FREE_QUOTA_PER_MONTH=50
# PRO_QUOTA_PER_MONTH=8000
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
- Secrets Manager `prod/line-translate-bot-secrets` に以下キーを保存済み（`RuntimeSecretArn` パラメータで参照）
  - `LINE_CHANNEL_SECRET`
  - `LINE_CHANNEL_ACCESS_TOKEN`
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
    RuntimeSecretArn=arn:aws:secretsmanager:ap-northeast-1:215896857123:secret:prod/line-translate-bot-secrets-Uqg35U
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

### 6. 新アーキテクチャに切り替える場合

`template.yaml` の Lambda 設定を以下のように変更します（例）:

```yaml
Resources:
  LineWebhookFunction:
    Type: AWS::Serverless::Function
    Properties:
      CodeUri: src_new        # ルートを src_new に変更
      Handler: lambda_handler.lambda_handler
      Runtime: python3.12
      # 既存の環境変数/ロール/タイムアウトなどはそのまま
```

もしくは Handler を `src_new.lambda_handler::lambda_handler` に設定する方法でも可。デプロイ前に `sam build` で解決されることを確認してください。

---

## Message Flow

1. LINE から Webhook イベント受信
2. Lambda が `200 OK` を即返信（高速化）
3. Lambda 内で非同期的に以下を実行：

   * Neon からグループの翻訳対象言語を取得
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
