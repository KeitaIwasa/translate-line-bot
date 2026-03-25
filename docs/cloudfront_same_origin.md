# CloudFront 同一オリジン化（`/api` ルーティング）

このドキュメントは、`kotori-ai.com` で静的サイトと API を同一オリジン配信するための導入手順です。  
WAF は導入しない前提です。

## 1. 事前準備

- ACM 証明書（`us-east-1`）を `kotori-ai.com` 向けに用意
- API Gateway の実ドメインを確認  
  例: `abcdef.execute-api.ap-northeast-1.amazonaws.com`
- 静的配信オリジンを決定  
  例: S3 website endpoint / 既存静的ホスト

## 2. CloudFront 作成

テンプレート: `infra/cloudfront_same_origin_template.yaml`

主な挙動:
- `/*` は静的オリジンへ転送
- `/api/*` は API Gateway オリジンへ転送
- CloudFront Function で `/api` プレフィックスを除去して API Gateway に渡す
- セキュリティヘッダを CloudFront で付与
  - `Referrer-Policy: no-referrer`
  - `Content-Security-Policy`（`connect-src` は `'self'`）

デプロイ例:

```bash
aws cloudformation deploy \
  --stack-name kotori-cloudfront-same-origin \
  --template-file infra/cloudfront_same_origin_template.yaml \
  --capabilities CAPABILITY_NAMED_IAM \
  --parameter-overrides \
    DomainName=kotori-ai.com \
    AcmCertificateArn=arn:aws:acm:us-east-1:123456789012:certificate/xxxx \
    HostedZoneId=ZXXXXXXXXXXXXX \
    StaticOriginDomainName=example-bucket.s3-website-ap-northeast-1.amazonaws.com \
    StaticOriginProtocolPolicy=http-only \
    ApiGatewayDomainName=abcdef.execute-api.ap-northeast-1.amazonaws.com \
    ApiOriginPath=/prod
```

## 3. アプリ側の前提

本リポジトリの実装は次を前提にしています。

- フロント JS は API を相対パス固定で呼ぶ
  - `/api/checkout`
  - `/api/contact`
  - `/api/stats/total-users`
- `api_base` クエリは廃止済み
- バックエンドは `api_base` を受け取っても無視し、警告ログを出す

## 4. 確認項目

- `https://kotori-ai.com/pro.html` から `mode=status/start/portal/auth_start` がすべて成功
- `https://kotori-ai.com/contact.html` から `/api/contact` が成功
- `https://kotori-ai.com/` の人数表示が `/api/stats/total-users` で取得できる
- `?api_base=https://attacker.example` を付けても API 送信先が変化しない
- API レスポンスヘッダに `Access-Control-Allow-Origin: *` が含まれない
