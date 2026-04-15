

```ini
skilltool-infra/
├── registry/
│   ├── Dockerfile
│   ├── docker-compose.yml
│   ├── server.py
│   └── .env.example
├── client/
│   ├── pyproject.toml
│   ├── README.md
│   └── src/
│       └── skilltool/
│           ├── __init__.py
│           ├── cli.py
│           ├── commands.py
│           ├── api.py
│           ├── config.py
│           └── output.py
├── setup/
│   ├── server/
│   │   └── install.sh
│   └── client/
│       ├── install.sh
│       └── uninstall.sh
├── .github/
│   └── workflows/
│       └── deploy-registry.yml
├── CODEOWNERS
├── README.md
└── .env.example

```
