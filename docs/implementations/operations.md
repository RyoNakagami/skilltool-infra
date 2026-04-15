---
author: "Ryo Nakagami"
date-modified: "2026-04-15"
project: skill-tool
---

# サーバー運用 Tips

skilltool registry を Server A で回しているインフラ担当向けの運用リファレンス。
設計背景は [architecture.md](./architecture.md)、publish の使い方は
[publishing.md](./publishing.md)、既知の制約は [limitations.md](./limitations.md)。

---

## 0. 推奨スペック（ユーザ数 × パッケージ数別）

registry コンテナ自体は軽量（FastAPI + uvicorn + 平文 filesystem）。
ボトルネックは **ディスク容量** と **Tailscale の帯域**、そして publish 時の
**zip パースに消える CPU** です。以下は 1 台持ちの目安。冗長化は現状
スコープ外です。

| 規模 | 想定ユーザ | 想定 package 数 | vCPU | RAM | ディスク (data) |
| --- | --- | --- | --- | --- | --- |
| S (個人 / チーム) | < 10 | < 50 × 10 ver | 1 | 1 GB | 10 GB |
| M (部署) | < 50 | < 500 × 20 ver | 2 | 2 GB | 50 GB |
| L (全社) | < 200 | < 5000 × 30 ver | 4 | 4 GB | 200 GB |

**ディスク見積もりの考え方**:

