# katana

A sharp little toolkit of agent plugins. Each plugin is independently
installable — take only what you need.

| Plugin | What it does |
|--------|--------------|
| `guide` | Toolkit map + composition guide (`using-katana`), injected at session start |
| `work-folder` | Work-folder convention (injected each session) + `checkpoint` skill for cross-session save/resume |
| `deep-research` | Workflow-orchestrated research over your knowledge base + web; judgment-driven stop; cited report |
| `memory` | Verified facts as memory cards, auto-injected as an L1 index each session |

## Install (Claude Code)

```
/plugin marketplace add Dandi007/katana
/plugin install guide@katana
/plugin install work-folder@katana
/plugin install deep-research@katana
/plugin install memory@katana
```

Enable/disable any plugin independently with `/plugin`.

## Install (Codex)

SKILL.md files follow the [Agent Skills](https://agentskills.io) open standard,
which Codex supports natively (source:
[agentskills.io client listing](https://agentskills.io),
[developers.openai.com/codex/skills](https://developers.openai.com/codex/skills/)).

Codex discovers skills from `.agents/skills/` in your repo root (and parent
directories up to the repo root), as well as `$HOME/.agents/skills` for
user-level installs.

**Project-level install** (skills available in this repo only):

```bash
# Clone katana and symlink the skills you want into your repo's .agents/skills/
git clone https://github.com/Dandi007/katana /tmp/katana

mkdir -p .agents/skills
cp -r /tmp/katana/plugins/guide/skills/using-katana         .agents/skills/
cp -r /tmp/katana/plugins/work-folder/skills/checkpoint     .agents/skills/
cp -r /tmp/katana/plugins/deep-research/skills/deep-research .agents/skills/
cp -r /tmp/katana/plugins/memory/skills/remember            .agents/skills/
cp -r /tmp/katana/plugins/memory/skills/validate            .agents/skills/
```

**User-level install** (skills available across all repos):

```bash
git clone https://github.com/Dandi007/katana /tmp/katana

mkdir -p "$HOME/.agents/skills"
cp -r /tmp/katana/plugins/guide/skills/using-katana         "$HOME/.agents/skills/"
cp -r /tmp/katana/plugins/work-folder/skills/checkpoint     "$HOME/.agents/skills/"
cp -r /tmp/katana/plugins/deep-research/skills/deep-research "$HOME/.agents/skills/"
cp -r /tmp/katana/plugins/memory/skills/remember            "$HOME/.agents/skills/"
cp -r /tmp/katana/plugins/memory/skills/validate            "$HOME/.agents/skills/"
```

Claude Code-specific features degrade gracefully elsewhere:

- `guide` / `work-folder` context injection and `memory` index injection use
  Claude Code SessionStart hooks. On Codex, paste the work-folder convention
  (`plugins/work-folder/rules/work-folder.md`) into your project's AGENTS.md.
- `deep-research` orchestration uses Claude Code's Workflow tool; other tools
  can follow the SKILL.md flow manually.

## Configuration

Katana plugins support three-tier configuration (highest priority first):

1. **Environment variables** — machine-level overrides
2. **`.katana` file** — project-level configuration (committed to repo)
3. **Default values** — sensible defaults for most projects

### `.katana` file

Create a `.katana` file in your project root to customize plugin behavior:

```bash
# Katana plugin configuration
# Priority: environment variables > this file > plugin defaults

# work-folder: override the default work folder path
work_folder_path=智元工作/工作记录

# memory: override the project memory directory
memory_project_dir=memory

# deep-research: override the knowledge base root directory
deep_research_kb_dir=.
```

The file uses simple `key=value` format. Lines starting with `#` are comments.

### Configuration options

| Plugin | Key | Env var | Default | Description |
|--------|-----|---------|---------|-------------|
| work-folder | `work_folder_path` | `KATANA_WORK_FOLDER` | `docs/work-records` | Work folder base path |
| memory | `memory_project_dir` | `CLAUDE_MEMORY_PROJECT_DIR` | `memory` | Project memory directory |
| memory | — | `CLAUDE_MEMORY_SYSTEM_DIR` | `~/.claude/memory` | System memory directory |
| deep-research | `deep_research_kb_dir` | `DEEP_RESEARCH_KB_DIR` | current directory | Knowledge base root |

**Note:** System-level memory directory only supports environment variables (machine dimension, not project-specific).

### Example

For a Chinese knowledge base project:

```bash
# .katana
work_folder_path=智元工作/工作记录
memory_project_dir=memory
deep_research_kb_dir=.
```

This tells katana to use `智元工作/工作记录/YYYY/MM/DD/<topic>/` for work folders instead of the default `docs/work-records/...`.

## memory binary resolution

On session start the memory hook resolves its Rust scanner in order:
cached binary in `bin/` or `target/release/` (version-matched) → prebuilt download from GitHub
Releases (macos-arm64, sha256-verified) → local `cargo build` → graceful
skip (never blocks the session).

## Releasing (maintainer)

1. Bump `version` in the plugin's `plugin.json` (and `Cargo.toml` for memory — keep them equal).
2. Tag `v<version>` and push; CI uploads `claude-memory-scan-macos-arm64` + `checksums.txt`.

## License

MIT
