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

---

# Task 2 Fix: NAME_RE Regex Tightening (kebab-case validation)

**Status:** COMPLETE
**Commit:** 8b88b28
**Branch:** feat/memory-mcp

## Summary

Fixed `NAME_RE` regex to properly reject trailing/leading hyphens in card names, enforcing strict kebab-case semantics.

## Changes

| File | Change |
|------|--------|
| `mcp/memory/katana_memory_mcp/store.py` | Updated `NAME_RE` from `r"[a-z0-9][a-z0-9-]*"` to `r"[a-z0-9]([a-z0-9-]*[a-z0-9])?"` and moved to module constants (line 10, alongside `ID_RE`, `STATUSES`, `TYPES`) |
| `mcp/memory/tests/test_store.py` | Added 3 new assertions in `test_create_rejects_bad_name_and_type`: reject `"bad-"`, reject `"-bad"`, allow single char `"a"` |

## Test Results

```
18 passed in 0.04s
  ✓ test_parse_card_extracts_canonical_fields
  ✓ test_parse_card_no_frontmatter_returns_none
  ✓ test_parse_card_missing_optional_fields
  ✓ test_serialize_roundtrip_canonical_order
  ✓ test_serialize_quotes_risky_scalars
  ✓ test_gen_id_format_and_collision
  ✓ test_scalar_risky_leading_chars
  ✓ test_serialize_body_trailing_newlines_roundtrip
  ✓ test_parse_card_fence_not_confused_by_partial_fence
  ✓ test_gen_id_deterministic_collision
  ✓ test_create_writes_file_and_returns_id
  ✓ test_create_rejects_duplicate_name
  ✓ test_create_rejects_bad_name_and_type (now includes hyphen edge cases)
  ✓ test_list_and_get
  ✓ test_list_skips_unparseable_and_id_less
  ✓ test_update_fields_and_rename
  ✓ test_update_rejects_bad_status
  ✓ test_delete
```

## Validation Details

The new regex `r"[a-z0-9]([a-z0-9-]*[a-z0-9])?"` enforces:
- Starts with `[a-z0-9]` (required)
- Middle can contain `[a-z0-9-]` (optional, zero or more)
- Ends with `[a-z0-9]` (required if length > 1)
- Single characters are valid (the `?` makes the middle+end group optional)

Examples:
- ✓ `"a"`, `"z"`, `"0"` (single chars)
- ✓ `"my-card"`, `"x-y"` (proper kebab-case)
- ✗ `"bad-"`, `"-bad"` (now rejected)
- ✗ `"-"` (only hyphen)
