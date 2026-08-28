# TEST.md — cli-anything-clash-verge

Unit tests and real end-to-end tests for the Clash Verge harness.

Unit tests live in `test_core.py`. End-to-end tests live in
`test_full_e2e.py` and invoke the real CLI through Click's `CliRunner`; a
throwaway HTTP server stands in for the mihomo controller so the live
command surface is exercised over real sockets rather than mocks.

```bash
cd clash-verge/agent-harness

# the whole suite (fast, no Clash Verge install needed)
python -m pytest cli_anything/clash_verge/tests/ -v

# unit tests only
python -m pytest cli_anything/clash_verge/tests/test_core.py -v

# end-to-end only
python -m pytest cli_anything/clash_verge/tests/test_full_e2e.py -v
```

## Unit tests (`test_core.py`)

### Path resolution (`test_paths.py` group)
Verifies `paths.py` reproduces `src-tauri/src/utils/dirs.rs`.

| Test | What it proves |
|------|----------------|
| `test_default_home_dir_uses_app_id` | Home ends with `io.github.clash-verge-rev.clash-verge-rev` |
| `test_dev_home_dir_uses_dev_suffix` | Dev build gets the `.dev` app id |
| `test_resolve_explicit_existing` | An explicit existing directory is used verbatim |
| `test_resolve_explicit_missing_raises` | A missing explicit directory raises `ClashVergeNotFound`, never falls back silently |
| `test_candidates_include_release_and_dev` | Auto-detection considers both layouts |
| `test_layout_classification` | `release` / `dev` / `portable` / `custom` are distinguished |
| `test_paths_file_names` | `config.yaml`, `verge.yaml`, `profiles.yaml`, `clash-verge.yaml` names are exact |
| `test_profile_file_joins_profiles_dir` | Item files resolve under `profiles/` |

### YAML persistence (`test_yamlio.py` group)
Guards the round-trip contract: a CLI edit must never lose data.

| Test | What it proves |
|------|----------------|
| `test_load_missing_returns_default` | Missing file → default, no exception |
| `test_load_empty_returns_default` | Empty file → default |
| `test_roundtrip_preserves_unknown_keys` | Keys this CLI does not know survive a write |
| `test_header_written` | `# Clash Verge Config` header is prepended, as Clash Verge does |
| `test_backup_returns_previous_content` | `make_backup` captures prior bytes for undo |
| `test_tolerant_loader_handles_unknown_tag` | Non-standard YAML tags degrade instead of aborting |
| `test_non_mapping_raises` | A scalar document raises `YamlError` rather than being replaced |
| `test_invalid_yaml_raises` | Corrupt file fails loudly |

### Session and undo (`test_session.py` group)
The undo stack is what makes agent-driven editing safe.

| Test | What it proves |
|------|----------------|
| `test_locked_save_creates_file` | Session persists as valid JSON |
| `test_stale_lock_reclaimed` | A lock older than 60 s is reclaimed, so a killed process cannot wedge the session |
| `test_changeset_captures_before_and_after` | Both sides of a mutation are recorded |
| `test_undo_restores_previous_content` | Undo restores exact prior bytes |
| `test_redo_reapplies` | Redo restores the post-mutation bytes |
| `test_undo_deletes_created_file` | Undoing a creation removes the file (before = None) |
| `test_commit_clears_redo` | A new change invalidates the redo branch |
| `test_history_newest_first` | History ordering |
| `test_corrupted_session_raises` | Corrupt session JSON raises `SessionError` with a clear message |
| `test_history_cap` | Stack is bounded |

### verge.yaml (`test_verge.py` group)
Schema fidelity against `IVerge`.

| Test | What it proves |
|------|----------------|
| `test_schema_types_are_declared` | Every schema entry has a known type |
| `test_coerce_bool_accepts_words` | `yes/on/1/true` → `True` |
| `test_coerce_bool_rejects_garbage` | Invalid boolean raises `VergeError` |
| `test_coerce_int` / `test_coerce_int_rejects` | Integer parsing and failure |
| `test_coerce_list_json_and_csv` | Both JSON arrays and comma lists work |
| `test_encrypted_keys_refused` | WebDAV keys cannot be written (they are stored encrypted) |
| `test_unknown_key_rejected` | Typos fail with a pointer to `verge list` |
| `test_get_distinguishes_unset_from_default` | Absent vs. present-with-default is distinguishable |
| `test_set_then_unset` | Write then removal |
| `test_unknown_keys_preserved_on_write` | Forward compatibility |
| `test_validate_detects_type_mismatch` | Bad types are reported, not silently accepted |

