# skilltool-infra

A PyPI-like registry for `skill.md` packages (Claude Agent Skills) that runs
on a Tailscale-connected server and is driven by a `skilltool` CLI installed
via `uv tool`.

| Component     | What it is                                                                          | Lives in                                                                       |
| ------------- | ----------------------------------------------------------------------------------- | ------------------------------------------------------------------------------ |
| Registry      | FastAPI + Docker image serving a PyPI-style API and browser UI                      | [registry/](registry/)                                                         |
| Server CLI    | SSH entry point (`skilltool-server <verb>`), shares core logic with the HTTP server | [registry/server_cli.py](registry/server_cli.py)                               |
| Client CLI    | `skilltool install / search / show / list / publish / config`, built on Typer       | [client/](client/)                                                             |
| Setup scripts | Bootstrap, user/token management, SSH key registration                              | [setup/](setup/)                                                               |
| Deploy        | GitHub Actions workflow pushing to Server A over Tailscale SSH                      | [.github/workflows/deploy-registry.yml](.github/workflows/deploy-registry.yml) |

## Documentation

- [docs/implementations/architecture.md](docs/implementations/architecture.md)
  — システム全体の設計と責務分担
- [docs/implementations/publishing.md](docs/implementations/publishing.md)
  — publish の手順と skill.toml の使い方
- [docs/implementations/package-manifest.md](docs/implementations/package-manifest.md)
  — `skill.toml` の完全仕様
- [docs/implementations/limitations.md](docs/implementations/limitations.md)
  — 現状の制限と将来の拡張ポイント
- [docs/transport.md](docs/transport.md) — HTTP / SSH トランスポート切り替え
- [docs/implementations/repository-architecture.md](docs/implementations/repository-architecture.md)
  — ディレクトリツリー
- [docs/development-rules/BRANCH_STRATEGY.md](docs/development-rules/BRANCH_STRATEGY.md)
  — ブランチ戦略
- [docs/development-rules/VERSIONING_POLICY.md](docs/development-rules/VERSIONING_POLICY.md)
  — バージョニング方針

## Quick start (development)

手元でとりあえず動かす最短パス。本番デプロイは次節。

```bash
git clone https://github.com/RyoNakagami/skilltool-infra.git
cd skilltool-infra
cp registry/.env.example registry/.env
./setup/server/install.sh                 # docker compose up -d --build

# publish できるユーザを 1 人以上登録する
./setup/server/add-user.sh alice team-doc,team-infra
```

出力された `tok_alice_...` を安全な経路（Slack DM / 1Password 等）で
本人に渡すこと。

## Production deployment (Server A)

本番運用は **`skilltool` サービスユーザ × `/srv/skilltool` × systemd** を
推奨します。

### レイアウト

```text
/srv/skilltool/                        skilltool:skilltool 0750
├── skilltool-infra/                   ← this repo
│   └── registry/data/                 ← bind mount (persistent)
└── .ssh/authorized_keys               ← SSH transport を使う場合
```

### Bootstrap

```bash
# root で 1 度だけ
sudo useradd --system --create-home \
    --home-dir /srv/skilltool --shell /bin/bash skilltool
sudo usermod -aG docker skilltool
sudo install -d -o skilltool -g skilltool -m 0750 /srv/skilltool

# 以降は skilltool ユーザで
sudo -iu skilltool
git clone https://github.com/RyoNakagami/skilltool-infra.git
cd skilltool-infra
cp registry/.env.example registry/.env

# bind mount を skilltool UID で書かせる (これを忘れると root 所有になる)
echo "PUID=$(id -u)" >> registry/.env
echo "PGID=$(id -g)" >> registry/.env

./setup/server/install.sh
./setup/server/add-user.sh alice team-doc,team-infra
```

> **Note**: `usermod -aG docker` を実行した直後は、既存シェルに
> 新グループが反映されていません。`exit` → `sudo -iu skilltool` で
> 張り直すか `newgrp docker` を使ってください。

### systemd で永続化

```bash
UNIT_SRC=/srv/skilltool/skilltool-infra/setup/server/systemd/skilltool.service
sudo install -m 0644 "$UNIT_SRC" /etc/systemd/system/skilltool.service
sudo systemctl daemon-reload
sudo systemctl enable --now skilltool.service
sudo systemctl status skilltool.service
```

