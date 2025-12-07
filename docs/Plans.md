# Stripe 収益化実装計画（Stripe ドキュメントに基づく）

## ゴール
- LINE グループ単位で月額サブスク課金（無料枠: 月50メッセージ）を導入し、無料枠超過時に Stripe Checkout へ誘導、決済完了で翻訳を再開できる状態にする。

## ToDo
- [x] DB拡張: `group_subscriptions` と `group_usage_counters` を追加し、現行テーブルとの関係を整理（PK/インデックス設計、月初自動作成ロジックの仕様化）。
- [x] インフラ: SAM テンプレートに Stripe Webhook 専用 Lambda と月次カウンタ初期化用 Lambda を追加。環境変数・IAM 権限・イベントトリガー（Webhook エンドポイント / EventBridge スケジュール）を定義。
- [x] 決済導線: 無料枠超過時に Checkout セッションを生成し、group_id をメタデータに付与した一時 URL を返信する処理を実装。料金プランは月次のみ。
- [x] Webhook 処理: `invoice.payment_succeeded` / `customer.subscription.deleted` / `invoice.payment_failed` を受信し、`group_subscriptions` を更新（status, current_period_end, stripe IDs）し翻訳制限フラグを解除/付与。
- [x] 利用カウント: 翻訳実行時に `group_usage_counters` をインクリメントし、無料枠・有料枠の判定を統一的に行うサービス層を追加。月次キー生成/ローテーションを実装。
- [x] フロー制御: 無料枠超過時は翻訳を中断し課金案内メッセージを返信、支払い後は「利用再開」メッセージを送信する分岐をメッセージ処理に組み込む。
- [x] 設定/Secrets: Stripe Secret/Webhook Secret/Price ID（月次）を Secrets Manager 連携し、環境変数に反映。
- [ ] テスト: Stripe SDK をモックした単体テスト、Webhook イベントの疑似ペイロードを用いた統合テストを追加。既存テストとの整合を確認。
- [x] ドキュメント: `README`/`AGENTS` に運用手順（無料枠、支払い後の挙動、価格 ID 差し替え手順、Webhook 構築手順）を追記。
- [ ] ステージング検証: ステージング Stripe アカウントで Checkout → Webhook → 翻訳再開までの E2E を確認し、手動 QA チェックリストを残す。
