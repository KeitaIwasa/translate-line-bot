# Follow 挨拶メッセージ多言語化（フォローイベント）

## 方針
- 英語原文をベースに、言語コードが en / ja / th / zh（地域コード付き含む）の場合は事前用意した定型文を返信する。
- それ以外の言語は Gemini で英語原文を翻訳して返信する。
- language が取得できない場合は、ja → th → zh-TW → en の順で挨拶を送信する。
- ユーザー名は取得できた場合のみ差し込み、LINE プロフィール取得不可時はプレースホルダなしで送る。

## ToDo
- [x] 現状調査：FollowHandler の処理、LinePort/LineApiAdapter のプロフィール取得可否、InterfaceTranslationService の利用方針を確認する。
- [x] 設計：言語コードの判定（地域コード許容、zh の扱い）、挨拶テンプレートの配置・再利用方法、Gemini 翻訳のエラーハンドリング方針を決める。
- [x] 実装：LinePort/LineApiAdapter に言語付きプロフィール取得を追加し、FollowHandler に挨拶選択・翻訳・フォールバック処理を組み込む。Bootstrap の依存注入とテンプレート定数を実装する。
- [x] テスト：FollowHandler のユニットテストを追加（en/ja/th/zh 系、未知言語→Gemini、language なし→多言語送信、翻訳失敗時のフォールバック）。pytest で全体を確認する。
- [ ] デプロイ：pytest 成功後にステージング環境へデプロイし、挨拶送信を実機で確認する。

## 現状調査メモ
- FollowHandler は `DIRECT_GREETING`（英語のみ）を返信するだけで、ユーザー名・言語分岐・Gemini 利用なし。
- LinePort/LineApiAdapter には displayName 取得メソッドはあるが language は未取得。プロフィール取得は `/v2/bot/profile/{userId}`（もしくは group/room member）を呼び出しているが、language をパースしていない。
- Event パースでは follow イベントに `reply_token` と `user_id` をセット。`group_id` は `_resolve_group_id` により userId になるので、プロフィール取得には `user_id` を使う必要がある。
- InterfaceTranslationService があり、英語ベース文を任意の言語コードへ Gemini 翻訳するユーティリティとして流用できる。

## 設計メモ
- プロフィール取得: LinePort に `get_profile(user_id, source_type, container_id)`（戻り: display_name, language）を追加し、LineApiAdapter で language をパース。group/room コンテキストも考慮。
- 言語判定: 受信 language を小文字化して地域コードは `split('-')[0]` でベースを取得。`en/ja/th/zh` が事前文面対象。zh は地域コード不問で同一メッセージ（繁体前提）を使用。
- テンプレート: 英語原文をベースとし、ja / th / zh（繁体）にも固定原文を用意。ユーザー名があれば `{name}, ...` 形式で埋め込み、なければ敬称なしでそのまま使用。
- Gemini 翻訳: 事前対応外の言語は InterfaceTranslationService で英語原文を target_lang に翻訳し、失敗時はフォールバック送信へ切替。
- フォールバック: language 取得不可または翻訳失敗時は ja → th → zh-TW → en の順で最大 4 件を 1 リプライで送信（LINE 5 件制限内）。
- 例外ハンドリング: プロフィール取得失敗・翻訳エラーはログ警告後に処理継続し、必ず何らかの挨拶を返信する。
