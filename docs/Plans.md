## ToDo

[x] グループメンション操作を OpenAI Agent SDK + Tools に一気置換（入力JSON化、tool分岐、errorフォールバック）
[ ] サブスクリプションが停止（キャンセル）されたときに通知を送信
[x] 個人チャットPIIマスク戻り値の型不整合を修正（履歴生成・safe_output_text）
[x] 上記不具合の回帰テストを追加（タプル混入防止）
[x] `pytest` を実行して全件成功を確認
[x] ステージング環境へデプロイして動作確認
[x] プロモーション用サイトページの作成。案内ポータルのデザインを参考に。
[x] 案内ポータルサイトのデフォルトを日本語に&パラメータで言語指定可能に
[x] 個人チャットで使い方などの質問に対して柔軟な応答をできるように
[x] 翻訳停止中（quota到達など）に返信が返らない不具合（`_send_over_quota_message` 参照のAttributeError）を修正
[x] 翻訳停止中に上限通知を出した場合でも limit_notice_plan を更新する
[x] LPで累計利用者数（ユニークユーザー）を表示するAPIを追加
[x] 料金プラン再編（Free/Standard/Pro）向けのドメイン定義を追加
[x] `/checkout` を `mode=status/start` + `st` トークン対応へ更新
[x] Stripe Webhook を price.id ベースで entitlement 同期するよう更新
[x] Pro向けメッセージ暗号化・7日削除ジョブの実装を追加
[x] 料金比較ページ（`/pro.html` 4言語）を新UIに再構成
