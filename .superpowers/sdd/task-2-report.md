# Task 2 Report: store CRUD（list/get/create/update/delete）

**Status:** DONE
**Commit:** c9f1ea6
**Branch:** feat/memory-mcp

## Test Summary

```
18 passed in 0.02s
  - 10 existing Task 1 tests (all green)
  - 8 new Task 2 tests (all green)
```

## Implementation

| File | Action | Details |
|---|---|---|
| `mcp/memory/katana_memory_mcp/store.py` | Modified | Added: `list_cards`, `get_card`, `create_card`, `update_card`, `delete_card` + helpers (`_today`, `_read`, `_scan`, `_l1`, `_find`, `_validate`, `NAME_RE`) |
| `mcp/memory/tests/test_store.py` | Modified | Added: 8 new CRUD tests (create, list, get, update, delete validation) |
| `mcp/memory/tests/conftest.py` | Created | Pytest fixtures: `tenant_dir`, `seeded` with 2 pre-created cards |

## Test Coverage

- **create_card**: writes file, returns id with changed_paths, rejects duplicate names, validates kebab-case name + type
- **list_cards**: returns cards list + skipped paths for unparseable/id-less files
- **get_card**: retrieves full metadata + body + path, returns None for missing id
- **update_card**: updates fields, renames file on name change, tracks old+new paths in changed_paths, validates status/type
- **delete_card**: removes file, returns deleted metadata with changed_paths, raises KeyError for missing

## Concerns

None. All Task 1 tests remain green. TDD flow completed as specified (write tests → confirm fail → implement → confirm pass → commit).
