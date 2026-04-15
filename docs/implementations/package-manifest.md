---
author: "Ryo Nakagami"
date-modified: "2026-04-15"
project: skill-tool
---

# Package Manifest (`skill.toml`) 仕様

skilltool が扱う skill package の **正式な manifest** は
`skill.toml` です（task004 で導入）。同じディレクトリにある
`skill.md` の YAML frontmatter もレガシー形式として読めますが、
新規パッケージは `skill.toml` で書いてください。

このドキュメントはフィールドの完全リファレンスです。運用例は
[publishing.md](./publishing.md) を参照。

---

## 1. 配置

```text
my-skill/
├── skill.toml       ← manifest（この仕様書の対象）
├── SKILL.md         ← entry（Agent が実行する本体）
└── …                ← include で拾う補助ファイル
```

- `skill.toml` は **パッケージディレクトリの root** に置くこと
- 1 つのディレクトリには 1 つの `skill.toml` のみ
- サーバ側は archive の root または 1 階層だけ下まで探す
  （実装: `_find_manifest`）

---

## 2. 最小例

```toml
[skill]
name        = "my-skill"
version     = "1.0.0"
description = "Minimal example."
```

これだけで publish 可能。`entry` / `include` / `author` は省略で下記のデフォルトが
適用されます。

- `entry = "SKILL.md"`
- `include = [entry]`（つまり `["SKILL.md"]`）
- `author` は未設定

`skill.toml` と `entry` に指定したファイルは `include` に書かなくても
**必ず zip に入る** ので、最小構成では `SKILL.md` と `skill.toml` の 2
ファイルが published artifact になります。

---

## 3. 完全例

```toml
[skill]
name        = "docx"
version     = "1.2.0"
description = "Author and inspect .docx files from Claude."
author      = "team-doc"
entry       = "SKILL.md"
include = [
    "SKILL.md",
    "scripts/**",
    "templates/*.txt",
    "docs/*.md",
]

# 追加の任意キーは自由に書ける。サーバは知らないキーもそのまま
# sidecar yaml に保存するので、将来のクライアント拡張に使える。
tags     = ["office", "docx", "word"]
homepage = "https://example.com/docx"
```

---

## 4. フィールド一覧

### `[skill]` テーブル

| キー | 型 | 必須 | デフォルト | 説明 |
| --- | --- | --- | --- | --- |
| `name` | string | ✅ | — | パッケージ名。`^[a-z0-9][a-z0-9._-]*$` にマッチすること |
| `version` | string | ✅ | — | SemVer 推奨。`^[A-Za-z0-9][A-Za-z0-9._+-]*$` にマッチすること |
| `description` | string | ✅ | — | 1 行程度の短い説明。search の検索対象 |
| `author` | string | 任意 | — | 慣習的に個人名ではなく "team-doc" 等のチーム名を推奨 |
| `entry` | string | 任意 | `"SKILL.md"` | Agent が読む本体のファイル名（パッケージディレクトリからの相対パス） |
| `include` | array[string] | 任意 | `[entry]` | zip に含めるファイルを選ぶ glob 配列 |
| その他 | any | 任意 | — | 未知のキーは sidecar yaml に保存されるだけ。`tags`, `homepage` 等を追加可 |

### `name` のルール

- 小文字英数 + `.` / `_` / `-`
- 先頭は英数字
- 例: `docx`, `my-skill`, `team-doc.docx`, `docx_v2`

