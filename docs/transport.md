---
author: "Ryo Nakagami"
date-modified: "2026-04-15"
project: skill-tool
---

# Transport ガイド

skilltool クライアントは 2 種類のトランスポートをサポートしています。

| Transport | Wire                               | サーバ要件                  | 向いている用途                     |
| --------- | ---------------------------------- | ---------------------- | --------------------------- |
| `http`    | Tailscale + HTTP :8765             | Docker (FastAPI server) | 通常運用                        |
| `ssh`     | Tailscale + SSH :22 → `skilltool-server` | `skilltool-server` + SSH user | HTTP が閉じている・Docker を動かせない場合 |

## 設定

解決順は以下のとおりです（上が優先）：

1. 環境変数
   - `SKILLTOOL_TRANSPORT` (`http` / `ssh`)
   - `SKILLTOOL_SSH_HOST` (`ssh` のみ)
   - `SKILLTOOL_SSH_USER` (`ssh` のみ、デフォルト `skilltool`)
   - `SKILLTOOL_REGISTRY` (`http` のみ)
   - `SKILLTOOL_TOKEN`
2. `~/.config/skilltool/config.toml`
3. `localhost:8765/api/health` が 1 秒以内に応答すれば `registry` を
   `http://localhost:8765` に自動ピン留め
4. デフォルト (`http` / `DEFAULT_REGISTRY`)

### `config.toml` 例

```toml
# HTTP (既定)
transport = "http"
registry  = "http://100.x.x.x:8765"
token     = "tok_alice_..."
```

```toml
# SSH
transport = "ssh"
ssh_host  = "100.x.x.x"       # Tailscale IP
ssh_user  = "skilltool"
token     = "tok_alice_..."   # publish 時のみ使用（監査ログに残る）
```

```toml
# Server A 上で直接 CLI を使うとき（localhost 自動検出）
token     = "tok_alice_..."
# transport / registry の記述は不要
```

### 切り替え方法

`skilltool config` で現在の解決状態を確認できます。

```bash
$ SKILLTOOL_TRANSPORT=ssh skilltool config
transport    ssh        [env]
ssh_host     100.64.0.1 [file]
ssh_user     skilltool  [default]
registry     http://…   [file]
token        tok_…      [env]
config file  /home/.../config.toml
version      0.1.0
```

## SSH トランスポートのセットアップ

サーバ側:

```bash
# Server A 上で
./setup/server/install.sh --with-ssh
./setup/server/add-ssh-key.sh alice /tmp/alice.pub
```

これで:

- `skilltool` システムユーザが作成され
- `/usr/local/bin/skilltool-server` が `registry/server_cli.py` への symlink になり
- `/home/skilltool/.ssh/authorized_keys` に alice の公開鍵が追加されます

クライアント側:

```bash
export SKILLTOOL_TRANSPORT=ssh
export SKILLTOOL_SSH_HOST=<tailnet-ip>
export SKILLTOOL_SSH_USER=skilltool
export SKILLTOOL_TOKEN=tok_alice_...

skilltool search doc
skilltool publish ./my-skill/
```

`skilltool` コマンドの使い方はトランスポートに関わらず変わりません。
`commands.py` は `api.RegistryClient(cfg)` を通して抽象化された
`Transport` だけを呼び出しているため、設定変更だけで HTTP/SSH を切り
替えられます。

## ワイヤプロトコル (SSH)

`ssh <user>@<host> skilltool-server <verb> [args]` で `server_cli.py`
を起動します。

| Verb                                | stdout                      | 備考                    |
| ----------------------------------- | --------------------------- | --------------------- |
| `list`                              | JSON 配列（全パッケージ）      | `search .*` と同等         |
| `search <regex>`                    | JSON 配列                   | `re.IGNORECASE`        |
| `show <name>`                       | JSON オブジェクト            | 404 は stderr + exit!=0 |
| `download <name> [--version <v>]`   | zip のバイト列（バイナリ）    | 改行なし                 |
| `publish --token <tok> --data <b64>` | JSON（publish結果）         | `--data -` で stdin 可能 |
| `audit [--limit <n>]`               | JSON（`entries` / `total`） |                       |

エラーは stderr に `{"error": "..."}` を出力し、exit code は 0 以外。
クライアントの `SshTransport` はどちらも `RegistryError` として扱うの
で、`commands.py` 側の `try/except RegistryError` で一元的にハンド
リングできます。

## テスト

`SKILLTOOL_SSH_COMMAND` に任意のコマンドを設定すると、`ssh
user@host skilltool-server` プレフィクスを丸ごと置き換えます。
`tests/e2e/test_ssh_flow.py` はこの仕組みで実際の sshd なしに
トランスポート境界を通過します。

```bash
SKILLTOOL_SSH_COMMAND="python3 registry/server_cli.py" \
  skilltool search doc
```

## 注意事項

- `StrictHostKeyChecking=accept-new` により初回接続時のみ自動承認します。
  本番では `known_hosts` を事前に配布することを推奨します。
- `skilltool-server` は `skilltool` ユーザ権限で動きます。
  `registry/data/` は `group=skilltool` で読み取り可能にしてください
  （`install.sh --with-ssh` が設定します）。
- publish 時の token は SSH トランスポートでも必須です。
  SSH 認証だけでは "誰が publish したか" を audit log に残せないため、
  ユーザごとの per-user トークンで同定します。
