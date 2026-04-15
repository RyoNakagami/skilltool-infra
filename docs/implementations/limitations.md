---
author: "Ryo Nakagami"
date-modified: "2026-04-15"
project: skill-tool
---

# 現状の制限と設計上の割り切り

skilltool は **社内規模 (< 数十人, < 数百パッケージ) の Tailscale 閉域**
を想定した MVP です。そのため、一般公開 registry が備える機能の多くを
**意図的に持たない** 状態で運用しています。この文書は「今なにができ
ないか」と「なぜそう決めたか」「必要になった場合どう拡張するか」を
集約します。

以下「実装優先度」は現時点の開発者視点の見込みです。実運用で要求が
上がってきたらタスクに切り出して実装します。

---

## 1. 認可 (authorization) {#ownership}

### 1.1 Package ownership がない

**現状**: `users.toml` に有効な token を持つ任意のユーザが、任意の
package の任意の version を publish できる。

**理由**: task002 時点では監査ログ (`published_by` / `publish.log`)
で誰が何をしたかを追跡できれば十分、という判断。事前制御より
事後監査に倒した設計。

**影響**:

- 同名 package への意図しない上書き（version を変えれば）
- 悪意のあるユーザによる squatting は可能だが、user 自体を revoke
  すれば連鎖を止められる