名前衝突時は prefix 付けが現状唯一の緩和策です
（[limitations.md §namespace](./limitations.md#namespace) 参照）。

### `version` のルール

- SemVer (`1.2.3` / `1.2.3-alpha` 等)
- 同一 `name@version` の再 publish は **必ず 409**（上書き禁止）
- server 側 sort は `_version_key()` — 主要部を整数タプルで比較
  し、prerelease は先に来る

### `entry`

- `Agent` が読み込むべきメインファイル。Claude Agent Skill では
  慣例的に `SKILL.md`（大文字）
- ファイルシステムが case-sensitive (Linux) なので、実ファイル名と
  完全一致させること
- zip 時に **自動で include される**。`include` に書かなくてよい

### `include`

zip に含めるファイルを選ぶ **glob パターン配列**。

- 省略 → `[entry]`
- Python の `pathlib.Path.glob` セマンティクス
  - `*` は 1 セグメント、`**` は任意数のセグメント
  - dot file もマッチする（shell 的な挙動ではない）
- `skill.toml` / `entry` は `include` に書かなくても常に含まれる
- hard-excluded path（`__pycache__`, `.git` 等）は include 結果から
  除外される（ゴミ混入防止の safety net）

よく使うパターン:

| パターン | 意味 |
| --- | --- |
| `SKILL.md` | ルート直下の当該ファイル |
| `*.py` | ルート直下の .py のみ |
| `**` | ルートからすべて再帰 |
| `**/*.py` | ネストを含む全 .py |
| `scripts/**` | `scripts/` 配下すべて |
| `scripts/*.py` | `scripts/` 直下の .py のみ |
| `docs/*.md` | `docs/` 直下の .md のみ |

matchの挙動は [tests/unit/test_skill_toml.py](../../tests/unit/test_skill_toml.py)
に網羅されています。

---

## 5. サーバに保存される形

publish 成功時、サーバは `skill.toml` の `[skill]` テーブルに
以下を追記して `<version>.yaml` として保存します。

```yaml
name: docx
version: 1.2.0
description: Author and inspect .docx files from Claude.
author: team-doc
entry: SKILL.md
manifest_format: skill.toml       # サーバが付与
published_by: alice                # サーバが付与
published_at: "2026-04-15T10:23:45Z"   # サーバが付与
published_teams: ["team-doc"]      # ユーザの teams から自動
tags: [office, docx, word]         # 任意キーは保持
homepage: https://example.com/docx
```

`skilltool show <name>` や `GET /api/packages/<name>` でこの yaml の
内容が metadata として返ります。

---

## 6. Legacy `skill.md` frontmatter 形式

後方互換のため、`skill.md` または `SKILL.md` の YAML frontmatter も
manifest として読めます。

```markdown
---
name: legacy-skill
version: 1.0.0
description: Old-style.
author: team-doc
---

# legacy-skill
...
```

**相違点** (skill.toml と比較):

- `entry` / `include` の概念なし → 全ファイルを zip に入れる
  （`_should_include` の hard exclude のみ適用）
- 未知キーも preserve するが、glob を使えないため柔軟性に欠ける

移行手順は
[publishing.md §8](./publishing.md#8-レガシー-skillmd-フロントマター形式)。

---

## 7. Precedence ルール (skill.toml ↔ skill.md frontmatter)

`skill.toml` が **ある** 場合:

| ケース | 振る舞い |
| --- | --- |
| `skill.md` に frontmatter が無い | **問題なし**。narrative markdown として扱い、一切パースしない |
| `skill.md` に frontmatter はあるが skill.toml と矛盾する値 | **skill.toml 側が常に採用**。skill.md の frontmatter は無視される |
| `skill.md` の frontmatter が文法的に壊れている | **無害**。そもそも読みに行かないので publish は成功する |
| `skill.md` が存在しない（`entry` を別のファイルに向けている 等） | **問題なし**。entry 指定に従う |

`skill.toml` が **無い** 場合（レガシー運用）:

| ケース | 振る舞い |
| --- | --- |
| `skill.md` (または `SKILL.md`) に valid frontmatter | 従来どおり frontmatter が manifest として扱われる |
| どちらも存在しない / frontmatter 無し | **400**: `no manifest found` |

実装上は、クライアント (`read_skill_manifest`) もサーバ
(`extract_skill_metadata`) も **`skill.toml` が見つかった時点で
return** する構造になっているため、skill.md 側の frontmatter は
そもそもロードすらされません。「skill.toml が優先される」では
なく「skill.toml がある時点で skill.md の frontmatter は存在しない
のと同じに扱う」のが正確な記述です。

この不変条件は以下のテストで担保しています:

- ユニット: [tests/unit/test_skill_toml.py](../../tests/unit/test_skill_toml.py)
  - `test_skill_toml_present_means_no_frontmatter_needed`
  - `test_skill_toml_present_with_broken_frontmatter_on_skill_md`
  - `test_skill_toml_overrides_all_conflicting_skill_md_fields`
- 統合: [tests/integration/test_skill_toml_publish.py](../../tests/integration/test_skill_toml_publish.py)
  - `test_publish_skill_toml_with_bare_skill_md_no_frontmatter`
  - `test_publish_skill_toml_with_broken_frontmatter_on_skill_md`
  - `test_publish_skill_toml_conflicting_fields_all_resolve_to_toml`

---

## 8. バリデーションの発生タイミング

| タイミング | 何を見るか | どこでエラーになるか |
| --- | --- | --- |
| `skilltool publish` 実行時 | `skill.toml` の文法・必須フィールド | client: `zip_skill_directory` が `ValueError` を送出、zip 生成前に fail fast |
| サーバ受信時 | 再度 `[skill]` を読み、`name` / `version` の正規表現、`include` の型 | 400 を返して保存しない |
| 保存直前 | 既存 `<version>.zip` との衝突 | 409 Conflict |
| 保存直後 | なし（append-only） | — |

---

## 9. 参考実装

- [client/src/skilltool/commands.py](../../client/src/skilltool/commands.py) —
  `SkillMetadata.from_skill_toml`, `expand_include`, `zip_skill_directory`
- [registry/server.py](../../registry/server.py) — `_parse_skill_toml`,
  `extract_skill_metadata`
- [tests/unit/test_skill_toml.py](../../tests/unit/test_skill_toml.py) —
  parser + glob ユニットテスト
- [tests/integration/test_skill_toml_publish.py](../../tests/integration/test_skill_toml_publish.py)
  — サーバ側 publish 統合テスト
