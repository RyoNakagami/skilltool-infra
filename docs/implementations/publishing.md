---
author: "Ryo Nakagami"
date-modified: "2026-04-15"
project: skill-tool
---

# Publishing ガイド

スキル開発者向けに、`skilltool publish` で registry に package を上げる
までの手順を示します。`skill.toml` のフィールド詳細は
[package-manifest.md](./package-manifest.md) を、登録後に何ができる
かは [README](../../README.md) を参照してください。

---

## 1. 前提

- クライアント (`skilltool`) が `uv tool` でインストール済み
- 有効な **per-user token** を持っている（サーバ管理者に
  `setup/server/add-user.sh` を叩いてもらう）
- token を環境変数 or `~/.config/skilltool/config.toml` に設定済み

```bash
skilltool config
# transport    http/ssh                     (file or env)
# registry     http://100.x.x.x:8765        (file or env)
# token        tok_…                        (file or env)    ← これが (unset) だと publish できない
```

---

## 2. 最小のスキル

ディレクトリ 1 つに `skill.toml` と `SKILL.md` があれば publish できます。

```text
my-first-skill/
├── skill.toml
└── SKILL.md
```

`skill.toml`:

```toml
[skill]
name        = "my-first-skill"
version     = "0.1.0"
description = "Example skill for smoke-testing the registry."
author      = "team-doc"
# entry と include は省略可能。デフォルトは entry = "SKILL.md"、
# include = [entry] なので、この 2 ファイルだけが zip に入る。
```

`SKILL.md`（Claude Agent Skill の本体。自由記述）:

```markdown
# my-first-skill

このスキルは…
```

これで publish:

```bash
skilltool publish ./my-first-skill/
# ✓ published my-first-skill 0.1.0
#   by alice  2026-04-15T10:23:45Z
```

---

## 3. 追加ファイルを含める

`include` に glob を並べると、`SKILL.md` 以外のファイルも zip に載せられます。
`skill.toml` と `entry` で指定した file は **常に自動で含まれる** ので、
`include` には *追加で* 含めたいものだけを書けば OK です。

```text
my-skill/
├── skill.toml        ← 常に含まれる
├── SKILL.md          ← include に書かなくても entry なので含まれる
├── scripts/
│   ├── helper.py     ← include = ["scripts/**"] なら含まれる
│   └── tests/
│       └── test_helper.py   ← 同上（ネストも拾う）
├── templates/
│   └── body.txt      ← include に入れなければ含まれない
├── tests/            ← 含まれない
├── .git/             ← そもそも含まれない（hard exclude）
└── README_dev.md     ← 含まれない
```

```toml
[skill]
name        = "my-skill"
version     = "1.2.0"
description = "Does something useful."
author      = "team-infra"
entry       = "SKILL.md"
include = [
    "SKILL.md",
    "scripts/**",
    "templates/*.txt",
]
```

### glob のセマンティクス

| パターン | 意味 |
|---|---|
| `SKILL.md` | ルート直下の当該ファイル |
| `*.py` | ルート直下の .py のみ |
| `**/*.py` | ネストを含むすべての .py |
| `scripts/**` | `scripts/` 配下すべて（再帰） |
| `scripts/*.py` | `scripts/` 直下の .py のみ |

