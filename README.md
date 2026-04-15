# skilltool-infra

A PyPI-like registry for `skill.md` packages (Claude Agent Skills) that runs
on a Tailscale-connected server and is driven by a `skilltool` CLI installed
via `uv tool`.

| Component     | What it is                                                                     | Lives in                                                                             |
| ------------- | ------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------ |
| Registry      | FastAPI + Docker image serving a simple PyPI-style API and browser UI          | [registry/](registry/)                                                               |
| Client CLI    | `skilltool install / search / show / list / publish / config`, built on Typer  | [client/](client/)                                                                   |
| Setup scripts | One-shot bootstrap for Server A and developer machines                         | [setup/](setup/)                                                                     |
| Deploy        | GitHub Actions workflow pushing to Server A over Tailscale SSH                 | [.github/workflows/deploy-registry.yml](.github/workflows/deploy-registry.yml)       |

## Server setup (Server A, on Tailscale)

```bash
git clone https://github.com/RyoNakagami/skilltool-infra.git
cd skilltool-infra
cp registry/.env.example registry/.env
./setup/server/install.sh                 # docker compose up -d --build

# Create at least one user before anyone can publish:
./setup/server/add-user.sh alice team-doc,team-infra
```

The registry listens on port `8765` and stores packages, user tokens,
and the publish audit log under `registry/data/` on the host
(bind-mounted into the container as `/data`).

## User management

Authentication is per-user. Each user has a bearer token listed in
`registry/data/users.toml` (git-ignored; schema in
[registry/users.example.toml](registry/users.example.toml)). The server
reads the file on every authenticated request — no restart is required
after edits.

```bash
# Issue a token
./setup/server/add-user.sh <username> [team1,team2,...]

# Revoke (sets `disabled = true`; rejected on next request)
./setup/server/revoke-user.sh <username>
```

Every successful publish is appended to `registry/data/publish.log`
and can be inspected via HTTP:

```bash
# Tail the log on Server A
tail -f registry/data/publish.log

# Over the API (your own token is required)
curl -H "Authorization: Bearer $SKILLTOOL_TOKEN" \
     "http://100.x.x.x:8765/api/audit?limit=100"
```

Each published package also carries `published_by` and `published_at`
in its server-side metadata, visible via `skilltool show <name>` and on
the registry's browser page.

## Client setup

```bash
uv tool install git+https://github.com/RyoNakagami/skilltool-infra.git#subdirectory=client

# Or develop against a local checkout:
uv tool install --editable ./skilltool-infra/client
```

### Configuration

Precedence: environment variables > config file > defaults.

```toml
# ~/.config/skilltool/config.toml
registry = "http://100.x.x.x:8765"   # Server A on Tailscale
token    = "your-publish-token"
```

Or via environment:

```bash
export SKILLTOOL_REGISTRY=http://100.x.x.x:8765
export SKILLTOOL_TOKEN=your-publish-token
```

Check the resolved configuration:

```bash
skilltool config
```

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

## Browser

The registry serves a PyPI-like index at the server root — e.g.
<http://100.x.x.x:8765/>. Each package has a detail page at
`/packages/<name>` with per-version download links.

## Upgrade

```bash
uv tool upgrade skilltool
```

## Repository layout

See [docs/implementations/repository-architecture.md](docs/implementations/repository-architecture.md)
for the canonical layout. Branch and versioning policies are under
[docs/development-rules/](docs/development-rules/).
