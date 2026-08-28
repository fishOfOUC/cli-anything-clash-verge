"""Profile templates lifted verbatim from ``src-tauri/src/utils/tmpl.rs``.

Clash Verge creates five companion items (merge / script / rules / proxies /
groups) alongside every real profile. Importing a profile without them would
leave the built-in enhancement pipeline looking up names that do not exist, so
``profile import`` and ``profile create`` write these exact templates.
"""

ITEM_LOCAL = """# Profile Template for Clash Verge

proxies: []

proxy-groups: []

rules: []
"""

ITEM_MERGE = """# Profile Enhancement Merge Template for Clash Verge

profile:
  store-selected: true
"""

ITEM_MERGE_EMPTY = """# Profile Enhancement Merge Template for Clash Verge

"""

ITEM_SCRIPT = """// Define main function (script entry)

function main(config, profileName) {
  return config;
}
"""

ITEM_RULES = """# Profile Enhancement Rules Template for Clash Verge

prepend: []

append: []

delete: []
"""

ITEM_PROXIES = """# Profile Enhancement Proxies Template for Clash Verge

prepend: []

append: []

delete: []
"""

ITEM_GROUPS = """# Profile Enhancement Groups Template for Clash Verge

prepend: []

append: []

delete: []
"""

#: Companion templates keyed by their ``type`` in ``profiles.yaml``.
COMPANION_TEMPLATES = {
    "merge": ITEM_MERGE_EMPTY,
    "script": ITEM_SCRIPT,
    "rules": ITEM_RULES,
    "proxies": ITEM_PROXIES,
    "groups": ITEM_GROUPS,
}