### config.yaml (`test_clash.py` group)

| Test | What it proves |
|------|----------------|
| `test_port_range_enforced` | Ports must be 1–65535 |
| `test_port_rejects_non_integer` | Non-numeric port rejected |
| `test_mode_enum_enforced` | Only `rule`/`global`/`direct` |
| `test_log_level_enum_enforced` | Only the five valid levels |
| `test_controller_url_default` | Falls back to `127.0.0.1:9097` |
| `test_controller_url_absorbs_scheme` | A value with `http://` is not double-prefixed |
| `test_secret_default` | Falls back to `set-your-secret` |
| `test_runtime_summary_counts` | `clash-verge.yaml` is parsed for ports/mode/counts |
| `test_validate_ports` | Non-integer port flagged |

### Profiles (`test_profiles.py` group)
Fidelity against `IProfiles` / `PrfItem`.

| Test | What it proves |
|------|----------------|
| `test_uid_prefixes_match_type` | `R`emote / `L`ocal / `s`cript / `m`erge / `r`ules / `p`roxies / `g`roups |
| `test_uid_is_alphanumeric` | Matches Clash Verge's profile-file regex `[RLmrpg][a-zA-Z0-9]+` |
| `test_file_name_extension` | Scripts get `.js`, everything else `.yaml` |
| `test_add_and_list` | Items persist |
| `test_remove_deletes_file` | Removing a profile removes its data file |
| `test_remove_current_promotes_next` | `current` never points at a deleted uid |
| `test_set_current_rejects_companion` | A merge/script item cannot be selected |
| `test_resolve_by_uid_and_name` | Both selectors work |
| `test_resolve_ambiguous_name_raises` | Ambiguous names fail instead of silently picking one |
| `test_companions_grouping` | Companion lookup by uid prefix |
| `test_validate_missing_file` | A dangling `file` reference is reported |
| `test_validate_duplicate_uid` | Duplicate uids are reported |
| `test_duplicate_uid_rejected_on_add` | Cannot add the same uid twice |

### Subscription handling (`test_subscription.py` group)

| Test | What it proves |
|------|----------------|
| `test_subscription_userinfo_parsed` | `upload/download/total/expire` extracted |
| `test_update_interval_hours_to_minutes` | Header hours → `option.update_interval` minutes |
| `test_payload_requires_proxies_or_providers` | Same acceptance gate Clash Verge applies |
| `test_payload_accepts_proxy_providers` | Provider-only configs are valid |
| `test_payload_rejects_invalid_yaml` | Garbage rejected |
| `test_payload_rejects_non_mapping` | A list document is rejected |
| `test_empty_payload_rejected` | Empty body rejected |
| `test_read_local` | Local file read and validated |

### Controller (`test_controller.py` group, mock HTTP server)

| Test | What it proves |
|------|----------------|
| `test_version` | `GET /version` decoded |
| `test_missing_auth_returns_401` | A wrong secret surfaces as a 401 `ControllerError`, not a generic failure |
| `test_groups_filters_selectable` | Only selectable groups are returned |
| `test_select_switches_node` | `PUT /proxies/:name` sets `now` |
| `test_select_rejects_non_member` | Choosing a node outside the group fails with the member list |
| `test_select_rejects_non_selectable` | A leaf proxy cannot be "selected" |
| `test_delay` / `test_group_delay` | Latency endpoints |
| `test_mode_roundtrip` | `PATCH /configs` changes mode |
| `test_set_mode_rejects_invalid` | Invalid mode rejected client-side |
| `test_connections_and_close` | List / close one / close all |
| `test_rules` | Rule decoding |
| `test_providers` | Provider listing and update |
| `test_probe_unreachable` | Unreachable host returns `reachable: False` instead of raising |

### Process (`test_process.py` group)

| Test | What it proves |
|------|----------------|
| `test_find_processes_shape` | Always returns `gui`/`core` lists |
| `test_tail_log_limits_lines` | Tail returns at most N lines |
| `test_tail_log_missing_file` | Missing log is empty, not an error |
| `test_log_files_sorted_newest_first` | Ordering |