詳細は [package-manifest.md §include](./package-manifest.md#include) を参照。

### Hard exclude（include の結果に関係なく除外されるもの）

安全網として、glob が拾ってしまっても以下は必ず zip から除外されます。

- ディレクトリ: `__pycache__`, `.git`, `.venv`, `node_modules`,
  `.mypy_cache`, `.pytest_cache`, `.ruff_cache`
- 拡張子: `*.pyc`, `*.pyo`, `*.swp`

（実装: [commands.py の `_EXCLUDED_*`](../../client/src/skilltool/commands.py)）

---

## 4. Publish コマンド

```bash
skilltool publish ./my-skill/            # ディレクトリを渡す（推奨）
skilltool publish ./my-skill.zip         # すでに zip を作ってある場合
skilltool publish ./my-skill/ --token tok_... # token を 1 回だけ上書き
```

実行すると client 側で:

1. `skill.toml` を parse（無ければ `skill.md` の frontmatter にフォールバック）
2. `skill.toml` があれば `include` + `entry` + `skill.toml` のみを zip
3. サーバへ `POST /api/publish`（HTTP transport）または
   `ssh … skilltool-server publish`（SSH transport）

サーバ側では:

1. token を `users.toml` と照合 → 有効なら caller を解決
2. manifest から `name` / `version` を確定
3. 既に `<name>/<version>.zip` があれば **409 Conflict** で拒否（上書き禁止）
4. 無ければ `registry/data/packages/<name>/<version>.zip` + `.yaml` を書き
5. `publish.log` に 1 行追記
6. `{name, version, published_by, published_at}` を返す

---

## 5. 確認

### CLI

```bash
skilltool show my-skill
#  name:     my-skill
#  latest:   0.1.0
#  author:   team-doc
#  versions: 0.1.0

skilltool search skill                    # 全文検索（name + description の regex）
```

### サーバ上

```bash
# パッケージのバージョン一覧
ls /srv/skilltool/skilltool-infra/registry/data/packages/my-skill/

# メタデータ sidecar を覗く
cat /srv/skilltool/skilltool-infra/registry/data/packages/my-skill/0.1.0.yaml
#   name: my-skill
#   version: 0.1.0
#   description: Example ...
#   author: team-doc
#   entry: SKILL.md
#   manifest_format: skill.toml
#   published_by: alice
#   published_at: "2026-04-15T10:23:45Z"
```

### ブラウザ

`http://100.x.x.x:8765/packages/my-skill` を開くと version 一覧 +
zip ダウンロード直リンクが見えます。

### 監査ログ

```bash
curl -H "Authorization: Bearer $SKILLTOOL_TOKEN" \
     "$SKILLTOOL_REGISTRY/api/audit?limit=20"
# {"entries":[{"raw":"2026-04-15T10:23:45Z  alice   my-skill  0.1.0 (new)", ...}], ...}
```

---

## 6. 再 publish（バージョンアップ）

**同じ `name@version` は 409 で拒否** されるため、publish のたびに
`skill.toml` の `version` を上げます。SemVer に従うこと
（[VERSIONING_POLICY.md](../development-rules/VERSIONING_POLICY.md)）。

```bash
sed -i 's/^version = "0.1.0"$/version = "0.2.0"/' skill.toml
skilltool publish ./my-skill/
# ✓ published my-skill 0.2.0
# audit log: my-skill  0.1.0 → 0.2.0
```

---

## 7. 他人が作ったパッケージに version を足す

現状 **package ownership はありません**。有効な token を持っていれば
誰でも既存パッケージの新 version を追加できます（`published_by` で
version 単位の責任者は記録される）。

```bash
skilltool install docx                    # 現行 latest を手元に
cd docx
# 編集…
sed -i 's/^version = "0.1.0"$/version = "0.2.0"/' skill.toml
cd ..
skilltool publish ./docx/
```

将来のチーム ACL 拡張は [limitations.md](./limitations.md#ownership) に
メモしています。

---

## 8. レガシー: `skill.md` フロントマター形式

task004 より前に作ったパッケージは `skill.md` の YAML frontmatter で
メタデータを持っていました。互換のため **現在も publish 可能** ですが、
新規は `skill.toml` を推奨します。

```markdown
---
name: legacy-skill
version: 1.0.0
description: Old-style package.
author: team-doc
---

# legacy-skill
...
```

このディレクトリには `skill.toml` を置かないこと。両方あると
`skill.toml` が優先され、`skill.md` のメタデータは無視されます。
レガシーパスでは `include` / `entry` 概念がないため、`_should_include`
の除外ルールを満たす全ファイルが zip に入ります（つまりテストや
README も入ってしまう）。**新規作成時に skill.toml を使う強い動機** は
ここにあります。

### 移行

1. ディレクトリに `skill.toml` を作る（フィールドは skill.md の
   frontmatter と同じで OK）
2. `skill.md` を `SKILL.md` にリネームするか、そのままでも可
3. `entry` / `include` を書いて不要ファイルを出さないようにする
4. `skill.md` のフロントマターは消してよい（`skill.toml` 優先のため
   無視される。残していても実害はない）
5. `skilltool publish` で version を上げて再公開

---

## 9. 詰まりどころ早見表

| エラー | 原因 | 対応 |
|---|---|---|
| `missing bearer token` (401) | `SKILLTOOL_TOKEN` 未設定 | `skilltool config` で (unset) ならば設定 |
| `invalid or revoked token` (401) | 無効・失効・typo | 管理者に再発行を依頼 |
| `no manifest found` (400) | ルートに `skill.toml` / `skill.md` 無し | どちらかを配置 |
| `skill.toml: missing [skill] table` (400) | TOML の section が違う | `[skill]` を追加 |
| `skill.<field> is required` (400) | 必須フィールド欠落 | `name` / `version` / `description` を揃える |
| `entry 'SKILL.md' does not exist` | `entry` に書いたファイルが無い | 実際の filename に合わせる（大文字小文字を含めて） |
| `… already exists; publish a new version` (409) | 同じ version の再 push | `version` を上げる |
| `invalid package name` (400) | `^[a-z0-9][a-z0-9._-]*$` に合わない | 英小文字 + 数字 + `.`/`_`/`-` のみ |

---

## 10. 参考

- [package-manifest.md](./package-manifest.md) — `skill.toml` の完全仕様
- [limitations.md](./limitations.md) — 現状の制限と将来の拡張ポイント
- [architecture.md](./architecture.md) — 全体設計
- [../transport.md](../transport.md) — HTTP / SSH 切り替え
