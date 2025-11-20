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

AWS SDKを使用

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
