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
| `joined_at` | TIMESTAMPTZ | ✔ | `NOW()` | ボットがグループに参加し、ユーザーを検知した日時 |
| `last_prompted_at` | TIMESTAMPTZ |  |  | 最後に言語設定催促を送った日時（null=未通知） |
| `last_completed_at` | TIMESTAMPTZ |  |  | ユーザーが言語設定を完了した日時（null=未設定） |

- 主キー: `(group_id, user_id)`
- インデックス:
  - `idx_group_members_user` (`user_id`): 1ユーザーが複数グループに所属する場合の検索最適化。
- `last_prompted_at`/`last_completed_at` は join 再発時のリセット用メタデータ。最新ステータスは `group_languages` を参照する。

```sql
CREATE TABLE group_members (
  group_id TEXT NOT NULL,
  user_id TEXT NOT NULL,
  joined_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  last_prompted_at TIMESTAMPTZ,
  last_completed_at TIMESTAMPTZ,
  PRIMARY KEY (group_id, user_id)
);

CREATE INDEX idx_group_members_user
  ON group_members (user_id);
```

### 2.2 `group_languages`

| 列名 | 型 | NOT NULL | デフォルト | 説明 |
| ---- | --- | -------- | ---------- | ---- |
| `group_id` | TEXT | ✔ |  | LINE グループ ID |
| `lang_code` | VARCHAR(16) | ✔ |  | ISO 639-1/2/BCP47 コード |
| `lang_name` | TEXT | ✔ |  | Gemini が返した自然言語表示名（テンプレート表示用）|
| `created_at` | TIMESTAMPTZ | ✔ | `NOW()` | 登録日時 |

- 主キー: `(group_id, lang_code)`
- 外部キーは張らず、アプリ層で整合性を担保。
- グループ単位で翻訳対象言語を共有し、一度設定すれば全メンバーに適用する。

```sql
CREATE TABLE group_languages (
  group_id TEXT NOT NULL,
  lang_code VARCHAR(16) NOT NULL,
  lang_name TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY (group_id, lang_code)
);
```

### 2.3 `messages`

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

### 2.4 `group_settings`

| 列名 | 型 | NOT NULL | デフォルト | 説明 |
| ---- | --- | -------- | ---------- | ---- |
| `group_id` | TEXT | ✔ |  | LINE グループ ID |
| `translation_enabled` | BOOLEAN | ✔ | TRUE | 通訳を稼働させるかのフラグ |
| `updated_at` | TIMESTAMPTZ | ✔ | `NOW()` | 最終更新時刻 |

- 主キー: `group_id`
- ボットメンションによる「翻訳停止/再開」の状態を保持する。

```sql
CREATE TABLE group_settings (
  group_id TEXT PRIMARY KEY,
  translation_enabled BOOLEAN NOT NULL DEFAULT TRUE,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

## 3. リレーション / 外部キー

- `group_languages` はグループ単位で保持し、外部キーは設定しない（Bot 再招待時にアプリ層で削除）。
- `messages.group_id` / `messages.user_id` は `group_members` と必ずしも一致しない（bot や未登録ユーザーの発言を許容）。

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
SELECT array_agg(lang_code ORDER BY lang_code) AS langs
FROM group_languages
WHERE group_id = $1;
```

### 6.3 言語設定登録（完了時）

```sql
INSERT INTO group_languages (group_id, lang_code, lang_name)
SELECT $1, lang_code, lang_name
FROM UNNEST($2::TEXT[], $3::TEXT[]) AS t(lang_code, lang_name)
ON CONFLICT (group_id, lang_code) DO NOTHING;
```

## 7. 監視対象メトリクス（DB）

- 接続数 / セッション使用率
- スロークエリ（`pg_stat_statements`）
- インデックスヒット率（特に `idx_messages_group_time`）
- ディスク使用量（無期限保存のため定期確認）

以上。