- 1 スキルの zip 平均サイズを `SZ` とする（実測 0.1〜1 MB が典型）
- packages 数 × 平均 version 数 × `SZ` × 1.2（sidecar yaml + オーバーヘッド）
- 例: 500 × 20 × 500 KB × 1.2 = 6 GB → 10 倍バッファで M の 50 GB
- **削除 API が無いので単調増加** ([limitations.md §2.1](./limitations.md#21-delete--yank-が無い))。
  手動 yank しない限り過去バージョンは残り続ける前提で見積もること

**CPU / メモリ**:

- 起動時の常駐 RAM は約 120 MB。1 publish につき一時的に zip サイズ程度の
  メモリを食うので、大きいパッケージを多数同時 publish する運用なら RAM に
  余裕を持たせる
- publish のスループットは `users.toml` の I/O と zip パースで決まる。
  数件/sec なら単一 vCPU で十分

**ネットワーク**:

- Tailscale: outbound UDP 41641（STUN/relay 用）、control plane HTTPS
- registry: inbound TCP 8765（HTTP transport）/ 22（SSH transport）

**冗長化・スケールアウト**:

- 現状の実装は **単一ホスト前提**（`/data` を共有前提にしていない）
- 水平スケールしたい場合は、`/data` を NFS / 共有 FS に載せる必要があるが、
  users.toml の race や publish の非 atomic 書き込み
  ([limitations.md §7](./limitations.md#71-publish-は-非-atomic)) が
  顕在化しやすくなる。現実的には Active-Standby の冷スタンバイで十分。

---

## 1. バージョン更新のプロセス

`skilltool-infra` リポジトリ自体を新しい main に追随させる手順です。
`skill.toml` の package version とは別物なので注意。

### 1.1 通常更新（無停止に近い）

skilltool ユーザで:

```bash
sudo -iu skilltool
cd /srv/skilltool/skilltool-infra

# (1) 変更を取り込む
git fetch --prune origin main
git log --oneline HEAD..origin/main     # 何が入るか確認
git pull --ff-only origin main

# (2) docker image を build し、コンテナを置き換える
cd registry
docker compose build --no-cache         # Dockerfile/code 変更を確実に拾う
docker compose up -d --force-recreate   # 旧コンテナを必ず捨てる

# (3) 健全性確認
docker compose ps                       # Up (healthy) を期待
docker compose logs --tail 40 registry
curl -fsS http://localhost:8765/api/health   # {"status":"ok"}
```

**ダウンタイム**: `up -d --force-recreate` の数秒のみ。クライアントの
publish は自動リトライしないので、再実行が必要になる程度。

### 1.2 systemd 経由で回している場合

```bash
cd /srv/skilltool/skilltool-infra
sudo -u skilltool git pull --ff-only

# unit の ExecReload が docker compose up -d --build を叩く
sudo systemctl reload skilltool.service
# もしくは明示的に restart
sudo systemctl restart skilltool.service
sudo systemctl status  skilltool.service
```

### 1.3 更新前にチェックすること

| 項目 | 確認方法 |
| --- | --- |
| API の互換性変更 | `docs/implementations/architecture.md` のエンドポイント表 / CHANGELOG（あれば） |
| ストレージ形式の変更 | `registry/data/` の構成が `load_manifest()` と整合するか |
| `.env.example` に新規追加された変数 | `diff registry/.env.example registry/.env` で差分確認 |
| 既存 users.toml フォーマット | TOML は 100% 互換で更新してきているので通常無関係 |

### 1.4 ロールバック

問題があれば前バージョンに即時戻せます。

```bash
cd /srv/skilltool/skilltool-infra
git log --oneline -10                      # 戻す先の commit を確認
git checkout -B revert-<date> <old-sha>    # detached HEAD を避ける
cd registry
docker compose up -d --build --force-recreate
```

`registry/data/` は触らないので publish 済みパッケージと audit log は
残り続けます。

### 1.5 更新ログを残す

監査性のため、更新のたびに以下を `/srv/skilltool/upgrade.log`
などに追記しておくと後で便利:

```text
2026-04-15T10:23:45Z  alice  updated to <sha>  (prev <sha>) reason: enable skill.toml
```

運用の歴史が audit log と分離して残ります。

---

## 2. ユーザ管理（確認 / 追加 / 削除）

ユーザ台帳は `registry/data/users.toml`。**サーバ再起動は不要** で、
各 API リクエストで `tomllib.load` が走ります。

### 2.1 現在のユーザ一覧を確認

```bash
sudo -iu skilltool
cd /srv/skilltool/skilltool-infra

# 生で見る
cat registry/data/users.toml

# token を伏せて一覧化
python3 - <<'PY'
import tomllib
from pathlib import Path
d = tomllib.loads(Path("registry/data/users.toml").read_text())
users = d.get("users", {})
w = max(len(n) for n in users) if users else 8
print(f"{'user':<{w}}  status    teams")
for name, meta in users.items():
    status = "disabled" if meta.get("disabled") else "active"
    teams = ",".join(meta.get("teams", []))
    print(f"{name:<{w}}  {status:<8}  {teams}")
PY
```

API 経由で確認する CLI は現状未実装。`audit log` 側で「誰がいつ publish
したか」は観測できます。

### 2.2 ユーザ追加（新規 token 発行）

```bash
./setup/server/add-user.sh <username> [team1,team2,...]
# 例: ./setup/server/add-user.sh alice team-doc,team-infra
```

実行結果のうち `token:` 行を **一度だけ** 控えて本人に共有（Slack DM /
1Password）。stdout にしか出ない設計なので、複数ターミナル・ログ転送を
挟まないで実行するのが安全です。メールや公開チャンネルへのコピペは避け
ること。

詳細な仕様と詰まりどころは
[publishing.md](./publishing.md) の "Client setup" と、前工程の user
プロビジョニングで扱ってきた内容を参照。

### 2.3 ユーザ失効（revoke）

```bash
./setup/server/revoke-user.sh <username>
```

- `users.toml` の当該ブロックに `disabled = true` を追記
- **次のリクエストから 401**。サーバ再起動不要
- 冪等（既に disabled なら no-op）

### 2.4 ユーザ完全削除（厳格運用）

`disabled = true` でなく **エントリごと削除** したいとき。監査観点では
「publish 履歴は publish.log に残るので users.toml から消しても追跡可能」
という判断ができる。

```bash
# バックアップ → 編集 → 検証 → 置換（atomic rename）
USERS=/srv/skilltool/skilltool-infra/registry/data/users.toml
cp "$USERS" "$USERS.bak.$(date +%s)"

python3 - <<'PY'
import tomllib, sys
from pathlib import Path
path = Path("$USERS".replace("$USERS", "") or "/srv/skilltool/skilltool-infra/registry/data/users.toml")
# ↑ heredoc のシェル変数は効かないので実パスで
PY
```

実用上は **エディタで直接編集** が最速です。編集後 TOML 検証:

```bash
python3 -c 'import tomllib, pathlib;
           tomllib.loads(pathlib.Path("registry/data/users.toml").read_text())
           and print("ok")'
```

### 2.5 Token ローテーション

同名ユーザで token を差し替えたいとき:

```bash
# (a) 新 token を生成
NEW=$(openssl rand -hex 32)

# (b) users.toml の該当行だけ上書き
sed -i -E \
  "s|(\[users\.alice\][^\[]*\ntoken = \")[0-9a-f]+(\")|\\1tok_alice_${NEW}\\2|" \
  registry/data/users.toml

# (c) 確認
grep alice registry/data/users.toml
```

古い token は即時 401 になります（サーバが users.toml を毎回読み直す）。
ローテーションの自動化はまだ提供していません
（[limitations.md §4.1](./limitations.md#41-token-rotation-が手動)）。

### 2.6 SSH key の追加・削除（SSH transport 利用時）

`users.toml` とは別に `authorized_keys` で管理。

```bash
# 追加
./setup/server/add-ssh-key.sh <username> /path/to/id_ed25519.pub

# 削除は authorized_keys を手で編集。各行末尾に
# "# skilltool:<username>" のコメントを付けているので grep で削除可能
sudo sed -i '/# skilltool:alice$/d' /home/skilltool/.ssh/authorized_keys
# もしくは /srv/skilltool/.ssh/authorized_keys (配置方針による)
```

token と ssh key は独立です。SSH 認証が通っても publish には `--token`
で per-user token が必要。二要素的に振る舞います。

---

## 3. パッケージ削除のプロセス

**削除 API は意図的に提供していません**（append-only 原則・監査目的）。
本当に消したい場合は管理者が Server A の FS を直接操作します。基本は
「新バージョンで差し替えて終わり」が推奨ルートです。

### 3.1 意思決定ツリー

```text
本当に消すべきか？
├── 誤 publish で中身が怪しい
│   └── 新しい version を publish して差し替え（推奨）
├── 機密情報が混入して即時除去が必要
│   └── 3.2 の手順で物理削除 + 3.4 のトークン失効
├── 古いバージョンが install されるのを防ぎたいだけ
│   └── 3.3 の "yank"（物理削除はしない）
└── パッケージ自体を廃止したい
    └── 3.2 全バージョン削除 + 名前の再利用方針を決める
```

### 3.2 物理削除（バージョン単位）

1 バージョンだけ消す例:

```bash
sudo -iu skilltool
cd /srv/skilltool/skilltool-infra

PKG=docx
VER=1.2.0
BASE=registry/data/packages/${PKG}

# (1) ファイル確認
ls -la ${BASE}/${VER}.zip ${BASE}/${VER}.yaml

# (2) 復旧用に退避（/srv/skilltool/quarantine/ に寄せる）
QUAR=/srv/skilltool/quarantine/${PKG}-${VER}-$(date +%Y%m%d-%H%M%S)
mkdir -p "${QUAR}"
mv ${BASE}/${VER}.zip ${BASE}/${VER}.yaml "${QUAR}/"

# (3) サーバに再読み込みさせる必要はない
#     (list_versions は毎回 directory scan なので即時反映)

# (4) 確認
curl -s "http://localhost:8765/api/packages/${PKG}" | jq .versions
```

**パッケージ全体を削除** したい場合は:

```bash
mv ${BASE} /srv/skilltool/quarantine/${PKG}-$(date +%Y%m%d-%H%M%S)
```

### 3.3 Yank（論理削除 / install 対象外にする）

「過去に誤った 1.2.0 を出してしまったので install されないようにしたい、
ただし監査のため痕跡は残す」という時の簡易 yank を手動で入れられます。

現状の registry は yank フラグを理解しませんが、**`.zip` を残したまま
`.yaml` の `yanked: true` を手で足す**ことで、将来的に対応コードを
入れたときにメタデータとして拾えます。また `.zip` をリネームして install
経路から外す手があります。

```bash
# 実質 yank: zip をリネームしておくと list_versions の glob 対象から外れる
mv ${BASE}/${VER}.zip ${BASE}/${VER}.zip.yanked
# ただし `.yaml` は残しておくと metadata 参照系が壊れるので、両方とも
# セットで外すのが無難:
mv ${BASE}/${VER}.yaml ${BASE}/${VER}.yaml.yanked
```

このテクニックは**非公式**（正式な yank 機能は
[limitations.md §2.1](./limitations.md#21-delete--yank-が無い)）。

### 3.4 削除に伴うトークン失効

機密漏洩で削除する場合、publish した user の token も失効させます:

```bash
./setup/server/revoke-user.sh alice
# audit log で追跡可能なことを確認
grep alice /srv/skilltool/skilltool-infra/registry/data/publish.log
```

必要なら別名 (`alice2`) で再発行。

### 3.5 audit log との整合

`publish.log` は append-only。パッケージを物理削除しても publish 履歴
はそのまま残ります（意図通り）。もし履歴自体も削除すべき事情があるなら、
該当行のみ sed で消してオリジナルを別に封印:

```bash
cp registry/data/publish.log /srv/skilltool/quarantine/publish.log.$(date +%s)
sed -i '/docx  1\.2\.0/d' registry/data/publish.log
```

ただし **改ざんが残らないためには通常の運用では触らない** のが原則。
法的・倫理的必要があるときだけ。

### 3.6 削除後にパッケージ名を再利用する場合

- 同じ `<name>/<version>.zip` が無ければ publish は通る（409 にならない）
- audit log 上は `0.9.0 → 1.0.0` のように遠くない version 数で
  旧 publish と新 publish が混在して見える可能性がある。削除時刻と
  再 publish 時刻で切り分け可能

---

## 4. トラブルシューティング

よく踏む順に並べた早見表。詳細手順は本文。

### 4.1 クライアントが 400 "skill.md not found at archive root"

- **原因**: サーバが task004 以前のコード
- **対応**: [1.1](#11-通常更新) の手順で image を rebuild
- **見分け方**: エラー文言が `skill.md not found at archive root` なら旧、
  `no manifest found: ...` なら新

### 4.2 クライアントが 401 "invalid or revoked token"

- **原因**: (a) token の typo、(b) 該当 user が disabled、(c) users.toml
  破損
- **対応**:

  ```bash
  # (a) token 先頭一致の有無
  grep "${SKILLTOOL_TOKEN%?????}" registry/data/users.toml

  # (b) disabled フラグ
  grep -B1 -A2 disabled registry/data/users.toml

  # (c) TOML として読めるか
  python3 -c 'import tomllib, pathlib;
             tomllib.loads(pathlib.Path("registry/data/users.toml").read_text())
             and print("valid toml")'
  ```

### 4.3 クライアントが 401 "missing bearer token"

- **原因**: `SKILLTOOL_TOKEN` 未設定、または SSH transport で `--token` 未指定
- **対応**:

  ```bash
  skilltool config
  # token が (unset) だったら ~/.config/skilltool/config.toml か環境変数に設定
  ```

### 4.4 クライアントが 409 "... already exists"

- **原因**: 同じ `name@version` の再 publish
- **対応**: `skill.toml` の `version` を上げる。削除 API は無い
  ([limitations.md §2.1](./limitations.md#21-delete--yank-が無い))

### 4.5 クライアントが `ConnectionError` / `connection refused`

- **原因**: (a) コンテナ停止中、(b) Tailscale 未接続、(c) SKILLTOOL_BIND
  が誤った IF を指している
- **対応**:

  ```bash
  # (a) コンテナ状態
  sudo -iu skilltool
  docker compose -f /srv/skilltool/skilltool-infra/registry/docker-compose.yml ps

  # (b) Tailscale 双方向
  tailscale ping <client-hostname>

  # (c) どこで listen しているか
  sudo ss -tlnp | grep 8765
  ```

### 4.6 `./setup/server/install.sh` が "permission denied ... docker.sock"

- **原因**: `skilltool` ユーザが docker グループに入っていない or 追加後
  のシェルが継承していない
- **対応**:

  ```bash
  sudo usermod -aG docker skilltool
  # 必ずセッションを張り直す
  exit
  sudo -iu skilltool
  id -Gn | tr ' ' '\n' | grep -x docker
  ```

### 4.7 `add-user.sh` が "permission denied: users.toml"

- **原因**: bind mount 先がコンテナ内で root 書き込みされて root 所有に
  なっている。`.env` に `PUID` / `PGID` を書く前に `up -d` した典型
- **対応**:

  ```bash
  cd registry
  docker compose down
  grep -q '^PUID=' .env || echo "PUID=$(id -u)" >> .env
  grep -q '^PGID=' .env || echo "PGID=$(id -g)" >> .env
  sudo chown -R skilltool:skilltool data
  docker compose up -d --build --force-recreate
  ```

### 4.8 `docker compose up --build` したのに変更が反映されない

- **原因**: image 層は更新されたが既存コンテナが再生成されない
- **対応**:

  ```bash
  docker compose down                 # コンテナを削除
  docker compose build --no-cache     # キャッシュ使わず再ビルド
  docker compose up -d --force-recreate
  ```

  それでもダメなら image 本体を消して build:

  ```bash
  docker image rm skilltool-registry:latest
  docker compose build --no-cache
  docker compose up -d
  ```

### 4.9 SSH transport で `Permission denied (publickey)`

- **原因**: (a) 公開鍵未登録、(b) authorized_keys のパーミッション違反、
  (c) 中継 hop の鍵不一致
- **対応**:

  ```bash
  # 詳細ログ
  ssh -v skilltool@100.x.x.x true

  # Server A 側の authorized_keys
  sudo ls -la /home/skilltool/.ssh/authorized_keys
  # → -rw------- skilltool skilltool であること
  sudo cat /home/skilltool/.ssh/authorized_keys

  # Jump host を挟む場合、~/.ssh/config の ProxyJump 参照
  ```

  BatchMode=yes なので password プロンプトに頼ることはできません。
  詳細は [docs/transport.md](../transport.md)。

### 4.10 publish が 500 "users.toml is not valid TOML"

- **原因**: `add-user.sh` の重複書き込み、手動編集でクォート漏れ 等
- **対応**:

  ```bash
  python3 -c 'import tomllib, pathlib;
             tomllib.loads(pathlib.Path("registry/data/users.toml").read_text())'
  # エラー行を示してくれる。バックアップから復旧、または修正
  ls -la registry/data/users.toml.tmp*    # add-user.sh の atomic write 残骸
  ```

### 4.11 ディスクが埋まってきた

- **現象**: `No space left on device` / publish が 500
- **対応**:

  ```bash
  du -sh /srv/skilltool/skilltool-infra/registry/data/*
  # packages/ が膨らんでいるのが通常

  # 古い docker image の掃除（データには触らない）
  docker system df
  docker image prune -a -f

  # 不要 version を quarantine 経由で削除 → §3.2
  ```

  **長期対策**: 3.3 の yank + バックアップ後削除 / ディスク拡張 /
  quarantine の定期クリーンアップ自動化。

### 4.12 サーバが "healthcheck timeout" で unhealthy

- **原因**: (a) uvicorn 起動中、(b) `/api/health` が 500、(c) ネット
  アクセス不可
- **対応**:

  ```bash
  docker compose logs --tail 100 registry
  docker compose exec registry python -c 'import urllib.request;
      print(urllib.request.urlopen("http://127.0.0.1:8765/api/health").read())'
  ```

### 4.13 audit log が書き込まれない

- **原因**: (a) publish_logic が例外で rollback した、(b) audit log
  ファイルの group 権限不足、(c) bind mount 設定間違い
- **対応**:

  ```bash
  ls -la registry/data/publish.log
  # skilltool 所有, mode 0664 程度であること

  docker compose exec registry ls -la /data/publish.log
  # コンテナ内から見えているかも確認
  ```

### 4.14 コンテナを起動すると即死する

```bash
docker compose logs --tail 100 registry
```

| メッセージ | 原因 |
| --- | --- |
| `ModuleNotFoundError: No module named 'fastapi'` | image build の段階で `pip install` が失敗。`docker compose build --no-cache` |
| `Error: bind: address already in use` | ホスト側で 8765 が使用中。`sudo ss -tlnp \| grep 8765` で犯人特定 |
| `invalid reference format` (docker-compose) | `.env` の変数が未定義のまま `${VAR}` 参照されている |
| permission denied on /data | bind mount の UID 不一致。§4.7 参照 |

### 4.15 どこから手をつけるか迷った時の診断コマンド集

下記をまとめて貼れば、ほぼ全ての状況を切り分け可能です。

```bash
set -x
uname -a
docker --version && docker compose version
sudo -iu skilltool bash -c '
  cd /srv/skilltool/skilltool-infra
  git log --oneline -3
  cd registry
  docker compose ps
  docker compose logs --tail 50 registry
  grep -c "manifest missing required field" server.py
  ls -la data/ | head
'
curl -fsS http://localhost:8765/api/health
```

---

## 5. バックアップと復旧

`/srv/skilltool/skilltool-infra/registry/data/` がすべての state です。
ここさえ守れば復旧可能。

### 5.1 取るべきもの

- `registry/data/packages/` — 公開済みバイナリ
- `registry/data/users.toml` — ユーザ台帳
- `registry/data/publish.log` — 監査ログ
- `registry/.env` — PUID/PGID と SKILLTOOL_BIND（無くても rebuild で足りるが、
  `SKILLTOOL_USERS_FILE` 等のオーバーライドがあれば必要）

### 5.2 シンプルな日次 snapshot 例

```bash
# cron: 毎日 03:00 に tar
0 3 * * * tar --exclude='.env.bak.*' -C /srv/skilltool/skilltool-infra \
  -czf /var/backups/skilltool-$(date +\%F).tgz registry/data registry/.env \
  && find /var/backups -name 'skilltool-*.tgz' -mtime +30 -delete
```

または [restic](https://restic.net/) で差分バックアップ。audit log が
append-only なので重複排除が効きます。

### 5.3 復旧

```bash
sudo -iu skilltool
cd /srv/skilltool/skilltool-infra
cd registry
docker compose down
tar -C /srv/skilltool/skilltool-infra -xzf /var/backups/skilltool-YYYY-MM-DD.tgz
docker compose up -d --build
```

コードそのもの（`server.py` など）は git 側で再取得すれば良いので、
バックアップ対象外で OK。

---

## 6. 監視（軽量）

メトリクスエンドポイントは現状未提供
（[limitations.md §9](./limitations.md#9-観測性--ロギング)）。以下で代替:

```bash
# 1 行 publish 統計
awk '{print $3}' registry/data/publish.log | sort | uniq -c | sort -rn

# ユーザ別 publish 回数
awk '{print $2}' registry/data/publish.log | sort | uniq -c | sort -rn

# 直近 24 時間の publish 数
awk -v cutoff="$(date -u -d '24 hours ago' +%Y-%m-%dT%H:%M:%SZ)" \
    '$1 > cutoff' registry/data/publish.log | wc -l

# ディスク使用量 top 20 package
du -sh registry/data/packages/*/ | sort -h | tail -20
```

systemd 経由なら `journalctl -u skilltool.service -f` で uvicorn の
アクセスログも追えます。

---

## 7. 参考

- [architecture.md](./architecture.md) — 全体設計
- [publishing.md](./publishing.md) — publish 手順
- [package-manifest.md](./package-manifest.md) — `skill.toml` 仕様
- [limitations.md](./limitations.md) — 既知の制限と拡張ポイント
- [../transport.md](../transport.md) — HTTP / SSH 切り替え
- [../../README.md](../../README.md) — Quick Start
