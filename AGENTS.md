- 英語でthinkして、日本語でoutputして
- `docs/Plans.md` にToDoリストをまとめています。適宜更新してください。
- インターフェース用の文章は、ベース原文を英語で作成して、必要に応じて、グループの設定言語などに翻訳して組み立てるようにしてください。その際、翻訳先が英語の場合は、ベース文をそのまま使用してください。
- Secrets Managerはデプロイ用のprofileでアクセスできます。
- Lambdaのコードの修正を行った後は以下のタスクを行ってから完了してください。
  1. `pytest`を実行して全テストが通ることを確認する
  2. ステージング環境にデプロイ

## コーディングスタイル
- コメントは日本語で書く
- DRY原則「同じ知識・ロジック・責務を重複させるな」
- SOLID原則
  - Single Responsibility Principle
  - Open/Closed Principle
  - Liskov Substitution Principle
  - Interface Segregation Principle
  - Dependency Inversion Principle

## デバッグのポイント
- コードを読んで問題の切り分けを行う
- 必要に応じてCloudWatch Logsでログを確認する
- 必要に応じてNeonのデータを確認する。
- 必要に応じてテストの実行やテストの追加をして、問題の切り分けを行う。
- 問題が特定できない時は、ログ出力を差し込んだ上で、ユーザーにテスト実行をお願いする。