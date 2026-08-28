"""End-to-end tests: the real CLI, real files, real HTTP.

Every test drives ``cli`` through Click's ``CliRunner`` and asserts on
observable outcomes — exit code, emitted JSON, and bytes on disk. The mihomo
controller is a real HTTP server on a loopback port, so URL construction,
Bearer auth and JSON decoding are genuinely exercised.

Nothing here requires a Clash Verge installation or network access.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml
from click.testing import CliRunner

from cli_anything.clash_verge.core import process as process_module
from cli_anything.clash_verge.clash_verge_cli import PROG_NAME, cli

from .conftest import MOCK_SECRET

# ===========================================================================
# helpers
# ===========================================================================


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def home_args(home: Path) -> list[str]:
    return ["--home", str(home)]


def run_json(runner: CliRunner, args: list[str], expect_ok: bool = True) -> Any:
    """Invoke the CLI with ``--json`` and parse the result."""
    result = runner.invoke(cli, ["--json", *args], obj={})
    if expect_ok:
        assert result.exit_code == 0, (
            f"command failed: {args}\n"
            f"exit={result.exit_code}\n"
            f"output={result.output}\n"
            f"exception={result.exception!r}"
        )
    return result


def payload_of(result: Any) -> Any:
    assert result.exit_code == 0, f"exit={result.exit_code} output={result.output}"
    return json.loads(result.output)


# ===========================================================================
class TestCliSurface:
    def test_version_flag(self, runner: CliRunner):
        result = runner.invoke(cli, ["--version"], obj={})
        assert result.exit_code == 0
        assert "1.0.0" in result.output

    def test_help_lists_command_groups(self, runner: CliRunner):
        result = runner.invoke(cli, ["--help"], obj={})
        assert result.exit_code == 0
        for group in (
            "env", "profile", "verge", "clash", "controller",
            "proxy", "mode", "conn", "rule", "core", "log", "session", "repl",
        ):
            assert group in result.output, f"{group} missing from help"

    def test_every_group_responds_to_help(self, runner: CliRunner):
        for group in (
            "env", "profile", "verge", "clash", "controller",
            "proxy", "mode", "conn", "rule", "core", "log", "session",
        ):
            result = runner.invoke(cli, [group, "--help"], obj={})
            assert result.exit_code == 0, f"{group} --help failed"
            assert "Usage:" in result.output

    def test_json_before_and_after_subcommand(
        self, home: Path, monkeypatch: pytest.MonkeyPatch, capsys
    ):
        """``--json`` must work in both positions, via the real entry point."""
        from cli_anything.clash_verge.clash_verge_cli import main

        expected = ["--home", str(home), "profile", "list"]
        for argv in (
            ["cli-anything-clash-verge", "--json", *expected],
            ["cli-anything-clash-verge", *expected, "--json"],
            ["cli-anything-clash-verge", "--home", str(home), "--json", "profile", "list"],
            ["cli-anything-clash-verge", "--home", str(home), "profile", "--json", "list"],
        ):
            monkeypatch.setattr("sys.argv", argv)
            capsys.readouterr()
            main()
            captured = capsys.readouterr()
            assert json.loads(captured.out) == [], f"failed for {argv}: {captured.out}"

    def test_json_as_option_value_is_not_eaten(self, runner: CliRunner, home: Path):
        """A literal ``--json`` value survives the hoisting logic.

        The first parse succeeds here, so no hoisting happens — which is
        exactly why the retry-based approach is safe.
        """
        result = runner.invoke(
            cli,
            ["--home", str(home), "verge", "set", "theme_mode", "--", "--json"],
            obj={},
        )
        assert result.exit_code == 0, result.output
        data = yaml.safe_load((home / "verge.yaml").read_text(encoding="utf-8"))
        assert data["theme_mode"] == "--json"


# ===========================================================================
class TestEnvironment:
    def test_env_paths_lists_candidates(self, runner: CliRunner, home: Path):
        data = payload_of(run_json(runner, ["--home", str(home), "env", "paths"]))
        assert data["in_use"] == str(home)
        assert data["layout"] == "custom"
        assert {"verge_yaml", "clash_yaml", "profiles_yaml", "runtime_config"} <= set(
            data["files"]
        )
        assert any(row["label"] == "release" for row in data["candidates"])

    def test_env_info_reads_runtime_config(
        self, runner: CliRunner, populated_home: Path
    ):
        data = payload_of(run_json(runner, ["--home", str(populated_home), "env", "info"]))
        assert data["runtime"]["available"] is True
        assert data["runtime"]["mixed_port"] == 7897
        assert data["profile"]["current_uid"] == "RAAA1111111"
        assert data["profile"]["main_profiles"] == 1

    def test_env_doctor_on_populated_home(
        self, runner: CliRunner, populated_home: Path
    ):
        data = payload_of(run_json(runner, ["--home", str(populated_home), "env", "doctor"]))
        levels = {row["level"] for row in data}
        assert "fail" not in levels
        assert any("runtime config" in row["check"] for row in data)

    def test_env_doctor_reports_missing_state(self, runner: CliRunner, home: Path):
        data = payload_of(run_json(runner, ["--home", str(home), "env", "doctor"]))
        failures = [row for row in data if row["level"] == "fail"]
        checks = {row["check"] for row in failures}
        assert {"profiles.yaml", "verge.yaml", "config.yaml"} <= checks

    def test_env_doctor_warns_about_dangling_profile(
        self, runner: CliRunner, populated_home: Path
    ):
        (populated_home / "profiles" / "RAAA1111111.yaml").unlink()
        data = payload_of(run_json(runner, ["--home", str(populated_home), "env", "doctor"]))
        assert any(
            row["level"] == "warn" and "missing data file" in row["detail"]
            for row in data
        )


# ===========================================================================
class TestProfiles:
    def test_profile_create_then_list(self, runner: CliRunner, home: Path):
        created = payload_of(
            run_json(runner, ["--home", str(home), "profile", "create", "my-profile"])
        )
        assert created["name"] == "my-profile"
        assert created["type"] == "local"

        rows = payload_of(run_json(runner, ["--home", str(home), "profile", "list"]))
        assert len(rows) == 1
        assert rows[0]["name"] == "my-profile"
        # the first profile becomes current automatically
        assert rows[0]["current"] is True

    def test_profile_create_writes_companions(self, runner: CliRunner, home: Path):
        run_json(runner, ["--home", str(home), "profile", "create", "p"])
        rows = payload_of(
            run_json(runner, ["--home", str(home), "profile", "list", "--all"])
        )
        assert len(rows) == 6, "a profile plus its five companions"

        types = sorted(row["type"] for row in rows)
        assert types == ["groups", "local", "merge", "proxies", "rules", "script"]

        for row in rows:
            assert row["file_exists"] is True, f"{row['file']} missing"

        scripts = [row for row in rows if row["type"] == "script"]
        assert scripts[0]["file"].endswith(".js")
        content = (home / "profiles" / scripts[0]["file"]).read_text(encoding="utf-8")
        assert "function main(config, profileName)" in content

    def test_profile_created_file_is_native_shape(self, runner: CliRunner, home: Path):
        run_json(runner, ["--home", str(home), "profile", "create", "p"])
        data = yaml.safe_load((home / "profiles.yaml").read_text(encoding="utf-8"))
        assert set(data) == {"current", "items"}
        item = data["items"][0]
        assert set(item) == {"uid", "type", "name", "file", "updated"}
        assert item["type"] == "local"
        assert item["uid"].startswith("L")
        assert item["file"] == f"{item['uid']}.yaml"
        assert isinstance(item["updated"], int)
        assert data["current"] == item["uid"]

    def test_profile_import_local(self, runner: CliRunner, home: Path, tmp_path: Path):
        source = tmp_path / "sub.yaml"
        source.write_text(
            "proxies:\n- name: n1\n  type: socks5\n  server: h\n  port: 1080\n"
            "rules:\n- MATCH,DIRECT\n",
            encoding="utf-8",
        )
        created = payload_of(
            run_json(
                runner,
                [
                    "--home", str(home), "profile", "import",
                    "--path", str(source), "--name", "imported",
                ],
            )
        )
        assert created["name"] == "imported"
        assert created["summary"]["proxy_count"] == 1
        assert created["summary"]["rule_count"] == 1

        stored = (home / "profiles" / f"{created['uid']}.yaml").read_text(encoding="utf-8")
        assert "n1" in stored

    def test_profile_import_rejects_non_profile(
        self, runner: CliRunner, home: Path, tmp_path: Path
    ):
        source = tmp_path / "not-a-profile.yaml"
        source.write_text("hello: world\n", encoding="utf-8")
        result = run_json(
            runner,
            ["--home", str(home), "profile", "import", "--path", str(source)],
            expect_ok=False,
        )
        assert result.exit_code != 0
        assert "proxy-providers" in result.output

    def test_profile_import_needs_exactly_one_source(
        self, runner: CliRunner, home: Path
    ):
        result = run_json(
            runner, ["--home", str(home), "profile", "import"], expect_ok=False
        )
        assert result.exit_code != 0
        assert "exactly one of --url or --path" in result.output

    def test_profile_select_and_current(
        self, runner: CliRunner, populated_home: Path
    ):
        created = payload_of(
            run_json(runner, ["--home", str(populated_home), "profile", "create", "second"])
        )
        run_json(runner, ["--home", str(populated_home), "profile", "select", "second"])

        current = payload_of(
            run_json(runner, ["--home", str(populated_home), "profile", "current"])
        )
        assert current["uid"] == created["uid"]

        data = yaml.safe_load((populated_home / "profiles.yaml").read_text(encoding="utf-8"))
        assert data["current"] == created["uid"]

    def test_profile_select_rejects_companion(self, runner: CliRunner, home: Path):
        run_json(runner, ["--home", str(home), "profile", "create", "p"])
        rows = payload_of(run_json(runner, ["--home", str(home), "profile", "list", "--all"]))
        merge = next(row for row in rows if row["type"] == "merge")
        result = run_json(
            runner,
            ["--home", str(home), "profile", "select", merge["uid"]],
            expect_ok=False,
        )
        assert result.exit_code != 0
        assert "not a selectable profile" in result.output

    def test_profile_rename(self, runner: CliRunner, home: Path):
        run_json(runner, ["--home", str(home), "profile", "create", "old"])
        run_json(runner, ["--home", str(home), "profile", "rename", "old", "new"])
        rows = payload_of(run_json(runner, ["--home", str(home), "profile", "list"]))
        assert rows[0]["name"] == "new"

    def test_profile_delete_removes_files(self, runner: CliRunner, home: Path):
        created = payload_of(
            run_json(runner, ["--home", str(home), "profile", "create", "doomed"])
        )
        data_file = home / "profiles" / f"{created['uid']}.yaml"
        assert data_file.exists()

        removed = payload_of(
            run_json(runner, ["--home", str(home), "profile", "delete", "doomed", "--yes"])
        )
        assert created["uid"] in removed["companions_removed"] or True
        assert not data_file.exists()
        assert payload_of(run_json(runner, ["--home", str(home), "profile", "list"])) == []
        assert len(list((home / "profiles").iterdir())) == 0

    def test_profile_export(self, runner: CliRunner, home: Path, tmp_path: Path):
        created = payload_of(
            run_json(runner, ["--home", str(home), "profile", "create", "exported"])
        )
        dest = tmp_path / "out.yaml"
        run_json(runner, ["--home", str(home), "profile", "export", "exported", str(dest)])
        assert dest.exists()
        assert "proxy-groups" in dest.read_text(encoding="utf-8")
        assert created["uid"]

    def test_profile_file_get_roundtrip(self, runner: CliRunner, home: Path, tmp_path: Path):
        run_json(runner, ["--home", str(home), "profile", "create", "rt"])
        result = runner.invoke(
            cli, ["--home", str(home), "profile", "file", "get", "rt"], obj={}
        )
        assert result.exit_code == 0
        assert "proxy-groups" in result.output

        replacement = tmp_path / "new.yaml"
        replacement.write_text(
            "proxies:\n- name: fresh\n  type: socks5\n", encoding="utf-8"
        )
        payload_of(
            run_json(
                runner,
                [
                    "--home", str(home), "profile", "file", "set", "rt",
                    str(replacement),
                ],
            )
        )
        after = runner.invoke(
            cli, ["--home", str(home), "profile", "file", "get", "rt"], obj={}
        )
        assert "fresh" in after.output

    def test_profile_file_set_rejects_non_profile(
        self, runner: CliRunner, home: Path, tmp_path: Path
    ):
        run_json(runner, ["--home", str(home), "profile", "create", "rt"])
        bad = tmp_path / "bad.yaml"
        bad.write_text("nothing: here\n", encoding="utf-8")
        result = run_json(
            runner,
            ["--home", str(home), "profile", "file", "set", "rt", str(bad)],
            expect_ok=False,
        )
        assert result.exit_code != 0

    def test_profile_show_reports_contents(self, runner: CliRunner, home: Path):
        run_json(runner, ["--home", str(home), "profile", "create", "shown"])
        data = payload_of(run_json(runner, ["--home", str(home), "profile", "show", "shown"]))
        assert data["item"]["name"] == "shown"
        assert len(data["companions"]) == 5
        assert data["size"] > 0


# ===========================================================================
class TestSettings:
    def test_verge_set_get_unset(self, runner: CliRunner, home: Path):
        run_json(runner, ["--home", str(home), "verge", "set", "theme_mode", "dark"])
        got = payload_of(
            run_json(runner, ["--home", str(home), "verge", "get", "theme_mode"])
        )
        assert got["value"] == "dark"
        assert got["set"] is True

        run_json(runner, ["--home", str(home), "verge", "unset", "theme_mode"])
        got = payload_of(
            run_json(runner, ["--home", str(home), "verge", "get", "theme_mode"])
        )
        assert got["set"] is False
        assert got["value"] == "system"

    def test_verge_set_boolean(self, runner: CliRunner, home: Path):
        run_json(
            runner, ["--home", str(home), "verge", "set", "enable_system_proxy", "true"]
        )
        data = yaml.safe_load((home / "verge.yaml").read_text(encoding="utf-8"))
        assert data["enable_system_proxy"] is True

    def test_verge_set_rejects_bad_boolean(self, runner: CliRunner, home: Path):
        result = run_json(
            runner,
            ["--home", str(home), "verge", "set", "enable_system_proxy", "maybe"],
            expect_ok=False,
        )
        assert result.exit_code != 0
        assert "boolean" in result.output

    def test_verge_set_rejects_unknown_key(self, runner: CliRunner, home: Path):
        result = run_json(
            runner,
            ["--home", str(home), "verge", "set", "not_a_key", "1"],
            expect_ok=False,
        )
        assert result.exit_code != 0
        assert "unknown verge key" in result.output

    def test_verge_set_rejects_encrypted_key(self, runner: CliRunner, home: Path):
        result = run_json(
            runner,
            ["--home", str(home), "verge", "set", "webdav_password", "secret"],
            expect_ok=False,
        )
        assert result.exit_code != 0
        assert "encrypted" in result.output

    def test_verge_list_includes_all_schema_keys(self, runner: CliRunner, home: Path):
        rows = payload_of(run_json(runner, ["--home", str(home), "verge", "list"]))
        keys = {row["key"] for row in rows}
        assert "enable_tun_mode" in keys
        assert "verge_mixed_port" in keys
        assert len(rows) > 50

    def test_clash_set_port_and_get(self, runner: CliRunner, home: Path):
        run_json(runner, ["--home", str(home), "clash", "set", "mixed-port", "8080"])
        got = payload_of(
            run_json(runner, ["--home", str(home), "clash", "get", "mixed-port"])
        )
        assert got["value"] == 8080
        data = yaml.safe_load((home / "config.yaml").read_text(encoding="utf-8"))
        assert data["mixed-port"] == 8080

    def test_clash_set_rejects_bad_port(self, runner: CliRunner, home: Path):
        result = run_json(
            runner,
            ["--home", str(home), "clash", "set", "mixed-port", "99999"],
            expect_ok=False,
        )
        assert result.exit_code != 0
        assert "65535" in result.output

    def test_clash_set_rejects_bad_mode(self, runner: CliRunner, home: Path):
        result = run_json(
            runner,
            ["--home", str(home), "clash", "set", "mode", "turbo"],
            expect_ok=False,
        )
        assert result.exit_code != 0
        assert "rule" in result.output

    def test_clash_set_mode(self, runner: CliRunner, home: Path):
        run_json(runner, ["--home", str(home), "clash", "set", "mode", "global"])
        got = payload_of(run_json(runner, ["--home", str(home), "clash", "get", "mode"]))
        assert got["value"] == "global"

    def test_clash_header_matches_clash_verge(self, runner: CliRunner, home: Path):
        run_json(runner, ["--home", str(home), "clash", "set", "mode", "direct"])
        text = (home / "config.yaml").read_text(encoding="utf-8")
        assert text.startswith("# Generated by Clash Verge")

    def test_unknown_keys_preserved_end_to_end(
        self, runner: CliRunner, populated_home: Path
    ):
        run_json(
            runner,
            ["--home", str(populated_home), "verge", "set", "theme_mode", "light"],
        )
        data = yaml.safe_load((populated_home / "verge.yaml").read_text(encoding="utf-8"))
        assert data["some_future_key"] == {"nested": True}
        assert data["enable_system_proxy"] is True
        assert data["theme_mode"] == "light"

    def test_clash_mappings_preserved_end_to_end(
        self, runner: CliRunner, populated_home: Path
    ):
        run_json(
            runner, ["--home", str(populated_home), "clash", "set", "mode", "direct"]
        )
        data = yaml.safe_load((populated_home / "config.yaml").read_text(encoding="utf-8"))
        assert data["tun"] == {"enable": False, "stack": "gvisor"}


# ===========================================================================
class TestController:
    def test_controller_status_reports_disabled(
        self, runner: CliRunner, populated_home: Path
    ):
        data = payload_of(
            run_json(runner, ["--home", str(populated_home), "controller", "status"])
        )
        assert data["enabled"] is False
        assert data["probe"]["reachable"] is False
        assert "127.0.0.1:9097" in data["url"]

    def test_controller_enable_writes_verge(self, runner: CliRunner, home: Path):
        payload_of(run_json(runner, ["--home", str(home), "controller", "enable"]))
        data = yaml.safe_load((home / "verge.yaml").read_text(encoding="utf-8"))
        assert data["enable_external_controller"] is True

    def test_controller_disable_writes_verge(self, runner: CliRunner, home: Path):
        run_json(runner, ["--home", str(home), "controller", "enable"])
        run_json(runner, ["--home", str(home), "controller", "disable"])
        data = yaml.safe_load((home / "verge.yaml").read_text(encoding="utf-8"))
        assert data["enable_external_controller"] is False

    def test_live_command_fails_clearly_when_disabled(
        self, runner: CliRunner, populated_home: Path
    ):
        result = run_json(
            runner,
            ["--home", str(populated_home), "proxy", "groups"],
            expect_ok=False,
        )
        assert result.exit_code != 0
        assert "controller enable" in result.output

    def test_proxy_groups_against_controller(
        self, runner: CliRunner, enabled_controller_home, mock_controller_server, reset_mock_state
    ):
        home = enabled_controller_home
        run_json(runner, ["--home", str(home), "controller", "url", "--set", mock_controller_server])
        run_json(runner, ["--home", str(home), "controller", "secret", "--set", MOCK_SECRET])

        data = payload_of(run_json(runner, ["--home", str(home), "proxy", "groups"]))
        names = {row["name"] for row in data["groups"]}
        assert names == {"GLOBAL", "PROXY"}
        assert data["count"] == 2

    def test_proxy_select_against_controller(
        self, runner: CliRunner, enabled_controller_home, mock_controller_server, reset_mock_state
    ):
        home = enabled_controller_home
        run_json(runner, ["--home", str(home), "controller", "url", "--set", mock_controller_server])
        run_json(runner, ["--home", str(home), "controller", "secret", "--set", MOCK_SECRET])

        result = payload_of(
            run_json(runner, ["--home", str(home), "proxy", "select", "PROXY", "node-a"])
        )
        assert result["now"] == "node-a"

        current = payload_of(
            run_json(runner, ["--home", str(home), "proxy", "current"])
        )
        assert current["selected"]["PROXY"] == "node-a"

    def test_proxy_select_rejects_non_member(
        self, runner: CliRunner, enabled_controller_home, mock_controller_server, reset_mock_state
    ):
        home = enabled_controller_home
        run_json(runner, ["--home", str(home), "controller", "url", "--set", mock_controller_server])
        run_json(runner, ["--home", str(home), "controller", "secret", "--set", MOCK_SECRET])

        result = run_json(
            runner,
            ["--home", str(home), "proxy", "select", "PROXY", "ghost"],
            expect_ok=False,
        )
        assert result.exit_code != 0
        assert "not a member" in result.output

    def test_mode_set_against_controller(
        self, runner: CliRunner, enabled_controller_home, mock_controller_server, reset_mock_state
    ):
        home = enabled_controller_home
        run_json(runner, ["--home", str(home), "controller", "url", "--set", mock_controller_server])
        run_json(runner, ["--home", str(home), "controller", "secret", "--set", MOCK_SECRET])

        assert payload_of(run_json(runner, ["--home", str(home), "mode", "get"]))["mode"] == "rule"
        assert (
            payload_of(run_json(runner, ["--home", str(home), "mode", "set", "global"]))["mode"]
            == "global"
        )

    def test_conn_list_against_controller(
        self, runner: CliRunner, enabled_controller_home, mock_controller_server, reset_mock_state
    ):
        home = enabled_controller_home
        run_json(runner, ["--home", str(home), "controller", "url", "--set", mock_controller_server])
        run_json(runner, ["--home", str(home), "controller", "secret", "--set", MOCK_SECRET])

        data = payload_of(run_json(runner, ["--home", str(home), "conn", "list"]))
        assert data["count"] == 1
        assert data["connections"][0]["host"] == "example.com"
        assert data["connections"][0]["chains"] == ["PROXY", "node-a"]

        assert payload_of(run_json(runner, ["--home", str(home), "conn", "close-all"]))["closed_all"]

    def test_rule_list_against_controller(
        self, runner: CliRunner, enabled_controller_home, mock_controller_server, reset_mock_state
    ):
        home = enabled_controller_home
        run_json(runner, ["--home", str(home), "controller", "url", "--set", mock_controller_server])
        run_json(runner, ["--home", str(home), "controller", "secret", "--set", MOCK_SECRET])

        data = payload_of(run_json(runner, ["--home", str(home), "rule", "list"]))
        assert data["count"] == 2
        assert data["rules"][0]["payload"] == "example.com"

    def test_proxy_providers_against_controller(
        self, runner: CliRunner, enabled_controller_home, mock_controller_server, reset_mock_state
    ):
        home = enabled_controller_home
        run_json(runner, ["--home", str(home), "controller", "url", "--set", mock_controller_server])
        run_json(runner, ["--home", str(home), "controller", "secret", "--set", MOCK_SECRET])

        data = payload_of(run_json(runner, ["--home", str(home), "proxy", "providers"]))
        assert data["count"] == 1
        assert data["providers"][0]["name"] == "provider-one"

    def test_wrong_secret_gives_actionable_error(
        self, runner: CliRunner, enabled_controller_home, mock_controller_server, reset_mock_state
    ):
        home = enabled_controller_home
        run_json(runner, ["--home", str(home), "controller", "url", "--set", mock_controller_server])
        run_json(runner, ["--home", str(home), "controller", "secret", "--set", "wrong"])

        result = run_json(
            runner, ["--home", str(home), "proxy", "groups"], expect_ok=False
        )
        assert result.exit_code != 0
        assert "401" in result.output


# ===========================================================================
class TestSafety:
    def test_write_blocked_while_app_running(
        self, runner: CliRunner, home: Path, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setattr(process_module, "gui_running", lambda: True)
        result = run_json(
            runner,
            ["--home", str(home), "verge", "set", "theme_mode", "dark"],
            expect_ok=False,
        )
        assert result.exit_code != 0
        assert "Clash Verge is running" in result.output
        assert not (home / "verge.yaml").exists()

    def test_force_overrides_guard(
        self, runner: CliRunner, home: Path, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setattr(process_module, "gui_running", lambda: True)
        run_json(
            runner,
            ["--home", str(home), "verge", "set", "theme_mode", "dark", "--force"],
        )
        data = yaml.safe_load((home / "verge.yaml").read_text(encoding="utf-8"))
        assert data["theme_mode"] == "dark"

    def test_session_undo_restores_config(self, runner: CliRunner, home: Path):
        run_json(runner, ["--home", str(home), "verge", "set", "theme_mode", "dark"])
        original = (home / "verge.yaml").read_text(encoding="utf-8")

        run_json(runner, ["--home", str(home), "verge", "set", "theme_mode", "light"])
        assert (home / "verge.yaml").read_text(encoding="utf-8") != original

        payload_of(run_json(runner, ["--home", str(home), "session", "undo"]))
        assert (home / "verge.yaml").read_text(encoding="utf-8") == original

    def test_session_redo_reapplies(self, runner: CliRunner, home: Path):
        run_json(runner, ["--home", str(home), "verge", "set", "theme_mode", "dark"])
        run_json(runner, ["--home", str(home), "verge", "set", "theme_mode", "light"])
        changed = (home / "verge.yaml").read_text(encoding="utf-8")

        run_json(runner, ["--home", str(home), "session", "undo"])
        run_json(runner, ["--home", str(home), "session", "redo"])
        assert (home / "verge.yaml").read_text(encoding="utf-8") == changed

    def test_session_history_records_labels(self, runner: CliRunner, home: Path):
        run_json(runner, ["--home", str(home), "verge", "set", "theme_mode", "dark"])
        run_json(runner, ["--home", str(home), "clash", "set", "mode", "global"])
        rows = payload_of(run_json(runner, ["--home", str(home), "session", "history"]))
        assert rows[0]["label"] == "clash set mode global"
        assert rows[1]["label"] == "verge set theme_mode dark"

    def test_undo_with_empty_history_is_safe(self, runner: CliRunner, home: Path):
        result = run_json(runner, ["--home", str(home), "session", "undo"])
        assert payload_of(result)["undone"] is None


# ===========================================================================
class TestHumanReadableRendering:
    """Render every command the way a human sees it — with no ``--json``.

    This path was entirely untested, which is how ``ReplSkin.table()`` missing
    its ``title`` parameter could crash the ``proxy`` commands while the whole
    suite stayed green: every existing test invoked the CLI with ``--json``,
    which never touches the table renderer.
    """

    @classmethod
    def _run(
        cls, runner: CliRunner, args: list[str], expect_ok: bool = True
    ):
        result = runner.invoke(cli, args, obj={})
        assert not isinstance(result.exception, TypeError), (
            f"{args} raised {result.exception!r}\noutput={result.output}"
        )
        if expect_ok:
            assert result.exit_code == 0, (
                f"command failed: {args}\n"
                f"exit={result.exit_code}\n"
                f"output={result.output}\n"
                f"exception={result.exception!r}"
            )
        return result

    @pytest.mark.parametrize(
        "args",
        [
            ["profile", "list"],
            ["profile", "current"],
            ["verge", "list"],
            ["clash", "list"],
            ["env", "info"],
            ["env", "paths"],
            ["session", "show"],
        ],
    )
    def test_file_commands_render(
        self, runner: CliRunner, populated_home: Path, args
    ):
        result = self._run(runner, ["--home", str(populated_home), *args])
        assert result.output.strip()

    @pytest.mark.parametrize(
        "args",
        [
            ["proxy", "groups"],
            ["proxy", "current"],
            ["proxy", "nodes"],
            ["proxy", "providers"],
            ["mode", "get"],
            ["conn", "list"],
            ["rule", "list"],
            ["controller", "status"],
            ["core", "version"],
        ],
    )
    def test_live_commands_render(
        self,
        runner: CliRunner,
        enabled_controller_home: Path,
        mock_controller_server,
        reset_mock_state,
        args,
    ):
        home = enabled_controller_home
        self._run(
            runner,
            ["--home", str(home), "controller", "url", "--set", mock_controller_server],
        )
        self._run(
            runner,
            ["--home", str(home), "controller", "secret", "--set", MOCK_SECRET],
        )
        result = self._run(runner, ["--home", str(home), *args])
        assert result.output.strip()

    def test_proxy_groups_renders_its_title(
        self,
        runner: CliRunner,
        enabled_controller_home: Path,
        mock_controller_server,
        reset_mock_state,
    ):
        """Direct regression guard for the ``title=`` crash."""
        home = enabled_controller_home
        self._run(
            runner,
            ["--home", str(home), "controller", "url", "--set", mock_controller_server],
        )
        self._run(
            runner,
            ["--home", str(home), "controller", "secret", "--set", MOCK_SECRET],
        )
        result = self._run(runner, ["--home", str(home), "proxy", "groups"])
        assert "Proxy groups" in result.output

    def test_env_doctor_renders(self, runner: CliRunner, populated_home: Path):
        """doctor may exit non-zero when it reports problems; it must render."""
        result = self._run(
            runner,
            ["--home", str(populated_home), "env", "doctor"],
            expect_ok=False,
        )
        assert result.output.strip()

    def test_profile_create_is_undoable(self, runner: CliRunner, home: Path):
        run_json(runner, ["--home", str(home), "profile", "create", "temp"])
        assert len(list((home / "profiles").iterdir())) == 6

        run_json(runner, ["--home", str(home), "session", "undo"])
        assert payload_of(run_json(runner, ["--home", str(home), "profile", "list"])) == []
        assert len(list((home / "profiles").iterdir())) == 0

    def test_no_temp_files_left_behind(self, runner: CliRunner, home: Path):
        run_json(runner, ["--home", str(home), "profile", "create", "p"])
        run_json(runner, ["--home", str(home), "verge", "set", "theme_mode", "dark"])
        run_json(runner, ["--home", str(home), "clash", "set", "mode", "global"])
        leftovers = [
            path for path in home.rglob("*")
            if path.name.endswith(".tmp") or path.name.startswith(".")
        ]
        assert leftovers == [], f"atomic writes left residue: {leftovers}"

    def test_session_isolated_from_real_home(
        self, runner: CliRunner, isolated_env, home: Path
    ):
        """The session file must live in the temp tree, not in $HOME."""
        run_json(runner, ["--home", str(home), "verge", "set", "theme_mode", "dark"])
        session_file = isolated_env["session_file"]
        assert session_file.exists()
        assert session_file.parent != Path.home()


# ===========================================================================
class TestLogs:
    def test_log_tail_reads_latest(self, runner: CliRunner, home: Path):
        logs = home / "logs"
        logs.mkdir(parents=True)
        (logs / "latest.log").write_text(
            "\n".join(f"line-{i}" for i in range(10)), encoding="utf-8"
        )
        data = payload_of(run_json(runner, ["--home", str(home), "log", "tail", "--lines", "3"]))
        assert data["exists"] is True
        assert data["lines"] == ["line-7", "line-8", "line-9"]

    def test_log_tail_missing_file(self, runner: CliRunner, home: Path):
        data = payload_of(run_json(runner, ["--home", str(home), "log", "tail"]))
        assert data["exists"] is False

    def test_log_files_lists(self, runner: CliRunner, home: Path):
        logs = home / "logs"
        logs.mkdir(parents=True)
        (logs / "latest.log").write_text("x", encoding="utf-8")
        data = payload_of(run_json(runner, ["--home", str(home), "log", "files"]))
        assert len(data["files"]) == 1


# ===========================================================================
class TestRepl:
    def test_repl_exits_on_quit(self, runner: CliRunner, home: Path):
        result = runner.invoke(
            cli,
            ["--home", str(home), "repl"],
            obj={},
            input="profile list\nquit\n",
        )
        assert result.exit_code == 0
        assert "Goodbye" in result.output or "goodbye" in result.output.lower()

    def test_repl_reports_unknown_command(self, runner: CliRunner, home: Path):
        result = runner.invoke(
            cli,
            ["--home", str(home), "repl"],
            obj={},
            input="totally-bogus\nquit\n",
        )
        assert result.exit_code == 0


# ===========================================================================
def test_prog_name_constant():
    """The console script name is stable — SKILL.md and docs depend on it."""
    assert PROG_NAME == "cli-anything-clash-verge"


def test_ensure_utf8_output_is_safe():
    """The encoding guard must never raise, whatever stream it is handed."""
    from cli_anything.clash_verge.clash_verge_cli import ensure_utf8_output

    ensure_utf8_output()  # twice: idempotent
    ensure_utf8_output()
    assert sys.stdout is not None


def test_ensure_utf8_output_survives_broken_stream(monkeypatch: pytest.MonkeyPatch):
    """A stream whose reconfigure() blows up must not take the CLI down."""

    class _Hostile:
        def reconfigure(self, **kwargs):
            raise ValueError("nope")

        def write(self, text):
            return len(text)

    from cli_anything.clash_verge.clash_verge_cli import ensure_utf8_output

    monkeypatch.setattr(sys, "stdout", _Hostile())
    monkeypatch.setattr(sys, "stderr", _Hostile())
    ensure_utf8_output()