**拡張パス**: `sidecar.yaml` の `published_teams` と publisher の
`teams` を intersect するブロックを `publish_logic` に 1 箇所追加
するだけで、「チーム単位 ACL」が実装できます。
詳細は [architecture.md §10](./architecture.md#10-拡張ポイント)。

**実装優先度**: 中（複数チーム運用が始まったら）

### 1.2 名前空間 (namespace) が無い {#namespace}

**現状**: `name` は flat。`team-a/docx` のような slash namespace は
不可（`_NAME_RE = ^[a-z0-9][a-z0-9._-]*$` に slash なし）。

**緩和策**: prefix 運用。`team-doc.docx`, `docx-wordtools` のように
付けるのが現状の慣例。

**拡張パス**: `_NAME_RE` に `/` を許可し、`PACKAGES_DIR` を 2 階層に
するだけだが、HTML URL や CLI 引数との整合を取る必要がある。

**実装優先度**: 低

### 1.3 読み取り系エンドポイントに認証が無い

**現状**: `GET /` / `GET /packages/<name>` / `GET /api/packages/…`
などはトークン不要。

**理由**: Tailscale を perimeter と見做しているため。Tailnet 内の
任意のノードから browse 可。

**影響**: Tailscale ACL で制限されない限り、同 tailnet の全ノードが
package 一覧・中身を取得可能。機微な内容を置かないこと。

**拡張パス**: `GET /api/packages/…` にも `Depends(_authenticate)` を
追加するのが最小変更。HTML 画面も Basic Auth でガードするなら
middleware 1 つ。

**実装優先度**: 中（機密 skill を扱い始めたら）

---

## 2. パッケージ操作

### 2.1 Delete / Yank が無い

**現状**: 一度 publish した zip / sidecar yaml を削除する API は無い。
`registry/data/packages/` を直接 rm する以外に取り消し不能。

**理由**:

- append-only な保管は監査の基本方針と整合
- 誤 publish よりも、誤削除で履歴が飛ぶほうが害が大きい、という判断

**対処法**: 問題のある version が出たら

1. 新しい version を publish して差し替える（`skilltool install` は
   最新版を取りに行くのでユーザには透過的）
2. どうしても消したい場合は管理者が Server A 上で当該 zip + yaml を
   手で削除

**拡張パス**: PyPI 同様の **yank** 概念（物理削除せず「install
対象から外す」フラグ）を `.yaml` sidecar に足すのが安全。
`yanked: true` を `list_versions` でフィルタすれば良い。

**実装優先度**: 中（誤 publish 1 件でも起きれば上がる）

### 2.2 Package 名の rename が無い

**現状**: `docx` → `docx-pro` のような rename はできない。旧名で
publish を止めるしか手段がない。

**拡張パス**: alias 機構（`<old>/alias.yaml` に `redirect_to: <new>`）
を入れて、`/api/packages/<old>/download` でリダイレクトする。

**実装優先度**: 低

### 2.3 Dependency 解決が無い

**現状**: `skill.toml` に `[dependencies]` のような機構は無い。
ある skill が別の skill を前提にする場合、ユーザが手動で両方を
install する。

**理由**: `skill.md` の世界では skill 間依存はあまり発生しない想定
（agent runtime が横断的にスキルを読むため）。

**拡張パス**: `skill.toml` の `[skill.dependencies]` を追加し、
`skilltool install` が再帰的に install する。semver range を扱う
場合は `packaging.version` の使用が妥当。

**実装優先度**: 低

### 2.4 Version range での install が無い

**現状**: `skilltool install docx` は常に latest、`--version 1.2.0`
で pin のみ。`>=1.2,<2.0` のような range 指定は不可。

**拡張パス**: 上の dependency と同時にやると整合。

**実装優先度**: 低

---

## 3. 署名 / 整合性

### 3.1 Package の署名 / sigstore 連携が無い

**現状**: publish 時に zip のハッシュを記録すらしていない。ネットワーク
改ざんは Tailscale の WireGuard が防ぐという前提。

**拡張パス**: sidecar yaml に `sha256` を記録し、`skilltool install`
時に検証。さらに sigstore との連携で cosign ベースの署名を足せる。

**実装優先度**: 低（Tailscale 信頼前提が崩れない限り）

### 3.2 Sidecar yaml の tamper-evident 保護が無い

**現状**: root ユーザで sidecar yaml を書き換えれば `published_by`
も偽装できる。

**拡張パス**: audit log と sidecar のハッシュを突き合わせる軽量な
verify を追加。あるいは storage を WORM に。

**実装優先度**: 低

---

## 4. 運用機能

### 4.1 Token rotation が手動

**現状**: `revoke-user.sh` → `add-user.sh` の 2 段。同じ username で
再発行するには `users.toml` を手で編集する必要がある。

**拡張パス**: `rotate-user.sh` を足して「古 token を disable しつつ
新 token を同じ名前で発行」を 1 コマンドに。ただし、旧 token で書か
れた publish 履歴と紐づけるため、"alice_v1" のように epoch suffix を
付ける運用も有効。

**実装優先度**: 中

### 4.2 Quotas / Rate limiting が無い

**現状**: 1 ユーザが暴走 publish してもブロックされない。zip サイズ
上限も無い（ただし SSH transport は argv 経由なので実質数 MB が上限）。

**拡張パス**: nginx/uvicorn ベースの rate limit。publish だけ特別に
`slowapi` で絞るのが簡単。

**実装優先度**: 低

### 4.3 users.toml のホットリロード

**現状**: サーバは **毎リクエストで `tomllib.load`** する。性能を
犠牲にした素朴実装だが、「revoke 即反映」を得るため意図的にキャッシュ
を持っていない。

**影響**: publish QPS が 数十/sec を超えると I/O がボトルネック化
し得る。現状の想定 (数/min) では非問題。

**拡張パス**: `inotify` / `watchdog` でファイル変更を検知してキャッ
シュ更新。失効伝播の保証を保つなら mtime ベースで安全に invalidate
可能。

**実装優先度**: 低

### 4.4 バックアップ自動化が無い

**現状**: `/srv/skilltool/skilltool-infra/registry/data/` を定期
バックアップするジョブはリポジトリには含まれない。

**拡張パス**: systemd timer + `rsync` / `restic`。audit log が
append-only なので増分バックアップと相性が良い。

**実装優先度**: 中（運用開始したら早めに）

---

## 5. 検索 / UI

### 5.1 検索は regex のみ

**現状**: `GET /api/search` は以下のフィールド別 regex をサポート
（case-insensitive、複数指定で AND）:

- `q` — name + description（後方互換）
- `name`
- `tag`
- `description`

tokenization も fuzzy matching も無い。

**拡張パス**: `entry` / `author` もマッチ対象に / inverted index 化 /
FAISS 等で意味検索。

**実装優先度**: 低

### 5.2 Web UI が read-only

**現状**: `/` は Name / Tag / Description の検索フォーム付き + Tags
列 / `/packages/<name>` は version ごとの published_at + Tags 表示、
まで実装済み。ただし publish は CLI のみ、package 管理操作（yank、
rename 等）も UI には無い。

**拡張パス**: Web publish 受付は CSRF・ファイル検証等で複雑さが増す
ので優先度は低いまま。検索結果の URL がそのまま共有できるという
利点は現状でも得られている。

**実装優先度**: 低

---

## 6. トランスポート境界

### 6.1 SSH transport の publish は argv ベース

**現状**: zip を base64 化して `skilltool-server publish --data <b64>`
の argv に載せている。Linux の `ARG_MAX` は 128KB〜2MB 程度なので、
**巨大 skill を SSH 経由で publish すると失敗** する可能性がある。

**緩和策**: `--data -` で stdin から bytes を読む機能が server_cli に
実装済み（[server_cli.py](../../registry/server_cli.py) の
`_verb_publish`）。client 側で argv を避けて stdin に切り替える
オプションを足せば解消。

**実装優先度**: 中（5MB 超のスキルが現れたら）

### 6.2 ProxyJump での 2 段 SSH は手動設定

**現状**: `~/.ssh/config` に `ProxyJump` を書くのはユーザの責務。
skilltool config にはホップの概念が無い。

**拡張パス**: `ssh_proxy_jump` フィールドを `config.toml` に足し、
SshTransport の ssh argv に `-J <host>` を差し込む。

**実装優先度**: 低（ssh_config の方が柔軟かつ他ツールとも共通）

### 6.3 `StrictHostKeyChecking=accept-new`

**現状**: 初回接続時に host key を自動受諾する。

**影響**: 初回だけは MITM の可能性を受け入れている。以後は `known_hosts`
に保存されて改ざん検知が効く。

**緩和策**: 本番では `known_hosts` を事前に配布し、`accept-new` を
`yes`（厳格）に切り替えるのが望ましい。

---

## 7. 並行性 / 整合性

### 7.1 Publish は非 atomic

**現状**: `publish_logic` は zip → sidecar yaml → audit log の順に
**3 ステップで個別に書き込む**。途中でサーバがクラッシュすると不整合
（zip はあるが sidecar yaml 無し、等）が残り得る。

**頻度**: 現実的にはまず起きないが、コンテナ強制停止 × publish 最中
のタイミングで理論上起きる。

**拡張パス**:

- sidecar yaml を先に書き、zip を `.zip.partial` → rename する
  atomic-write
- audit log は publish 成功後にのみ書く（既に守られている）
- 復旧スクリプトで孤児を掃除する `scripts/reconcile.py`

**実装優先度**: 低

### 7.2 同一 version の並行 publish

**現状**: ファイル存在チェック → 書き込み の間に race がある。
ほぼ同時の 2 クライアントが同じ `<version>.zip` を書くと、両方が
成功扱いになり片方が勝つ。

**緩和策**: `users.toml` + 運用で並行 publish が起きない前提。

**拡張パス**: `fcntl.flock` で per-package のディレクトリロック。

**実装優先度**: 低

---

## 8. テスト / CI

### 8.1 E2E で実 SSH を通していない

**現状**: `tests/e2e/test_ssh_flow.py` は `SKILLTOOL_SSH_COMMAND` で
`ssh` コマンドを `python3 server_cli.py` に差し替えている。

**影響**: ssh のオプション組み立て（BatchMode, ConnectTimeout 等）
は unit test の argv 検証でしか担保されていない。

**拡張パス**: CI に `sshd` コンテナを立てて真の SSH hop を通す E2E
job を追加。

**実装優先度**: 低

### 8.2 カバレッジ計測が optional

**現状**: `pytest-cov` は dev deps にあるが CI で閾値を強制していない。

**拡張パス**: `pytest --cov=skilltool --cov=registry --cov-fail-under=85`
を CI に入れる。

**実装優先度**: 中

---

## 9. 観測性 / ロギング

### 9.1 構造化ログが無い

**現状**: uvicorn のアクセスログが stdout に流れるのみ。JSON ログや
trace id は無い。

**拡張パス**: `logging` + `python-json-logger`、または OpenTelemetry
連携。

**実装優先度**: 低

### 9.2 メトリクスエンドポイントが無い

**現状**: publish 数、エラー率、ユーザ別 publish 頻度などは
`publish.log` を grep する以外に集計手段が無い。

**拡張パス**: `prometheus_client` で `/metrics` を追加。publish
回数・エラー分布等を expose。

**実装優先度**: 低

---

## 10. サマリー

| 分類 | 主な欠落 | 現状の緩和 | 優先度 |
| --- | --- | --- | --- |
| 認可 | package ACL, namespace, 読み取り認証 | audit, prefix 運用, Tailscale perimeter | 中 / 低 / 中 |
| 操作 | delete/yank, rename, deps | 版上げ差し替え, 手動 rm | 中 / 低 / 低 |
| 整合性 | 署名, tamper 検出 | Tailscale 暗号化 | 低 |
| 運用 | rotate, quota, hot reload, backup | 手作業, 低 QPS 想定 | 中 / 低 / 低 / 中 |
| 検索 | regex のみ, tokenize 無し | per-field 検索 + Tags 列 + 検索フォーム実装済 | 低 |
| Transport | argv 上限, manual ProxyJump | stdin stub あり, ssh_config 運用 | 中 / 低 |
| 並行性 | 非 atomic publish, race | 運用で回避 | 低 |
| テスト/CI | 実 SSH 非検証, カバレッジ未強制 | mock で代替 | 低 / 中 |
| 観測性 | 構造化ログ, metrics 無し | publish.log で代替 | 低 |

いずれも「運用実績が上がってから、ピンポイントで足す」方針です。
大きな破壊的変更を入れずに継ぎ足せるよう設計を保っています
（[architecture.md §10](./architecture.md#10-拡張ポイント)）。
