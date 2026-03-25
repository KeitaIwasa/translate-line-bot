# CloudFront 同一オリジン運用（`/api` ルーティング）

このドキュメントは、`kotori-ai.com` で静的サイトと API を同一オリジン配信する現行運用の記録です。
WAF は導入しない前提です。

## 現在の本番構成（2026-03-25 時点）

- ドメイン: `https://kotori-ai.com`
- CloudFront Distribution: `E38ONIMIEBQ6UE`
- CloudFront Domain: `d1w7go0b7qo42m.cloudfront.net`
- TLS証明書（us-east-1 ACM）:
  - `arn:aws:acm:us-east-1:215896857123:certificate/785af9cc-8f37-4354-a747-0cd99fe5af01`
- DNS（Cloudflare）:
  - `kotori-ai.com` は CloudFront への CNAME
- オリジン:
  - Static: `kotori-ai-static-origin-215896857123-1774415519.s3-website-ap-northeast-1.amazonaws.com`
  - API: `h2xf6dwz5e.execute-api.ap-northeast-1.amazonaws.com`（OriginPath: `/prod`）

## ルーティング仕様

- `/*` は静的オリジンへ転送
- `/api/*` は API Gateway オリジンへ転送
- CloudFront Function（viewer-request）で `/api` プレフィックスを除去して API Gateway に渡す

例:
- `GET /api/stats/total-users` → API Gateway 側 `GET /stats/total-users`
- `POST /api/contact` → API Gateway 側 `POST /contact`
- `GET /api/checkout?...` → API Gateway 側 `GET /checkout?...`

## セキュリティ設定

CloudFront Response Headers Policy で以下を付与:

- `Referrer-Policy: no-referrer`
- `Content-Security-Policy`（`connect-src 'self'`）
- `X-Frame-Options: DENY`
- `X-Content-Type-Options: nosniff`
- `Strict-Transport-Security`
- `Permissions-Policy: geolocation=(), microphone=(), camera=()`

また、アプリ側の前提は以下:

- フロントの API 呼び出しは相対パス固定
  - `/api/checkout`
  - `/api/contact`
  - `/api/stats/total-users`
- `api_base` / `apiBase` クエリは廃止
- バックエンドは `api_base` が来ても無視し、警告ログのみ出力

## 再デプロイ手順（同一構成を再現する場合）

### 1. 静的ファイルを S3 へ同期

```bash
aws s3 sync ./kotori-homepage/ \
  s3://kotori-ai-static-origin-215896857123-1774415519/ \
  --delete \
  --exclude ".git/*" \
  --exclude "AGENTS.md" \
  --exclude "README.md" \
  --exclude "CNAME" \
  --profile line-translate-bot
```

### 2. API（SAM）を更新

```bash
cd translate-line-bot
STACK_NAME=translate-line-bot-prod STAGE=prod PROFILE=line-translate-bot GEMINI_MODEL=gemini-2.5-flash ./scripts/deploy.sh
```

### 3. CloudFront キャッシュ無効化

```bash
aws cloudfront create-invalidation \
  --distribution-id E38ONIMIEBQ6UE \
  --paths '/*' \
  --profile line-translate-bot
```

## 確認項目

- `https://kotori-ai.com/` が `200`
- `https://kotori-ai.com/pro.html` が `200`
- `https://kotori-ai.com/api/stats/total-users` が `200`
- `https://kotori-ai.com/api/contact` の preflight が成功し、`Access-Control-Allow-Origin: *` ではない
- `?api_base=https://attacker.example` を付与しても送信先が外部ホストに変化しない

## 補足

- `www.kotori-ai.com` は別運用で apex (`kotori-ai.com`) へ 301 リダイレクト。
- GitHub Pages は無効化済み。静的配信の正系は CloudFront + S3。
