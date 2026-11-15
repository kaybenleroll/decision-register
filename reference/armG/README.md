# arm G reference snapshot

Read-only snapshot for porting; not a working package. Paths and imports (e.g. `../lib`, sibling `tests/` and `stub_*` modules) assume the original layout.

- Source repo: random_llm_projects/claude_code_research, HEAD 01d33be147a31ac331679236ac5414bcf25649af
- Source paths (under artifacts/seq-thinking-study/harness/): readback/{register_server,register_store,hook_common,hook_register_*,render_tree,tree_lib}.py, readback/settings.hooks.G{,-lite}.template.json, lib/common.py, tests/test_all.py (covers register code among other arms), stub_register.py, stub_items.py (-> tests/ here).
- Included beyond the request: render_tree.py, tree_lib.py (imported by hook_common.py), lib/common.py (imported by render_tree.py), stub_register.py and stub_items.py (offline stub that drives the hooks and server).

Line counts (wc -l):

        73 hook_common.py
        36 hook_register_common.py
        22 hook_register_sessionstart.py
        59 hook_register_stop.py
        39 hook_register_userprompt.py
       101 register_server.py
       922 register_store.py
        71 render_tree.py
       158 tree_lib.py
        13 settings.hooks.G-lite.template.json
        13 settings.hooks.G.template.json
       194 lib/common.py
       137 tests/stub_items.py
       263 tests/stub_register.py
      2133 tests/test_all.py
      4234 total
