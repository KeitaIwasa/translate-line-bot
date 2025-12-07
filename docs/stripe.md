# Stripeを用いた収益化に関するドキュメント

## 収益化モデルの基本方針

| 項目   | 採用案                                            |
| ---- | ---------------------------------------------- |
| 課金単位 | **LINE グループ単位**                                |
| 課金形態 | 月額サブスクリプション                                    |
| 無料枠  | 月50メッセージ（翻訳結果数）                  |
| 課金連動 | 無料枠超過後に「課金URL（Stripe Checkout）」を返信、支払い確認後に翻訳再開 |
| 請求先  | グループの代表者（誰が払ってもよい）                             |


## 課金フロー

無料枠内 → 無料で翻訳
↓
無料枠超過 → 料金案内 + Checkoutリンクをグループへ送信
↓
ユーザーが決済 → StripeがWebhookで通知
↓
DB更新（サブスク有効） → 翻訳再開


## Stripe 情報のデータモデル
現状のテーブルを生かして、以下を追加
1. `group_subscriptions`
| 列                     | 型          | 説明                                  |
| ---------------------- | ----------- | ------------------------------------- |
| group_id               | TEXT(PK)    | LINEグループID                            |
| stripe_customer_id     | TEXT        | Stripe顧客                              |
| stripe_subscription_id | TEXT        | Stripeサブスク                            |
| status                 | TEXT        | active / canceled / trialing / unpaid |
| current_period_end     | TIMESTAMPTZ | 次請求タイミング                              |

2. `group_usage_counters`
| 列                 | 型          | 説明               |
| ----------------- | ---------- | ---------------- |
| group_id          | TEXT (PK)  |                  |
| month_key         | VARCHAR(7) | 2025-01 のような年月キー |
| translation_count | INT        | 今月の翻訳回数          |
PK: (group_id, month_key)
→ 月初に自動的に新規レコードを作成(新規Lambda関数で対応)


## Stripe Webhook（Lambda別ファンクション）
Stripe から以下イベントを受け取る：
| Event                           | 意味         |
| ------------------------------- | ---------- |
| `invoice.payment_succeeded`     | サブスク有効化/更新 |
| `customer.subscription.deleted` | 解約         |
| `invoice.payment_failed`        | 支払い失敗      |

Webhook Lambda の処理：
1. Stripeの subscription_id から group_id をDB検索
2. status, current_period_end を反映
3. 該当グループの翻訳制限フラグ解除


## 料金リンク（Stripe Checkout）
「課金URLを提供」という仕様に対応：
Webhook返信文例：
```
無料枠を使い切りました。
引き続き翻訳をご利用いただくには、以下のURLからご購入ください:

https://checkout.stripe.com/...
```

支払い後、以下メッセージを送信：
```
ご購入ありがとうございます！
翻訳サービスの利用が再開しました。
```


## セキュリティ設計
- Checkout URLは使い回し不可（毎回セッション生成）
- group_id を Stripe にメタデータとして付与
- 決済データをDBに保存せず、常に Stripe Webhook による同期を信頼


## 料金プラン例
| プラン | 内容                         | 料金    |
| ----- | -------------------------- | ----- |
| Free  | 月50メッセージ               | 無料    |
| Pro(月次のみ) | 月3000メッセージ（1グループ） | 380円/月 |
MVPは月次プランのみ提供

## 環境変数（Secrets Manager）
- `STRIPE_SECRET_KEY`: Stripe シークレットキー
- `STRIPE_WEBHOOK_SECRET`: Webhook 署名検証用シークレット
- `STRIPE_PRICE_MONTHLY_ID`: サブスク用 Price ID（月次のみ提供）

