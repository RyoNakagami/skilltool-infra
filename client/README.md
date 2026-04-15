# skilltool (client)

CLI client for the **skilltool** registry — a PyPI-like index for
`skill.md` packages (Claude Agent Skills).

## Install

```bash
uv tool install git+https://github.com/RyoNakagami/skilltool-infra.git#subdirectory=client

# Development install (edits reflected immediately)
uv tool install --editable ./skilltool-infra/client
```

## Configuration

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

Show the resolved configuration:

```bash
skilltool config
```

## Commands

```bash
skilltool install docx                    # extract to ./docx/
skilltool install docx --dest ./skills    # extract to ./skills/docx/
skilltool install docx --version 1.2.0    # pin a version
skilltool search "word|pdf"               # regex across name+description
skilltool show docx                       # versions + metadata
skilltool list                            # skills installed in CWD
skilltool publish ./my-skill/             # zip a dir and upload
skilltool publish ./my-skill.zip          # upload a pre-built zip
skilltool config                          # print resolved config
```

## Package format

A skill is a directory whose root contains `skill.md` with YAML
frontmatter:

```markdown
---
name: docx
version: 1.0.0
description: Author and inspect .docx files from Claude.
author: Ryo Nakagami
---

# docx skill

...
```

## Upgrade

```bash
uv tool upgrade skilltool
```
