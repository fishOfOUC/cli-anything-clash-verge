"""Unit tests for the Clash Verge harness core modules.

Grouped to mirror TEST.md: paths, yamlio, session, verge, clash, profiles,
subscription, controller, process.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest
import yaml

from cli_anything.clash_verge.core import process as process_module
from cli_anything.clash_verge.core.clash import (
    DEFAULT_EXTERNAL_CONTROLLER,
    DEFAULT_SECRET,
    ClashConfig,
    ClashError,
    RuntimeConfig,
)
from cli_anything.clash_verge.core.controller import ControllerError, MihomoController
from cli_anything.clash_verge.core.paths import (
    APP_ID,
    DEV_APP_ID,
    ClashVergeNotFound,
    ClashVergePaths,
    default_home_dir,
    resolve_home_dir,
)
from cli_anything.clash_verge.core.profiles import (
    ITEM_TYPES,
    Profiles,
    ProfileError,
    file_name_for,
    new_uid,
)
from cli_anything.clash_verge.core.session import (
    ChangeSet,
    Session,
    SessionError,
    SessionManager,
)
from cli_anything.clash_verge.core.subscription import (
    SubscriptionError,
    parse_subscription_userinfo,
    parse_update_interval,
    read_local,
    validate_payload,
)
from cli_anything.clash_verge.core.verge import (
    ENCRYPTED_KEYS,
    SCHEMA as VERGE_SCHEMA,
    VergeConfig,
    VergeError,
)
from cli_anything.clash_verge.core.yamlio import (
    VERGE_HEADER,
    YamlError,
    dumps_yaml,
    load_mapping,
    load_yaml,
    save_yaml,
)

from .conftest import MOCK_SECRET, paths_for


# ===========================================================================
class TestPaths:
    """``paths.py`` reproduces ``src-tauri/src/utils/dirs.rs``."""

    def test_default_home_dir_uses_app_id(self):
        assert default_home_dir().name == APP_ID

    def test_dev_home_dir_uses_dev_suffix(self):
        assert default_home_dir(dev=True).name == DEV_APP_ID
        assert DEV_APP_ID.endswith(".dev")

    def test_resolve_explicit_existing(self, tmp_path: Path):
        assert resolve_home_dir(tmp_path) == tmp_path

    def test_resolve_explicit_missing_raises(self, tmp_path: Path):
        missing = tmp_path / "nope"
        with pytest.raises(ClashVergeNotFound) as excinfo:
            resolve_home_dir(missing)
        assert "does not exist" in str(excinfo.value)

    def test_candidates_include_release_and_dev(self):
        from cli_anything.clash_verge.core.paths import candidate_home_dirs

        labels = {label for label, _ in candidate_home_dirs()}
        assert "release" in labels
        assert "dev" in labels

    def test_layout_classification(self):
        assert ClashVergePaths(Path("/x") / APP_ID).layout() == "release"
        assert ClashVergePaths(Path("/x") / DEV_APP_ID).layout() == "dev"
        assert ClashVergePaths(Path("/x") / ".config" / APP_ID).layout() == "portable"
        assert ClashVergePaths(Path("/x") / "elsewhere").layout() == "custom"

    def test_paths_file_names(self, home: Path):
        paths = paths_for(home)
        assert paths.verge_yaml.name == "verge.yaml"
        assert paths.clash_yaml.name == "config.yaml"
        assert paths.profiles_yaml.name == "profiles.yaml"
        assert paths.runtime_config.name == "clash-verge.yaml"

    def test_profile_file_joins_profiles_dir(self, home: Path):
        paths = paths_for(home)
        assert paths.profile_file("R1.yaml") == home / "profiles" / "R1.yaml"

    def test_describe_is_json_serializable(self, home: Path):
        described = paths_for(home).describe()
        assert json.dumps(described)
        assert "verge_yaml" in described


# ===========================================================================
class TestYamlIO:
    """Round-trip contract: a CLI edit must never lose data."""

    def test_load_missing_returns_default(self, tmp_path: Path):
        assert load_yaml(tmp_path / "absent.yaml", default={"a": 1}) == {"a": 1}

    def test_load_empty_returns_default(self, tmp_path: Path):
        (tmp_path / "empty.yaml").write_text("", encoding="utf-8")
        assert load_yaml(tmp_path / "empty.yaml", default=[]) == []

    def test_roundtrip_preserves_unknown_keys(self, tmp_path: Path):
        path = tmp_path / "v.yaml"
        save_yaml(path, {"known": 1, "future_key": {"nested": [1, 2]}})
        data = load_mapping(path)
        data["known"] = 2
        save_yaml(path, data)
        reloaded = load_mapping(path)
        assert reloaded["known"] == 2
        assert reloaded["future_key"] == {"nested": [1, 2]}

    def test_header_written(self, tmp_path: Path):
        path = tmp_path / "v.yaml"
        save_yaml(path, {"a": 1}, header=VERGE_HEADER)
        assert path.read_text(encoding="utf-8").startswith("# Clash Verge Config")

    def test_backup_returns_previous_content(self, tmp_path: Path):
        path = tmp_path / "v.yaml"
        save_yaml(path, {"a": 1})
        previous = save_yaml(path, {"a": 2}, make_backup=True)
        assert previous is not None
        assert "a: 1" in previous

    def test_tolerant_loader_handles_unknown_tag(self, tmp_path: Path):
        path = tmp_path / "v.yaml"
        path.write_text("value: !custom hello\n", encoding="utf-8")
        assert load_yaml(path) == {"value": "hello"}

    def test_non_mapping_raises(self, tmp_path: Path):
        path = tmp_path / "v.yaml"
        path.write_text("- just\n- a\n- list\n", encoding="utf-8")
        with pytest.raises(YamlError):
            load_mapping(path)

    def test_invalid_yaml_raises(self, tmp_path: Path):
        path = tmp_path / "v.yaml"
        path.write_text("key: [unclosed\n", encoding="utf-8")
        with pytest.raises(YamlError):
            load_yaml(path)

    def test_dumps_keeps_key_order(self):
        text = dumps_yaml({"b": 1, "a": 2}, header=None)
        assert list(yaml.safe_load(text)) == ["b", "a"]

    def test_unicode_preserved(self, tmp_path: Path):
        path = tmp_path / "v.yaml"
        save_yaml(path, {"name": "机场订阅"})
        assert load_mapping(path)["name"] == "机场订阅"


# ===========================================================================
class TestSession:
    """The undo stack — what makes agent editing reversible."""

    def test_locked_save_creates_file(self, tmp_path: Path):
        target = tmp_path / "s.json"
        manager = SessionManager(target)
        manager.session.home_dir = "/somewhere"
        manager.save()
        assert json.loads(target.read_text())["home_dir"] == "/somewhere"

    def test_stale_lock_reclaimed(self, tmp_path: Path):
        target = tmp_path / "s.json"
        lock = target.with_suffix(target.suffix + ".lock")
        lock.write_text("99999", encoding="utf-8")
        old = time.time() - 3600
        os.utime(lock, (old, old))

        manager = SessionManager(target)
        manager.save()  # must not hang or raise
        assert target.exists()
        assert not lock.exists()

    def test_changeset_captures_before_and_after(self, tmp_path: Path):
        path = tmp_path / "f.txt"
        path.write_text("before", encoding="utf-8")
        changes = ChangeSet("test")
        changes.track(path)
        path.write_text("after", encoding="utf-8")
        entry = changes.finish()
        assert entry["files"][str(path)]["before"] == "before"
        assert entry["files"][str(path)]["after"] == "after"

    def test_undo_restores_previous_content(self, tmp_path: Path):
        path = tmp_path / "f.txt"
        path.write_text("before", encoding="utf-8")
        manager = SessionManager(tmp_path / "s.json")

        changes = ChangeSet("edit")
        changes.track(path)
        path.write_text("after", encoding="utf-8")
        manager.commit(changes)

        assert path.read_text() == "after"
        manager.undo()
        assert path.read_text() == "before"

    def test_redo_reapplies(self, tmp_path: Path):
        path = tmp_path / "f.txt"
        path.write_text("before", encoding="utf-8")
        manager = SessionManager(tmp_path / "s.json")

        changes = ChangeSet("edit")
        changes.track(path)
        path.write_text("after", encoding="utf-8")
        manager.commit(changes)

        manager.undo()
        manager.redo()
        assert path.read_text() == "after"

    def test_undo_deletes_created_file(self, tmp_path: Path):
        path = tmp_path / "new.txt"
        manager = SessionManager(tmp_path / "s.json")

        changes = ChangeSet("create")
        changes.track(path)
        path.write_text("fresh", encoding="utf-8")
        manager.commit(changes)

        assert path.exists()
        manager.undo()
        assert not path.exists()

    def test_commit_clears_redo(self, tmp_path: Path):
        path = tmp_path / "f.txt"
        path.write_text("v1", encoding="utf-8")
        manager = SessionManager(tmp_path / "s.json")

        first = ChangeSet("first")
        first.track(path)
        path.write_text("v2", encoding="utf-8")
        manager.commit(first)
        manager.undo()
        assert manager.session.redo_stack

        second = ChangeSet("second")
        second.track(path)
        path.write_text("v3", encoding="utf-8")
        manager.commit(second)
        assert not manager.session.redo_stack

    def test_history_newest_first(self, tmp_path: Path):
        path = tmp_path / "f.txt"
        path.write_text("0", encoding="utf-8")
        manager = SessionManager(tmp_path / "s.json")
        for index in range(3):
            changes = ChangeSet(f"change-{index}")
            changes.track(path)
            path.write_text(str(index), encoding="utf-8")
            manager.commit(changes)
        labels = [row["label"] for row in manager.history()]
        assert labels == ["change-2", "change-1", "change-0"]

    def test_ids_increment(self, tmp_path: Path):
        path = tmp_path / "f.txt"
        path.write_text("0", encoding="utf-8")
        manager = SessionManager(tmp_path / "s.json")
        for index in range(3):
            changes = ChangeSet(f"c{index}")
            changes.track(path)
            path.write_text(str(index), encoding="utf-8")
            manager.commit(changes)
        assert [entry["id"] for entry in manager.session.undo_stack] == [1, 2, 3]

    def test_undo_with_empty_stack_returns_none(self, tmp_path: Path):
        manager = SessionManager(tmp_path / "s.json")
        assert manager.undo() is None
        assert manager.redo() is None

    def test_corrupted_session_raises(self, tmp_path: Path):
        target = tmp_path / "s.json"
        target.write_text("{not json", encoding="utf-8")
        with pytest.raises(SessionError) as excinfo:
            SessionManager(target)
        assert "corrupted" in str(excinfo.value)

    def test_history_bounded(self, tmp_path: Path):
        path = tmp_path / "f.txt"
        path.write_text("0", encoding="utf-8")
        manager = SessionManager(tmp_path / "s.json")
        for index in range(120):
            changes = ChangeSet(f"c{index}")
            changes.track(path)
            path.write_text(str(index), encoding="utf-8")
            manager.commit(changes)
        assert len(manager.session.undo_stack) <= 100

    def test_session_roundtrip(self, tmp_path: Path):
        target = tmp_path / "s.json"
        manager = SessionManager(target)
        manager.session.home_dir = "/a"
        manager.session.controller_url = "http://127.0.0.1:1"
        manager.save()
        restored = SessionManager(target)
        assert restored.session.home_dir == "/a"
        assert restored.session.controller_url == "http://127.0.0.1:1"

    def test_session_model_defaults(self):
        session = Session()
        assert session.home_dir is None
        assert session.undo_stack == []
        assert session.to_dict()["version"] == 1


# ===========================================================================
class TestVerge:
    """``verge.yaml`` schema fidelity against ``IVerge``."""

    def test_schema_types_are_declared(self):
        valid = {"bool", "int", "str", "list", "json"}
        for key, (kind, _default, doc) in VERGE_SCHEMA.items():
            assert kind in valid, f"{key} has unknown type {kind}"
            assert isinstance(doc, str) and doc, f"{key} missing description"

    def test_coerce_bool_accepts_words(self):
        for word in ("true", "TRUE", "yes", "on", "1", "enabled"):
            assert VergeConfig.coerce("enable_tun_mode", word) is True
        for word in ("false", "no", "off", "0", "disabled"):
            assert VergeConfig.coerce("enable_tun_mode", word) is False

    def test_coerce_bool_rejects_garbage(self):
        with pytest.raises(VergeError):
            VergeConfig.coerce("enable_tun_mode", "maybe")

    def test_coerce_int(self):
        assert VergeConfig.coerce("verge_mixed_port", "8080") == 8080

    def test_coerce_int_rejects(self):
        with pytest.raises(VergeError):
            VergeConfig.coerce("verge_mixed_port", "http")

    def test_coerce_list_from_json(self):
        assert VergeConfig.coerce("hotkeys", '["a","b"]') == ["a", "b"]

    def test_coerce_list_from_csv(self):
        assert VergeConfig.coerce("hotkeys", "a, b") == ["a", "b"]

    def test_coerce_json_object(self):
        assert VergeConfig.coerce("theme_setting", '{"a":1}') == {"a": 1}

    def test_coerce_json_rejects_non_object(self):
        with pytest.raises(VergeError):
            VergeConfig.coerce("theme_setting", "[1,2]")

    def test_encrypted_keys_refused(self):
        for key in ENCRYPTED_KEYS:
            with pytest.raises(VergeError) as excinfo:
                VergeConfig.coerce(key, "value")
            assert "encrypted" in str(excinfo.value)

    def test_unknown_key_rejected(self):
        with pytest.raises(VergeError) as excinfo:
            VergeConfig.coerce("not_a_real_key", "1")
        assert "unknown verge key" in str(excinfo.value)

    def test_get_distinguishes_unset_from_default(self, home: Path):
        verge = VergeConfig(paths_for(home))
        is_set, value = verge.get("enable_tun_mode")
        assert is_set is False
        assert value is False  # template default

        verge.set("enable_tun_mode", "true")
        is_set, value = verge.get("enable_tun_mode")
        assert is_set is True
        assert value is True

    def test_set_then_unset(self, home: Path):
        verge = VergeConfig(paths_for(home))
        verge.set("theme_mode", "dark")
        assert verge.get("theme_mode")[1] == "dark"
        removed, old = verge.unset("theme_mode")
        assert removed is True
        assert old == "dark"
        assert verge.get("theme_mode")[0] is False

    def test_unset_missing_key(self, home: Path):
        verge = VergeConfig(paths_for(home))
        assert verge.unset("theme_mode") == (False, None)

    def test_unknown_keys_preserved_on_write(self, home: Path):
        paths = paths_for(home)
        save_yaml(paths.verge_yaml, {"future_key": {"deep": True}})
        VergeConfig(paths).set("theme_mode", "dark")
        data = load_mapping(paths.verge_yaml)
        assert data["future_key"] == {"deep": True}
        assert data["theme_mode"] == "dark"

    def test_validate_detects_type_mismatch(self, home: Path):
        paths = paths_for(home)
        save_yaml(paths.verge_yaml, {"enable_tun_mode": "yes"})
        problems = VergeConfig(paths).validate()
        assert any("enable_tun_mode" in problem for problem in problems)

    def test_validate_clean_file(self, home: Path):
        paths = paths_for(home)
        save_yaml(paths.verge_yaml, {"enable_tun_mode": True, "verge_mixed_port": 7897})
        assert VergeConfig(paths).validate() == []

    def test_unknown_keys_reported(self, home: Path):
        paths = paths_for(home)
        save_yaml(paths.verge_yaml, {"brand_new_key": 1})
        assert "brand_new_key" in VergeConfig(paths).unknown_keys()


# ===========================================================================
class TestClash:
    """``config.yaml`` (``IClashTemp``)."""

    def test_port_range_enforced(self):
        assert ClashConfig.coerce("mixed-port", "8080") == 8080
        for bad in ("0", "70000", "-1"):
            with pytest.raises(ClashError):
                ClashConfig.coerce("mixed-port", bad)

    def test_port_rejects_non_integer(self):
        with pytest.raises(ClashError):
            ClashConfig.coerce("mixed-port", "http")

    def test_mode_enum_enforced(self):
        assert ClashConfig.coerce("mode", "global") == "global"
        with pytest.raises(ClashError):
            ClashConfig.coerce("mode", "turbo")

    def test_log_level_enum_enforced(self):
        assert ClashConfig.coerce("log-level", "debug") == "debug"
        with pytest.raises(ClashError):
            ClashConfig.coerce("log-level", "verbose")

    def test_bool_keys(self):
        assert ClashConfig.coerce("allow-lan", "yes") is True
        assert ClashConfig.coerce("ipv6", "off") is False

    def test_free_form_key_passes_through(self):
        assert ClashConfig.coerce("anything-else", "value") == "value"

    def test_controller_url_default(self, home: Path):
        assert ClashConfig(paths_for(home)).controller_url() == f"http://{DEFAULT_EXTERNAL_CONTROLLER}"

    def test_controller_url_absorbs_scheme(self, home: Path):
        paths = paths_for(home)
        save_yaml(paths.clash_yaml, {"external-controller": "http://127.0.0.1:1234"})
        assert ClashConfig(paths).controller_url() == "http://127.0.0.1:1234"

    def test_controller_url_from_file(self, home: Path):
        paths = paths_for(home)
        save_yaml(paths.clash_yaml, {"external-controller": "127.0.0.1:9999"})
        assert ClashConfig(paths).controller_url() == "http://127.0.0.1:9999"

    def test_secret_default(self, home: Path):
        assert ClashConfig(paths_for(home)).secret() == DEFAULT_SECRET

    def test_secret_from_file(self, home: Path):
        paths = paths_for(home)
        save_yaml(paths.clash_yaml, {"secret": "hunter2"})
        assert ClashConfig(paths).secret() == "hunter2"

    def test_runtime_summary_counts(self, home: Path):
        paths = paths_for(home)
        save_yaml(
            paths.runtime_config,
            {
                "mixed-port": 7897,
                "mode": "global",
                "proxies": [{"name": "a"}, {"name": "b"}],
                "proxy-providers": {"p": {}},
                "rules": [{"type": "MATCH"}],
            },
        )
        summary = RuntimeConfig(paths).summary()
        assert summary["available"] is True
        assert summary["mixed_port"] == 7897
        assert summary["mode"] == "global"
        assert summary["proxy_count"] == 2
        assert summary["provider_count"] == 1
        assert summary["rule_count"] == 1

    def test_runtime_summary_absent(self, home: Path):
        assert RuntimeConfig(paths_for(home)).summary()["available"] is False

    def test_validate_ports(self, home: Path):
        paths = paths_for(home)
        save_yaml(paths.clash_yaml, {"mixed-port": "7897"})
        assert any("mixed-port" in p for p in ClashConfig(paths).validate())

    def test_set_and_unset(self, home: Path):
        clash = ClashConfig(paths_for(home))
        old, new = clash.set("mode", "global")
        assert new == "global"
        assert old == "rule"
        assert clash.unset("mode") == (True, "global")

    def test_mappings_preserved(self, home: Path):
        paths = paths_for(home)
        save_yaml(paths.clash_yaml, {"tun": {"enable": False}})
        ClashConfig(paths).set("mode", "direct")
        assert load_mapping(paths.clash_yaml)["tun"] == {"enable": False}


# ===========================================================================
class TestProfiles:
    """``profiles.yaml`` fidelity against ``IProfiles`` / ``PrfItem``."""

    def test_uid_prefixes_match_type(self):
        for itype, meta in ITEM_TYPES.items():
            assert new_uid(itype).startswith(meta["prefix"])

    def test_uid_is_alphanumeric(self):
        for _ in range(50):
            uid = new_uid("remote")
            assert len(uid) == 12
            assert uid[1:].isalnum() and uid[1:].isascii()

    def test_uid_rejects_unknown_type(self):
        with pytest.raises(ProfileError):
            new_uid("nonsense")

    def test_file_name_extension(self):
        assert file_name_for("sABC", "script") == "sABC.js"
        assert file_name_for("RABC", "remote") == "RABC.yaml"

    def test_add_and_list(self, home: Path):
        profiles = Profiles(paths_for(home))
        profiles.add({"uid": "R1", "type": "remote", "name": "a", "file": "R1.yaml"})
        assert len(profiles.items()) == 1
        assert profiles.by_uid("R1")["name"] == "a"

    def test_duplicate_uid_rejected(self, home: Path):
        profiles = Profiles(paths_for(home))
        profiles.add({"uid": "R1", "type": "remote", "name": "a", "file": "R1.yaml"})
        with pytest.raises(ProfileError):
            profiles.add({"uid": "R1", "type": "remote", "name": "b", "file": "R1b.yaml"})

    def test_remove_deletes_file(self, home: Path):
        paths = paths_for(home)
        profiles = Profiles(paths)
        profiles.add({"uid": "R1", "type": "remote", "name": "a", "file": "R1.yaml"})
        target = paths.profile_file("R1.yaml")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("proxies: []\n", encoding="utf-8")

        removed = profiles.remove("R1")
        assert not target.exists()
        assert removed["removed_files"] == ["R1.yaml"]
        assert profiles.items() == []

    def test_remove_current_promotes_next(self, home: Path):
        profiles = Profiles(paths_for(home))
        profiles.add({"uid": "R1", "type": "remote", "name": "a", "file": "R1.yaml"})
        profiles.add({"uid": "R2", "type": "remote", "name": "b", "file": "R2.yaml"})
        profiles.set_current("R1")
        profiles.remove("R1")
        assert profiles.load()["current"] == "R2"

    def test_remove_last_clears_current(self, home: Path):
        profiles = Profiles(paths_for(home))
        profiles.add({"uid": "R1", "type": "remote", "name": "a", "file": "R1.yaml"})
        profiles.set_current("R1")
        profiles.remove("R1")
        assert profiles.load()["current"] is None

    def test_remove_unknown_uid(self, home: Path):
        with pytest.raises(ProfileError):
            Profiles(paths_for(home)).remove("nope")

    def test_set_current_rejects_companion(self, home: Path):
        profiles = Profiles(paths_for(home))
        profiles.add({"uid": "m1", "type": "merge", "name": "x merge", "file": "m1.yaml"})
        with pytest.raises(ProfileError) as excinfo:
            profiles.set_current("m1")
        assert "not a selectable profile" in str(excinfo.value)

    def test_set_current_unknown(self, home: Path):
        with pytest.raises(ProfileError):
            Profiles(paths_for(home)).set_current("nope")

    def test_main_items_filters_companions(self, home: Path):
        profiles = Profiles(paths_for(home))
        profiles.add({"uid": "R1", "type": "remote", "name": "a", "file": "R1.yaml"})
        profiles.add({"uid": "m1", "type": "merge", "name": "R1 merge", "file": "m1.yaml"})
        assert len(profiles.main_items()) == 1
        assert len(profiles.items()) == 2

    def test_resolve_by_uid_and_name(self, home: Path):
        profiles = Profiles(paths_for(home))
        profiles.add({"uid": "R1", "type": "remote", "name": "My Sub", "file": "R1.yaml"})
        assert profiles.resolve("R1")["uid"] == "R1"
        assert profiles.resolve("my sub")["uid"] == "R1"

    def test_resolve_missing_raises(self, home: Path):
        with pytest.raises(ProfileError) as excinfo:
            Profiles(paths_for(home)).resolve("ghost")
        assert "no profile" in str(excinfo.value)

    def test_resolve_ambiguous_name_raises(self, home: Path):
        profiles = Profiles(paths_for(home))
        profiles.add({"uid": "R1", "type": "remote", "name": "dup", "file": "R1.yaml"})
        profiles.add({"uid": "R2", "type": "remote", "name": "dup", "file": "R2.yaml"})
        with pytest.raises(ProfileError) as excinfo:
            profiles.resolve("dup")
        assert "matches 2" in str(excinfo.value)

    def test_companions_grouping(self, home: Path):
        profiles = Profiles(paths_for(home))
        profiles.add({"uid": "R1", "type": "remote", "name": "a", "file": "R1.yaml"})
        profiles.add({"uid": "m1", "type": "merge", "name": "R1 merge", "file": "m1.yaml"})
        profiles.add({"uid": "m2", "type": "merge", "name": "R2 merge", "file": "m2.yaml"})
        assert [c["uid"] for c in profiles.companions("R1")] == ["m1"]

    def test_validate_missing_file(self, home: Path):
        profiles = Profiles(paths_for(home))
        profiles.add({"uid": "R1", "type": "remote", "name": "a", "file": "R1.yaml"})
        problems = profiles.validate()
        assert any("missing data file" in p for p in problems)

    def test_validate_duplicate_uid(self, home: Path):
        profiles = Profiles(paths_for(home))
        profiles.add({"uid": "R1", "type": "remote", "name": "a", "file": "R1.yaml"})
        profiles.save({"current": "R1", "items": profiles.items() * 2})
        assert any("duplicate uid" in p for p in profiles.validate())

    def test_validate_dangling_current(self, home: Path):
        profiles = Profiles(paths_for(home))
        profiles.save({"current": "ghost", "items": []})
        assert any("unknown uid" in p for p in profiles.validate())

    def test_update_item(self, home: Path):
        profiles = Profiles(paths_for(home))
        profiles.add({"uid": "R1", "type": "remote", "name": "a", "file": "R1.yaml"})
        profiles.update_item("R1", {"name": "renamed"})
        assert profiles.by_uid("R1")["name"] == "renamed"

    def test_update_item_unknown(self, home: Path):
        with pytest.raises(ProfileError):
            Profiles(paths_for(home)).update_item("ghost", {"name": "x"})

    def test_roundtrip_with_real_installation_shape(self, populated_home: Path):
        profiles = Profiles(paths_for(populated_home))
        current = profiles.current()
        assert current["uid"] == "RAAA1111111"
        assert current["type"] == "remote"
        assert profiles.file_exists(current)


# ===========================================================================
class TestSubscription:
    """Profile payload validation — Clash Verge's own acceptance gate."""

    def test_subscription_userinfo_parsed(self):
        parsed = parse_subscription_userinfo(
            "upload=1024; download=2048; total=10737418240; expire=1735689600"
        )
        assert parsed == {
            "upload": 1024,
            "download": 2048,
            "total": 10737418240,
            "expire": 1735689600,
        }

    def test_subscription_userinfo_missing(self):
        assert parse_subscription_userinfo(None) == {}

    def test_update_interval_hours_to_minutes(self):
        assert parse_update_interval("24") == 1440
        assert parse_update_interval("0.5") == 30

    def test_update_interval_garbage(self):
        assert parse_update_interval("soon") is None
        assert parse_update_interval("0") is None

    def test_payload_requires_proxies_or_providers(self):
        with pytest.raises(SubscriptionError) as excinfo:
            validate_payload("rules:\n- MATCH,DIRECT\n")
        assert "proxy-providers" in str(excinfo.value)

    def test_payload_accepts_proxies(self):
        assert validate_payload("proxies:\n- name: a\n  type: socks5\n") == {
            "proxies": [{"name": "a", "type": "socks5"}]
        }

    def test_payload_accepts_proxy_providers(self):
        data = validate_payload("proxy-providers:\n  p:\n    type: http\n")
        assert "proxy-providers" in data

    def test_payload_rejects_invalid_yaml(self):
        with pytest.raises(SubscriptionError):
            validate_payload("key: [unclosed")

    def test_payload_rejects_non_mapping(self):
        with pytest.raises(SubscriptionError):
            validate_payload("- a\n- b\n")

    def test_empty_payload_rejected(self):
        for empty in ("", "   \n"):
            with pytest.raises(SubscriptionError):
                validate_payload(empty)

    def test_read_local(self, tmp_path: Path):
        path = tmp_path / "sub.yaml"
        path.write_text(
            "proxies:\n- name: a\n  type: socks5\nrules:\n- MATCH,DIRECT\n",
            encoding="utf-8",
        )
        result = read_local(path)
        assert result["summary"]["proxy_count"] == 1
        assert result["summary"]["rule_count"] == 1
        assert result["suggested_name"] == "sub"

    def test_read_local_missing(self, tmp_path: Path):
        with pytest.raises(SubscriptionError):
            read_local(tmp_path / "absent.yaml")