unit の中身は [setup/server/systemd/skilltool.service](setup/server/systemd/skilltool.service)。

### SSH transport 併用 (optional)

HTTP 以外に SSH でも叩けるようにする場合:

```bash
./setup/server/install.sh --with-ssh
./setup/server/add-ssh-key.sh alice /tmp/alice.pub
```

- `/usr/local/bin/skilltool-server` が `registry/server_cli.py` への
  symlink になる
- `/home/skilltool/.ssh/authorized_keys` に公開鍵が登録される
  （`/srv/skilltool/.ssh/authorized_keys` に揃えたい場合は
  [architecture.md](docs/implementations/architecture.md) §8.3 参照）

## User management

認証は **per-user token** です。台帳は `registry/data/users.toml`
（git 管理外）で、スキーマは
[registry/users.example.toml](registry/users.example.toml) を参照。
**サーバは毎リクエストで読み直す** ので、追加・失効は即時反映されます。

```bash
# 発行 — stdout に表示される token は 1 度限り。控え忘れたら再発行
./setup/server/add-user.sh <username> [team1,team2,...]

# 失効 — disabled = true を追記、即時反映
./setup/server/revoke-user.sh <username>

# 権限付きで SSH 公開鍵を追加（SSH transport 用）
./setup/server/add-ssh-key.sh <username> path/to/pubkey.pub
```

Token 共有は **Slack DM / 1Password** 等のセキュアなチャンネルで。
メール・Issue コメント・公開チャンネルは避けること。

### 監査

publish のたびに `registry/data/publish.log` に追記され、
`GET /api/audit` でも参照できます。

```bash
# Server A 上で
tail -f /srv/skilltool/skilltool-infra/registry/data/publish.log

# API 経由
curl -H "Authorization: Bearer $SKILLTOOL_TOKEN" \
     "http://100.x.x.x:8765/api/audit?limit=100"
```

publish した成果物には `published_by` / `published_at` が自動で付与され、
`skilltool show <name>` や registry の browser UI から確認できます。

## Client setup

```bash
uv tool install git+https://github.com/RyoNakagami/skilltool-infra.git#subdirectory=client

# 開発中のローカル checkout から入れる場合
uv tool install --editable ./skilltool-infra/client
```

### Configuration

優先順位: 環境変数 > `~/.config/skilltool/config.toml` > localhost 自動検出
> デフォルト。詳細は [config.py](client/src/skilltool/config.py) と
[docs/transport.md](docs/transport.md)。

```toml
# ~/.config/skilltool/config.toml

# 共通
token    = "tok_alice_..."

# HTTP transport (default)
transport = "http"
registry  = "http://100.x.x.x:8765"

# もしくは SSH transport
# transport = "ssh"
# ssh_host  = "100.x.x.x"
# ssh_user  = "skilltool"
```

または環境変数:

```bash
export SKILLTOOL_TOKEN=tok_alice_...
export SKILLTOOL_REGISTRY=http://100.x.x.x:8765         # HTTP
# export SKILLTOOL_TRANSPORT=ssh
# export SKILLTOOL_SSH_HOST=100.x.x.x
# export SKILLTOOL_SSH_USER=skilltool
```

解決結果を確認:

```bash
skilltool config
```

### Transport の選択指針

| 条件 | 推奨 transport |
| --- | --- |
| Tailscale で直接 `:8765` に届く | `http` (default) |
| ブラウザで UI を見たい | `http` |
| HTTP が閉じている / 踏み台 SSH しかない | `ssh` |
| CI から publish したい | どちらでも。鍵配備が楽な `ssh` がおすすめ |

切り替えの詳細は [docs/transport.md](docs/transport.md) を参照。

### 2 段 SSH (ProxyJump) で届ける

`client → jdscmac → server` のように踏み台を挟む場合は、`~/.ssh/config`
の `ProxyJump` で透過的に解決できます（skilltool 側の設定は変えなくて
よい）。

```sshconfig
Host jdscmac
    HostName jdscmac.tailxxxx.ts.net
    User myname
    ControlMaster auto
    ControlPath ~/.ssh/cm-%r@%h:%p
    ControlPersist 10m

Host skilltool-server
    HostName 10.0.0.5
    User skilltool
    IdentityFile ~/.ssh/id_ed25519_skilltool
    ProxyJump jdscmac
```