## End-to-end tests (`test_full_e2e.py`)

Every test drives the real CLI in-process and asserts on observable
outcomes: exit code, emitted JSON, and the bytes on disk.

### CLI surface
| Test | What it proves |
|------|----------------|
| `test_version_flag` | `--version` reports the package version |
| `test_help_lists_command_groups` | All top-level groups are advertised |
| `test_json_before_and_after_subcommand` | `--json` is accepted in both positions |
| `test_json_as_option_value_still_works` | A literal `--json` value is not eaten by the hoisting logic |

### Environment
| `test_env_paths_lists_candidates` | Path discovery output is complete and JSON-valid |
| `test_env_info_reads_runtime_config` | `clash-verge.yaml` is picked up when present |
| `test_env_doctor_reports_missing_home` | Missing state files surface as `fail` |

### Profiles
| `test_profile_create_then_list` | A profile appears in `profile list` and is auto-selected |
| `test_profile_create_writes_companions` | Five companion files are created with Clash Verge's templates |
| `test_profile_created_file_is_native_shape` | `profiles.yaml` matches Clash Verge's schema exactly |
| `test_profile_import_local` | Local import validates and stores the payload |
| `test_profile_import_rejects_non_profile` | A YAML without proxies/providers is refused |
| `test_profile_select_and_current` | Selection changes `current` |
| `test_profile_rename` | Rename persists |
| `test_profile_delete_removes_files` | Data file and companions are removed |
| `test_profile_export` | Export copies bytes out |
| `test_profile_file_get_roundtrip` | Raw read and validated write |

### Settings
| `test_verge_set_get_unset` | Full write/read/remove cycle |
| `test_verge_set_rejects_unknown_key` | Typos fail with guidance |
| `test_verge_set_rejects_encrypted_key` | WebDAV keys protected |
| `test_clash_set_port_and_get` | Port coercion and persistence |
| `test_clash_set_rejects_bad_mode` | Enum enforced through the CLI |
| `test_unknown_keys_preserved_end_to_end` | A write does not drop keys added by another tool |

### Controller
| `test_controller_enable_writes_verge` | `enable` flips the flag and reports the transition |
| `test_controller_status_reports_disabled` | Disabled state is explicit |
| `test_proxy_groups_against_controller` | Live group listing over real HTTP |
| `test_proxy_select_against_controller` | Node switching over real HTTP |
| `test_mode_set_against_controller` | Live mode change |
| `test_conn_list_against_controller` | Connection listing |
| `test_live_command_fails_clearly_when_disabled` | Disabled controller produces actionable guidance |

### Safety
| `test_write_blocked_while_app_running` | The running-app guard refuses edits without `--force` |
| `test_force_overrides_guard` | `--force` writes anyway |
| `test_session_undo_restores_config` | Undo reverts a CLI write on disk |
| `test_session_redo_reapplies` | Redo re-applies it |
| `test_no_temp_files_left_behind` | Atomic writes leave no `.tmp` residue |

## Test strategy

1. **Real behaviour over mocks.** The controller tests run a real HTTP server
   on a loopback port, so URL construction, header auth, query encoding and
   JSON decoding are all genuinely exercised. Mocks are used only to pin down
   failures that are hard to trigger for real (stale lock files, unreachable
   hosts).
2. **Isolation.** An autouse fixture redirects the session file and the
   Clash Verge home directory into `tmp_path`, and pins process detection to
   "not running". No test can touch a real installation.
3. **Fidelity assertions.** Several tests assert on the exact bytes written to
   `profiles.yaml` / `verge.yaml`, because the whole point of this harness is
   that Clash Verge can read what the CLI writes.
4. **Failure paths are tested.** Rejected input, ambiguous selectors, missing
   files, blocked writes and disabled controllers each have a test; a CLI that
   only works on the happy path is not agent-safe.

## Environment

No Clash Verge installation and no network access are required — every test
runs against temporary directories and a loopback HTTP server.

Optional dependency for the WebSocket streaming commands (`/traffic`,
`/logs`): `pip install websocket-client`. Those endpoints are not covered by
the suite because they are long-lived streams; everything else is.
