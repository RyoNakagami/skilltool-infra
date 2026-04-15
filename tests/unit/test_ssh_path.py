"""Unit tests for the SSH transport's verb builder.

Task003 §6 listed U20–U24 against a ``_path_to_args`` helper that
translated HTTP-style paths back into CLI verbs. Our transport design
skips the URL round-trip and goes straight from semantic methods to
verb argv, so these tests assert on ``SshTransport.build_command``
instead — the same invariant, measured at the boundary that actually
leaves the client process.
"""
from __future__ import annotations

from skilltool.config import Config
from skilltool.transport.ssh import SshTransport


def _cfg(**overrides: object) -> Config:
    base = dict(
        registry="http://example:8765",
        token="tok_alice_abc",
        transport="ssh",
        ssh_host="100.64.0.1",
        ssh_user="skilltool",
        registry_source="file",
        token_source="file",
        transport_source="file",
        ssh_host_source="file",
        ssh_user_source="file",
        config_file=__import__("pathlib").Path("/nonexistent"),
    )
    base.update(overrides)
    return Config(**base)  # type: ignore[arg-type]


def _verb_args(cmd: list[str]) -> list[str]:
    """Strip the ssh prefix so we can assert on verb + args alone."""
    assert "skilltool-server" in cmd, cmd
    return cmd[cmd.index("skilltool-server") + 1 :]


def test_list_verb_is_built_from_search_all() -> None:
    # U20 parity: 'list registry' is expressible either as the dedicated
    # `list` verb (server-side only) or a match-all `search`. The client
    # chooses `search` because the CLI only exposes search.
    t = SshTransport(_cfg())
    cmd = t.build_command("search", ".*")
    assert _verb_args(cmd) == ["search", ".*"]


def test_search_verb_carries_the_regex() -> None:
    # U21
    t = SshTransport(_cfg())
    cmd = t.build_command("search", "doc|pdf")
    assert _verb_args(cmd) == ["search", "doc|pdf"]


def test_show_verb_passes_the_package_name() -> None:
    # U22
    t = SshTransport(_cfg())
    cmd = t.build_command("show", "docx")
    assert _verb_args(cmd) == ["show", "docx"]


def test_download_verb_latest_and_pinned_version() -> None:
    # U23
    t = SshTransport(_cfg())
    assert _verb_args(t.build_command("download", "docx")) == ["download", "docx"]
    assert _verb_args(
        t.build_command("download", "docx", "--version", "1.2.0")
    ) == ["download", "docx", "--version", "1.2.0"]


def test_audit_verb_with_limit() -> None:
    # U24
    t = SshTransport(_cfg())
    cmd = t.build_command("audit", "--limit", "20")
    assert _verb_args(cmd) == ["audit", "--limit", "20"]


def test_prefix_uses_ssh_host_and_user() -> None:
    t = SshTransport(_cfg(ssh_host="100.64.0.99", ssh_user="otheruser"))
    cmd = t.build_command("list")
    assert cmd[0] == "ssh"
    # user@host must appear before the remote command name
    user_host_idx = cmd.index("otheruser@100.64.0.99")
    server_cmd_idx = cmd.index("skilltool-server")
    assert user_host_idx < server_cmd_idx


def test_ssh_options_harden_the_invocation() -> None:
    t = SshTransport(_cfg())
    cmd = t.build_command("list")
    # Stream the command as one string for easy flag assertions.
    joined = " ".join(cmd)
    assert "BatchMode=yes" in joined
    assert "ConnectTimeout=10" in joined
    assert "StrictHostKeyChecking=accept-new" in joined


def test_missing_ssh_host_raises_registry_error() -> None:
    from skilltool.transport.base import RegistryError

    t = SshTransport(_cfg(ssh_host=None, ssh_host_source="none"))
    try:
        t.build_command("list")
    except RegistryError as exc:
        assert "ssh_host" in str(exc)
    else:
        raise AssertionError("expected RegistryError when ssh_host is unset")
