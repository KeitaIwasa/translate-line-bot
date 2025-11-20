# DB スキーマ仕様

LINE 多言語翻訳ボットで使用する Neon (PostgreSQL) のデータモデルを定義する。

## 1. 命名規約

- スキーマ: `public`
- テーブル名: スネークケース複数形
- タイムスタンプ: `TIMESTAMPTZ`、デフォルト `NOW()`
- 主キーは単一列または複合キーを明示し、全て `PRIMARY KEY` 制約を付与

## 2. テーブル定義

### 2.1 `group_members`

| 列名 | 型 | NOT NULL | デフォルト | 説明 |
| ---- | --- | -------- | ---------- | ---- |
| `group_id` | TEXT | ✔ |  | LINE グループ ID |
| `user_id` | TEXT | ✔ |  | LINE ユーザー ID |
| `preferred_lang` | VARCHAR(10) |  |  | ISO 639-1 言語コード（未設定可） |
| `created_at` | TIMESTAMPTZ | ✔ | `NOW()` | 登録日時 |
| `updated_at` | TIMESTAMPTZ | ✔ | `NOW()` | 最終更新日時 |

- 主キー: `(group_id, user_id)`
- インデックス:
  - `idx_group_members_user` (`user_id`): 1ユーザーが複数グループに所属する場合の検索最適化。
- トリガー / 更新制御: `updated_at` は更新時に `NOW()` へ自動更新（`ON UPDATE` トリガー or アプリ側設定）。
- `preferred_lang` は未設定状態を許容し、MVP では言語登録前の placeholder として扱う。

```sql
CREATE TABLE group_members (
  group_id TEXT NOT NULL,
  user_id TEXT NOT NULL,
  preferred_lang VARCHAR(10),
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY (group_id, user_id)
);

CREATE INDEX idx_group_members_user
  ON group_members (user_id);
```

### 2.2 `messages`

| 列名 | 型 | NOT NULL | デフォルト | 説明 |
| ---- | --- | -------- | ---------- | ---- |
| `id` | BIGSERIAL | ✔ |  | 連番 PK |
| `group_id` | TEXT | ✔ |  | LINE グループ ID |
| `user_id` | TEXT | ✔ |  | 発言者 ID（bot も含む）|
| `sender_name` | TEXT | ✔ |  | 表示名（翻訳時に使用）|
| `text` | TEXT | ✔ |  | 受信テキスト（メンション含む原文）|
| `timestamp` | TIMESTAMPTZ | ✔ | `NOW()` | 発言時刻（LINE イベントの `timestamp`）|
| `created_at` | TIMESTAMPTZ | ✔ | `NOW()` | 保存日時 |

- 主キー: `id`
- インデックス:
  - `idx_messages_group_time` (`group_id`, `timestamp DESC`)：文脈20件取得用。
  - `idx_messages_group_user_time` (`group_id`, `user_id`, `timestamp DESC`)：ユーザー別参照や bot 除外条件で使用。
- `sender_name` は「発言時点の表示ラベル」をそのまま保存し、後日名前が変わっても過去ログは更新しない。
- 備考: 文脈取得時は `user_id <> :bot_user_id` 条件を付与。

```sql
CREATE TABLE messages (
  id BIGSERIAL PRIMARY KEY,
  group_id TEXT NOT NULL,
  user_id TEXT NOT NULL,
  sender_name TEXT NOT NULL,
  text TEXT NOT NULL,
  timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_messages_group_time
  ON messages (group_id, timestamp DESC);

CREATE INDEX idx_messages_group_user_time
  ON messages (group_id, user_id, timestamp DESC);
```

## 3. リレーション / 外部キー

- `messages.group_id` および `messages.user_id` は `group_members` とは必ずしも 1:1 ではない（bot や未登録ユーザーの発言を許容）。
- 外部キー制約は設定しないが、アプリ層で整合性を担保する。

## 4. マイグレーション指針

1. 新規列は `NULL DEFAULT` で追加し、アプリ改修後に `NOT NULL` 制約へ移行する。
2. 破壊的変更（列削除・型変更）はステージングで検証後、本番へ段階適用。
3. マイグレーションは `prepare_database_migration` → テスト → `complete_database_migration` フローで実施。

## 5. データ保持・削除

- MVP では削除運用を行わず、`messages` は無期限保存。
- 将来の削除ニーズに備え、論理削除用フラグ `deleted_at TIMESTAMPTZ` を追加できる余白を確保（未実装）。

## 6. サンプルクエリ

### 6.1 文脈20件取得（bot 除外）

```sql
SELECT sender_name, text, timestamp
FROM messages
WHERE group_id = $1
  AND user_id <> $2
ORDER BY timestamp DESC
LIMIT 20;
```

### 6.2 言語設定一覧

```sql
SELECT user_id, preferred_lang
FROM group_members
WHERE group_id = $1;
```

### 6.3 言語設定アップサート（追加のみ）

```sql
INSERT INTO group_members (group_id, user_id, preferred_lang)
VALUES ($1, $2, $3)
ON CONFLICT (group_id, user_id)
DO UPDATE SET preferred_lang = EXCLUDED.preferred_lang,
              updated_at = NOW();
```

## 7. 監視対象メトリクス（DB）

- 接続数 / セッション使用率
- スロークエリ（`pg_stat_statements`）
- インデックスヒット率（特に `idx_messages_group_time`）
- ディスク使用量（無期限保存のため定期確認）

以上。
