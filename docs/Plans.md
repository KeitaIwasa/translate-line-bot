## リファクタリング計画（DRY/SOLID 適用）

- [x] 現状調査：イベントハンドラ（特に `MessageHandler` / `PostbackHandler`）とインフラ層の責務分担・重複処理を洗い出し、改善ポイントを列挙する。
- [x] 設計方針策定：翻訳/購読/言語設定などのユースケース単位でサービスクラスを定義し、インターフェース経由の依存注入に揃える（DIP/SRP）。
- [x] 共通コンポーネント抽出：
  - 翻訳リクエスト組み立て、言語上限チェック、メッセージ整形などハンドラ間で重複する処理をユーティリティまたはドメインサービスへ集約（DRY）。
  - Gemini/LINE/Neon クライアントの生成をファクトリ/ブートストラップモジュールで一元化し、設定値の重複を排除。
- [x] ハンドラ分割：`MessageHandler` をコマンド処理・翻訳フロー・課金/クオータ管理の小さなクラスに分離し、公開インターフェースで接続（SRP/OCP）。
- [x] ポストバック処理整理：言語確認/サブスク取消のロジックをサービスに移し、ハンドラは協調ロジックのみを担当する。
- [x] エラーハンドリング共通化：レートリミット通知・リトライ・ロギング方針を横断的にまとめ、テストしやすい形で注入する。
- [x] テスト強化：新設サービスの単体テストと既存 E2E テストを追加・更新し、`pytest` で回帰を確認。
- [ ] ステージングデプロイ：全テスト通過後、ステージング環境へデプロイし、主要ユースケースを目視確認。

### 設計方針（サービス分割・依存関係）
- **TranslationFlowService（新設）**: グループ翻訳の主処理を担当。コンテキスト取得、ターゲット言語制限、翻訳実行、返信テキスト組み立てまでを一箇所に集約。`TranslationService` と `InterfaceTranslationService`、`MessageRepositoryPort` を依存注入。
- **CommandHandlingService（新設）**: コマンド判定結果を受け、言語設定・使い方案内・一時停止/再開・サブスク操作をユースケース別に委譲するオーケストレーション層。`GeminiCommandRouter` はここから呼び出し、ハンドラは薄く保つ。
- **LanguageSettingsService（新設）**: 言語登録/上限チェック/確認プロンプト生成/キャンセル処理を集約し、`MessageHandler` と `PostbackHandler` から共通利用する。`LanguagePreferencePort` と `MessageRepositoryPort`、インターフェース翻訳サービスを依存注入。
- **QuotaService（新設）**: 無料/有料判定、期間キー算出、利用カウント増分、上限通知要否判定を責務とする。翻訳実行と分離し、テストしやすい純粋ロジックを目指す。
- **SubscriptionCoordinator（再編）**: 既存 `SubscriptionService` を活かしつつ、メニュー生成・キャンセル/アップグレード導線生成をまとめるファサードを用意し、ハンドラは返信生成のみを担当。
- **InterfaceTextService（再編）**: UI 文言の翻訳・テンプレート整形・多言語展開を一元化し、通知/メニュー/ガイダンスで重複しているフォーマッタを削減。
- **RetryPolicy / RateLimitNotifier（横断）**: 翻訳系リトライとレートリミット通知を小さなクラス/モジュールに切り出し、各サービスに注入（DRY）。
- **Bootstrap 整理**: `build_dispatcher` でのクライアント・サービス生成をファクトリ的に分割し、設定値の重複・循環依存を防ぐ。依存はポート越しに注入し、ハンドラはユースケースサービスへの委譲のみ行う。
- **Port 契約明確化**: `MessageRepositoryPort` にサブスク関連メソッドを明示化し、`getattr` フォールバックを廃止。必要に応じて専用ポートを分割して ISP を満たす。
- **レスポンス組立の分離**: 翻訳結果や通知を「送信するメッセージ DTO」として返す `ReplyBuilder` 的コンポーネントを導入し、ハンドラは LINE 送信のみに集中。

### 進捗メモ（インターフェース定義）
- 新規 DTO `ReplyBundle` を追加し、返信構築の戻り値を統一するための器を用意。
- ポートを分割: `UsageRepositoryPort` / `SubscriptionRepositoryPort` / `ReplyBuilderPort` を定義（ISP/DIP 強化）。
- サービス雛形を追加: `TranslationFlowService` / `LanguageSettingsService` / `QuotaService` をドメイン層に作成し、順次ロジックを移植できる状態にした。
- QuotaService に既存クオータ判定ロジックを実装し、`MessageHandler` から利用開始（上限判定の責務をドメインサービスへ移譲）。
- TranslationFlowService に翻訳実行パスを移植し、MessageHandler の翻訳/通知責務を縮小。ReplyBuilder を追加。
- LanguageSettingsService を実装し、言語設定プロンプト生成・確認/キャンセル処理を MessageHandler/PostbackHandler から完全委譲。ポストバック処理はサービス経由に統一。
- RetryPolicy を導入し、翻訳系リトライを共通化（エラーハンドリングの横断化）。
- `pytest` フルスイート実行（2件 skip: 既存 live/gemini 系）。
- 未知コマンド案内のベース文言を英語に変更し、多言語展開の基点を統一。

### 現状調査メモ
- `MessageHandler` がコマンド処理・翻訳フロー・課金/クオータ管理・UI翻訳・言語設定プロンプト生成まで抱えており SRP/DIP を逸脱。内部のヘルパが 60+ 個あり複雑度高。
- 翻訳・UI 翻訳のリトライ処理 `_run_with_retry` がハンドラ内に実装され、他コンポーネントから再利用不可。レートリミット通知もハンドラ単体管理で横断性なし。
- 言語設定の確認/完了メッセージ生成が `MessageHandler` と `PostbackHandler` で重複（多言語化ロジック、言語上限チェック、完了文言組み立て）。共通サービス化余地。
- サブスク関連処理が `MessageHandler` と `PostbackHandler` に分散し、Repository への `get_subscription_*` 呼び出しが `getattr` フォールバック付きで直接行われており、ポート契約が曖昧（DIP/ISP の不足）。
- インターフェース文言の多言語化が複数メソッドに散在（`_build_multilingual_interface_message` と `_build_multilingual_notice` など）。テンプレート整形・翻訳をサービス化すると DRY 化可能。
- クオータ判定・通知と翻訳実行が同一メソッド `_handle_translation_flow` に結合しており、テストが難しい。課金状態推定（paid/free）もハンドラ側で実装されている。
- `LanguagePreference` 解析後の上限超過や未対応言語レスポンスが都度ハンドラ内で生成され、`_build_language_limit_message` などが分散。設定系のユースケースサービス切り出しが必要。
