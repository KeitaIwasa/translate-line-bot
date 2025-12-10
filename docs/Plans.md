# Stripe 収益化実装計画（Stripe ドキュメントに基づく）

## ゴール
- LINE グループ単位で月額サブスク課金（無料枠: 月50メッセージ）を導入し、無料枠超過時に Stripe Checkout へ誘導、決済完了で翻訳を再開できる状態にする。

## ToDo
- [x] DB拡張: `group_subscriptions` と `group_usage_counters` を追加し、現行テーブルとの関係を整理（PK/インデックス設計、月初自動作成ロジックの仕様化）。
- [x] インフラ: SAM テンプレートに Stripe Webhook 専用 Lambda と月次カウンタ初期化用 Lambda を追加。環境変数・IAM 権限・イベントトリガー（Webhook エンドポイント / EventBridge スケジュール）を定義。
- [x] 決済導線: 無料枠超過時に Checkout セッションを生成し、group_id をメタデータに付与した一時 URL を返信する処理を実装。料金プランは月次のみ。
- [x] Webhook 処理: `invoice.payment_succeeded` / `customer.subscription.deleted` / `invoice.payment_failed` を受信し、`group_subscriptions` を更新（status, current_period_end, stripe IDs）し翻訳制限フラグを解除/付与。
- [x] 利用カウント: 翻訳実行時に `group_usage_counters` をインクリメントし、無料枠・有料枠の判定を統一的に行うサービス層を追加。月次キー生成/ローテーションを実装。
- [x] 課金サイクル連動: Stripe の `billing_cycle_anchor` / `current_period_start` に基づいて利用カウンタのリセットキーを算出し、`group_usage_counters` のキー設計・初期化ジョブ・上限通知判定をサブスク開始日基準に統一（未課金時は暦月運用を維持）。
- [x] フロー制御: 無料枠超過時は翻訳を中断し課金案内メッセージを返信、支払い後は「利用再開」メッセージを送信する分岐をメッセージ処理に組み込む。
- [x] 料金支払い確認時の案内文をグループの設定言語すべてへ翻訳・列挙して通知する。
- [x] 設定/Secrets: Stripe Secret/Webhook Secret/Price ID（月次）を Secrets Manager 連携し、環境変数に反映。
- [x] Pro 上限: Pro プランの月間メッセージ上限を 8,000 件に設定し、`PRO_QUOTA_PER_MONTH` 環境変数で上書きできるようにする。
- [ ] テスト: Stripe SDK をモックした単体テスト、Webhook イベントの疑似ペイロードを用いた統合テストを追加。既存テストとの整合を確認。
- [x] ドキュメント: `README`/`AGENTS` に運用手順（無料枠、支払い後の挙動、価格 ID 差し替え手順、Webhook 構築手順）を追記。
- [ ] ステージング検証: ステージング Stripe アカウントで Checkout → Webhook → 翻訳再開までの E2E を確認し、手動 QA チェックリストを残す。
- [x] 決済導線改善: /checkout リダイレクトで短い決済リンクを配信し、LINE 上での URL 可読性を向上。
- [ ] 決済後の遷移先: Checkout の `success_url` / `cancel_url` を自前のサンクスページ（LINE への遷移ボタン付き）に差し替え、スマホでは LINE グループへ自動遷移する導線を用意する。
- [ ] サンクスページホスティング: GitHub Pages 上の静的ページでサンクス画面を提供し、Stripe.js（publishable key のみ）で `retrieveCheckoutSession(session_id)` を用いてステータス表示しつつ、`line://` ディープリンクを試行する実装を追加。

---

# メンション機能拡張（サブスク管理）

## ゴール
- ボットメンション経由でサブスク状態の確認・停止・Pro へのアップグレードを自己完結できる操作メニューを提供し、グループ管理者が LINE 上だけで課金関連の操作を完了できるようにする。

## ToDo
- [ ] コマンド判定拡張: Gemini の command router に `subscription_menu` / `subscription_cancel` / `subscription_upgrade` を追加し、ヒント語と ack テンプレート（英語原文）を定義する。
- [ ] メニューUI: メンション入力に応じて Buttons テンプレートを返すハンドラを追加し、ボタン文言の英語原文＋設定言語への翻訳を組み立てる共通関数を用意する。
- [ ] 現在プラン表示: `group_subscriptions` 状態と Stripe Customer Portal セッションを用いて「請求内容を見る」リンクを生成し、メニュー/ボタン経由で返す（return_url は LINE グループ遷移を指定）。
- [ ] サブスク停止フロー: 確認テンプレート表示→承認時に Stripe の cancel_at_period_end=True で解約予約し、DB を即時更新（status / current_period_end / canceled_at）し多言語で完了メッセージを返す。
- [ ] Pro へのアップグレード: 現プランが Free の場合のみ Pro 価格 ID で Checkout セッションを発行し、URL を応答。既に課金中の場合はガード応答を返す。
- [ ] Postback/状態管理: Buttons/Confirm の postback data をパースし、メンションハンドラ側で各フローにディスパッチするルーティングを実装。重複クリック抑止のため idempotency key を用意。
- [ ] テスト: Command Router の JSON 出力、メンションハンドラのメニュー/解約/アップグレード分岐、Stripe SDK モックを用いた単体テスト、Webhook 連携を含む統合テストを追加。
- [ ] ステージング検証: ステージング Stripe でメンション→メニュー表示→解約予約→再課金（Pro）までを E2E 検証し、確認結果を記録する。
