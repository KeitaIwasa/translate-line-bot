# ToDo リスト
## アーキテクチャ改善（Lambda 可読性向上）
- [x] Lambda 内のイベント分岐を `app/dispatcher.py` に集約し、イベントごとにユースケースハンドラ（message/postback/join/memberJoined/follow）をクラス分離する（`src_new/` に新構成を追加）
- [x] ドメインモデル（GroupLanguageSetting, MessageRecord, TranslationRequest/Result）を `domain/models` 配下に整理し、整形専用オブジェクトを `presentation/view_models` に切り出す（`src_new/domain`, `src_new/presentation`）
- [x] 外部サービスのインターフェース（LINE, Gemini, Neon）を `domain/ports` で抽象化し、`infra/` に実装を配置、DI コンテナ `app/bootstrap.py` で組み立てる（`src_new/`）
- [x] `lambda_handler.py` を薄いエントリポイントにし、初期化・依存解決とイベントディスパッチのみ行う構造へ移行する（`src_new/lambda_handler.py`）
- [ ] ハンドラ単体テスト用に、LINE Webhook Event の固定フィクスチャとモックポートを追加し、主要ユースケースのユニットテストを整備する
  - 2025-11-26: 上記4項目を `src_new/` で実装（既存 `src/` は無変更）。デプロイに組み込む場合は `template.yaml` / `scripts/deploy.sh` で CodeUri/Handler を調整して新構成を参照させる必要あり。