クライアントは `SKILLTOOL_SSH_HOST=skilltool-server` にするだけで、
jdscmac 経由でも直接経路でも動きます。token は中継ホストに漏れません
（ProxyJump は encrypted tunnel のみ）。

## Commands

```bash
skilltool install docx
skilltool install docx --dest ./skills
skilltool install docx --version 1.2.0
skilltool search "word|pdf"
skilltool show docx
skilltool list
skilltool publish ./my-skill/
skilltool publish ./my-skill.zip --token mytoken
skilltool config
```

## Publishing

**正式な manifest は `skill.toml`** です（task004 で導入）。レガシーな
`skill.md` frontmatter 形式も後方互換で読めますが、新規は
`skill.toml` を推奨します。完全仕様は
[package-manifest.md](docs/implementations/package-manifest.md)、
手順と詰まりどころは
[publishing.md](docs/implementations/publishing.md) を参照。

### 最小のスキル

```text
my-first-skill/
├── skill.toml     ← 常に zip に含まれる
└── SKILL.md       ← entry (default)。常に含まれる
```

`skill.toml`:

```toml
[skill]
name        = "my-first-skill"
version     = "0.1.0"
description = "Example skill."
author      = "team-doc"
# entry / include を省略すると
#   entry   = "SKILL.md"
#   include = [entry]
# が適用される。
```

### 追加ファイルを含める

`include` に glob を書くと、`scripts/` や `templates/` も zip に
載せられます。`skill.toml` / `entry` は `include` に書かなくても
必ず含まれます。

```toml
[skill]
name        = "my-skill"
version     = "1.0.0"
description = "With helper scripts."
include = [
    "SKILL.md",
    "scripts/**",     # 再帰。__pycache__ は自動除外
    "templates/*.txt",
]
```

### publish

```bash
skilltool publish ./my-first-skill/
# ✓ published my-first-skill 0.1.0
#   by alice  2026-04-15T10:23:45Z
```

同名同版の二重 publish は 409 で拒否されるので、2 回目以降は
`skill.toml` の `version` を上げてから再度叩く。SemVer ルールは
[VERSIONING_POLICY.md](docs/development-rules/VERSIONING_POLICY.md)。

### Collaborator として既存パッケージに version を足す

現状の registry に **package ownership はありません** — `users.toml`
に載っていて有効な token を持っていれば、誰でも既存 package の新 version
を追加できます。audit log と `published_by` に version 粒度で記録が
残るので、事後の追跡は可能です。

```bash
skilltool install docx              # 最新版を取得
cd docx
# …編集…
sed -i 's/^version = "0.1.0"$/version = "0.2.0"/' skill.toml
cd ..
skilltool publish ./docx/
```

team ACL などの拡張方針は
[limitations.md](docs/implementations/limitations.md) にまとめて
あります。

## Browser

registry は PyPI-like な HTML index をサーブします。

- `http://100.x.x.x:8765/` — パッケージ一覧
- `http://100.x.x.x:8765/packages/<name>` — バージョン一覧 + 直リンク
- `http://100.x.x.x:8765/docs` — FastAPI の Swagger UI（API を試打できる）

Tailscale に入っていないマシンから見たい場合は SSH port forward が楽:

```bash
ssh -N -L 8765:localhost:8765 skilltool@100.x.x.x
# 別ターミナルで http://localhost:8765/
```

外向けを完全に塞ぎたい時は `registry/.env` で `SKILLTOOL_BIND=100.x.x.x`
を設定すると Tailscale インターフェースでのみ listen します。

## Upgrade

```bash
uv tool upgrade skilltool
```

サーバ側のアップデートは:

```bash
sudo -iu skilltool
cd /srv/skilltool/skilltool-infra
git pull --ff-only
sudo systemctl reload skilltool.service    # docker compose up -d --build
```

## Testing

```bash
# 全テスト
uv run --project client --group dev \
       --with pyyaml --with fastapi --with python-multipart --with httpx \
       python -m pytest tests -q

# カテゴリ別
pytest tests/unit/
pytest tests/integration/
pytest tests/e2e/
```

テスト層の責務は
[architecture.md §11](docs/implementations/architecture.md#11-テスト戦略) を
参照。