# ===========================================================================
class TestController:
    """Live Mihomo controller client, against a real loopback HTTP server."""

    def test_version(self, mock_controller_server):
        client = MihomoController(mock_controller_server, MOCK_SECRET)
        assert "mihomo" in client.version()["version"]

    def test_missing_auth_returns_401(self, mock_controller_server):
        client = MihomoController(mock_controller_server, "wrong-secret")
        with pytest.raises(ControllerError) as excinfo:
            client.version()
        assert "401" in str(excinfo.value)

    def test_probe_reachable(self, mock_controller_server):
        probe = MihomoController(mock_controller_server, MOCK_SECRET).probe()
        assert probe["reachable"] is True
        assert probe["latency_ms"] >= 0

    def test_probe_unreachable(self):
        client = MihomoController("http://127.0.0.1:1", MOCK_SECRET, timeout=1.0)
        probe = client.probe()
        assert probe["reachable"] is False
        assert probe["error"]

    def test_groups_filters_selectable(self, mock_controller_server):
        groups = MihomoController(mock_controller_server, MOCK_SECRET).groups()
        assert set(groups) == {"GLOBAL", "PROXY"}

    def test_nodes_of_group(self, mock_controller_server):
        nodes = MihomoController(mock_controller_server, MOCK_SECRET).nodes("GLOBAL")
        assert [node["name"] for node in nodes] == ["node-a", "node-b"]

    def test_nodes_all_excludes_groups(self, mock_controller_server):
        nodes = MihomoController(mock_controller_server, MOCK_SECRET).nodes()
        assert "GLOBAL" not in [node["name"] for node in nodes]

    def test_nodes_unknown_group(self, mock_controller_server):
        with pytest.raises(ControllerError):
            MihomoController(mock_controller_server, MOCK_SECRET).nodes("ghost")

    def test_select_switches_node(self, mock_controller_server):
        client = MihomoController(mock_controller_server, MOCK_SECRET)
        client.select("PROXY", "node-a")
        assert client.current_of("PROXY") == "node-a"

    def test_select_rejects_non_member(self, mock_controller_server):
        client = MihomoController(mock_controller_server, MOCK_SECRET)
        with pytest.raises(ControllerError) as excinfo:
            client.select("PROXY", "not-a-node")
        assert "not a member" in str(excinfo.value)

    def test_select_rejects_non_selectable(self, mock_controller_server):
        client = MihomoController(mock_controller_server, MOCK_SECRET)
        with pytest.raises(ControllerError) as excinfo:
            client.select("node-a", "node-b")
        assert "not selectable" in str(excinfo.value)

    def test_delay(self, mock_controller_server):
        result = MihomoController(mock_controller_server, MOCK_SECRET).delay("node-a")
        assert result["delay"] == 42

    def test_group_delay(self, mock_controller_server):
        result = MihomoController(mock_controller_server, MOCK_SECRET).group_delay("PROXY")
        assert "node-a" in result

    def test_mode_roundtrip(self, mock_controller_server):
        client = MihomoController(mock_controller_server, MOCK_SECRET)
        client.set_mode("global")
        assert client.mode() == "global"

    def test_set_mode_rejects_invalid(self, mock_controller_server):
        with pytest.raises(ControllerError):
            MihomoController(mock_controller_server, MOCK_SECRET).set_mode("turbo")

    def test_connections(self, mock_controller_server):
        data = MihomoController(mock_controller_server, MOCK_SECRET).connections()
        assert data["connections"][0]["id"] == "conn-1"
        assert data["downloadTotal"] == 4096

    def test_close_connection(self, mock_controller_server):
        client = MihomoController(mock_controller_server, MOCK_SECRET)
        assert client.close_connection("conn-1") == {}
        assert client.close_connections() == {}

    def test_rules(self, mock_controller_server):
        rules = MihomoController(mock_controller_server, MOCK_SECRET).rules()
        assert rules[0]["type"] == "DOMAIN-SUFFIX"

    def test_providers(self, mock_controller_server):
        client = MihomoController(mock_controller_server, MOCK_SECRET)
        assert "provider-one" in client.providers()
        assert client.update_provider("provider-one") == {}

    def test_patch_configs(self, mock_controller_server):
        client = MihomoController(mock_controller_server, MOCK_SECRET)
        client.patch_configs({"mode": "direct"})
        assert client.configs()["mode"] == "direct"

    def test_url_quoting(self, mock_controller_server):
        """Group names with spaces must be percent-encoded, not break the path."""
        from cli_anything.clash_verge.core.controller import _quote

        assert "/" not in _quote("a/b")
        assert _quote("a b") == "a%20b"

    def test_stream_fails_loudly_when_unusable(self, mock_controller_server):
        """WebSocket streams never degrade to "no output".

        The mock server has no WS endpoints, so opening one must raise. With
        the optional dependency missing the error names the missing package;
        with it present the error names the failed handshake. Either way the
        caller is told instead of receiving an empty stream.
        """
        client = MihomoController(mock_controller_server, MOCK_SECRET)
        with pytest.raises(ControllerError) as excinfo:
            list(client.traffic(duration=0.5))
        message = str(excinfo.value)
        assert "websocket-client" in message or "cannot open" in message


# ===========================================================================
class TestProcess:
    """Process detection helpers."""

    def test_find_processes_shape(self, monkeypatch: pytest.MonkeyPatch):
        rows = process_module.find_processes()
        assert isinstance(rows, dict)
        assert "gui" in rows and "core" in rows

    def test_tail_log_limits_lines(self, tmp_path: Path):
        log = tmp_path / "latest.log"
        log.write_text("\n".join(f"line-{i}" for i in range(100)), encoding="utf-8")
        lines = process_module.tail_log(log, 5)
        assert lines == [f"line-{i}" for i in range(95, 100)]

    def test_tail_log_missing_file(self, tmp_path: Path):
        assert process_module.tail_log(tmp_path / "absent.log") == []

    def test_log_files_sorted_newest_first(self, tmp_path: Path):
        for name, age in (("old.log", 1000), ("new.log", 2000)):
            path = tmp_path / name
            path.write_text("x", encoding="utf-8")
            os.utime(path, (age, age))
        assert [p.name for p in process_module.log_files(tmp_path)] == ["new.log", "old.log"]

    def test_log_files_empty_dir(self, tmp_path: Path):
        assert process_module.log_files(tmp_path) == []


# ===========================================================================
class TestReplSkinTable:
    """Regression guard for the rendering layer.

    Many commands call ``ReplSkin.table(..., title=...)``. Before ``title``
    existed as a parameter every one of them died with a TypeError — and the
    suite stayed green because tests only ever invoked the CLI with ``--json``,
    which bypasses table rendering entirely.
    """

    @pytest.fixture
    def skin(self):
        from cli_anything.clash_verge.utils.repl_skin import ReplSkin

        return ReplSkin("clash-verge", version="1.0.0")

    def test_table_accepts_title(self, skin, capsys):
        skin.table(
            ["Group", "Selected"],
            [["GLOBAL", "DIRECT"]],
            title="Current selection",
        )
        out = capsys.readouterr().out
        assert "Current selection" in out
        assert "GLOBAL" in out
        assert "DIRECT" in out

    def test_table_without_title_still_renders(self, skin, capsys):
        skin.table(["Header"], [["cell"]])
        out = capsys.readouterr().out
        assert "Header" in out
        assert "cell" in out

    def test_table_accepts_full_signature(self, skin, capsys):
        """Cover every keyword the CLI passes, together."""
        skin.table(["h"], [["r"]], max_col_width=10, title="T")
        out = capsys.readouterr().out
        assert "T" in out
        assert "h" in out
        assert "r" in out

    def test_table_truncates_to_max_col_width(self, skin, capsys):
        skin.table(["h"], [["x" * 50]], max_col_width=8)
        out = capsys.readouterr().out
        assert "x" * 8 in out
        assert "x" * 9 not in out
