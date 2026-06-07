/**
 * katana OpenCode parity adapter
 *
 * Thin event adapter: maps OpenCode events to Claude Code hook semantics and
 * spawns the SAME plugins/hooks scripts that Claude Code uses. There is
 * only ONE hook implementation; this file owns zero hook logic.
 *
 * Verified event surface (official opencode 1.16.2, 2026-06-08 spike):
 *   - lifecycle events (session.created/idle) arrive via generic `event` handler
 *   - real hook keys: tool.execute.before/after, chat.message, chat.params
 *
 * Contracts: parity/contracts/*.yaml
 */
import { spawn } from 'node:child_process';
import * as fs from 'node:fs';
import * as os from 'node:os';
import * as path from 'node:path';
import { fileURLToPath } from 'node:url';

// ---------- root / table resolution ----------

function repoRoot(): string {
  const env = process.env.KATANA_PARITY_ROOT;
  if (env && env.trim()) return env.trim();
  // this file: <root>/parity/adapter/opencode/index.ts
  const here = path.dirname(fileURLToPath(import.meta.url));
  return path.resolve(here, '..', '..', '..');
}

const ROOT = repoRoot();
const TABLE_PATH = path.join(path.dirname(fileURLToPath(import.meta.url)), 'table.json');

interface TableEntry {
  plugin: string;
  script: string;
  matcher: string;
  interpreter?: string;
}

interface Table {
  sessionStart: TableEntry[];
  postToolUse: TableEntry[];
}

const TABLE: Table = JSON.parse(fs.readFileSync(TABLE_PATH, 'utf8'));

// contracts/_meta.yaml tool_name_map
const TOOL_NAME_MAP: Record<string, string> = {
  bash: 'Bash',
  write: 'Write',
  edit: 'Edit',
  read: 'Read',
  glob: 'Glob',
  grep: 'Grep',
  todowrite: 'TodoWrite',
  task: 'Agent'
};

function ccToolName(ocTool: string): string {
  return TOOL_NAME_MAP[ocTool?.toLowerCase?.() ?? ''] ?? ocTool;
}

// OpenCode tool args use camelCase (filePath); CC hook consumers expect
// snake_case (file_path).
function ccToolInput(args: Record<string, unknown> | undefined): Record<string, unknown> {
  const input = { ...(args ?? {}) };
  if ('filePath' in input && !('file_path' in input)) {
    input.file_path = input.filePath;
    delete input.filePath;
  }
  return input;
}

/**
 * Spawn a hook script with the payload as stdin.
 *
 * OpenCode may exit the instant a session-idle handler starts (e.g. `opencode
 * run`), killing piped stdin before the child reads it. So the payload goes
 * through a temp file opened as the child's stdin fd, and the child is
 * detached+unref'd — it survives parent death and completes its file effects.
 *
 * `capture: true` keeps stdout piped and awaits completion — only for hooks
 * whose stdout matters (session-start additionalContext), which run while the
 * session is alive anyway.
 */
function runHook(
  entry: TableEntry,
  payload: Record<string, unknown>,
  opts: { cwd?: string; capture?: boolean } = {}
): Promise<{ code: number | null; stdout: string; stderr: string }> {
  const payloadFile = path.join(os.tmpdir(), `katana-parity-stdin-${process.pid}-${Math.random().toString(36).slice(2)}.json`);
  fs.writeFileSync(payloadFile, JSON.stringify(payload), 'utf8');
  const stdinFd = fs.openSync(payloadFile, 'r');
  const pluginRoot = path.join(ROOT, 'plugins', entry.plugin);
  const scriptPath = path.join(pluginRoot, entry.script);
  const interpreter = entry.interpreter ?? 'bash';
  const common = {
    cwd: opts.cwd ?? ROOT,
    env: { ...process.env, CLAUDE_PLUGIN_ROOT: pluginRoot, CLAUDE_PROJECT_DIR: opts.cwd ?? ROOT }
  };
  return new Promise(resolve => {
    try {
      if (opts.capture) {
        const child = spawn(interpreter, [scriptPath], {
          ...common,
          stdio: [stdinFd, 'pipe', 'pipe']
        });
        let stdout = '';
        let stderr = '';
        child.stdout.on('data', (d: Buffer) => (stdout += d.toString()));
        child.stderr.on('data', (d: Buffer) => (stderr += d.toString()));
        child.on('error', () => resolve({ code: -1, stdout, stderr }));
        child.on('close', code => {
          fs.closeSync(stdinFd);
          try {
            fs.unlinkSync(payloadFile);
          } catch {}
          resolve({ code, stdout, stderr });
        });
      } else {
        const child = spawn(interpreter, [scriptPath], {
          ...common,
          detached: true,
          stdio: [stdinFd, 'ignore', 'ignore']
        });
        child.unref();
        fs.closeSync(stdinFd);
        resolve({ code: 0, stdout: '', stderr: '' }); // fire-and-forget
      }
    } catch (err) {
      resolve({ code: -1, stdout: '', stderr: '' });
    }
  });
}

// ---------- per-session state ----------

interface SessionState {
  injection: string | null;
  injected: boolean;
}

const sessions = new Map<string, SessionState>();

function state(sessionID: string): SessionState {
  let s = sessions.get(sessionID);
  if (!s) {
    s = { injection: null, injected: false };
    sessions.set(sessionID, s);
  }
  return s;
}

// SessionStart stdout protocol: {hookSpecificOutput:{additionalContext}}
function parseAdditionalContext(stdout: string): string | null {
  const raw = stdout.trim();
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw);
    const ctx = parsed?.hookSpecificOutput?.additionalContext;
    return typeof ctx === 'string' && ctx.trim() ? ctx : null;
  } catch {
    return null;
  }
}

// ---------- plugin ----------

export const KatanaParity = async (ctx: { directory?: string }) => {
  const projectDir = ctx?.directory ?? process.cwd();
  const disabled = (process.env.KATANA_DISABLED_PLUGINS ?? '').split(',').map(s => s.trim()).filter(Boolean);

  async function onSessionCreated(sessionID: string) {
    const s = state(sessionID);
    const results = await Promise.allSettled(
      TABLE.sessionStart
        .filter(e => !disabled.includes(e.plugin))
        .map(async (entry) => {
          const res = await runHook(
            entry,
            {
              session_id: sessionID,
              source: 'startup',
              cwd: projectDir,
              hook_event_name: 'SessionStart'
            },
            { cwd: projectDir, capture: true }
          );
          return parseAdditionalContext(res.stdout);
        })
    );
    const contexts = results
      .filter((r): r is PromiseFulfilledResult<string | null> => r.status === 'fulfilled')
      .map(r => r.value)
      .filter((v): v is string => v !== null);
    if (contexts.length > 0) {
      s.injection = contexts.join('\n\n');
    }
  }

  return {
    // lifecycle arrives ONLY on the generic event bus (spike-verified)
    event: async (input: { event?: { type?: string; properties?: any } }) => {
      const ev = input?.event;
      if (!ev?.type) return;
      const sessionID: string | undefined = ev.properties?.sessionID ?? ev.properties?.info?.id;
      if (ev.type === 'session.created' && sessionID) await onSessionCreated(sessionID);
    },

    'chat.message': async (_input: { sessionID?: string }, output: { message?: { id?: string; sessionID?: string; role?: string }; parts?: Array<{ type: string; text?: string; id?: string; sessionID?: string; messageID?: string }> }) => {
      const sid = output?.message?.sessionID;
      if (!sid || output?.message?.role !== 'user' || !Array.isArray(output?.parts)) return;
      const s = state(sid);
      // session-start context injection: prepend once, before the first user message.
      // Official opencode (>=1.16) schema-validates user parts on save: a bare
      // {type,text} part dies with `Missing key ["id"]/["sessionID"]/["messageID"]`
      // and the whole prompt fails silently. So we prepend into an existing text
      // part (already carries valid keys); only if no text part exists do we
      // unshift a new part with all required keys set.
      if (s.injection && !s.injected) {
        s.injected = true;
        const ctx = `<katana-context>\n${s.injection}\n</katana-context>`;
        const firstText = output.parts.find(p => p.type === 'text' && typeof p.text === 'string');
        if (firstText) {
          firstText.text = `${ctx}\n\n${firstText.text}`;
        } else {
          output.parts.unshift({
            type: 'text',
            text: ctx,
            id: `prt_katana${Date.now().toString(36)}${Math.random().toString(36).slice(2, 8)}`,
            sessionID: sid,
            messageID: output.message?.id,
          });
        }
      }
    },

    'tool.execute.after': async (input: { tool: string; sessionID: string; callID: string; args?: any }, output: { title?: string; output?: string; metadata?: any }) => {
      const toolName = ccToolName(input.tool);
      const toolInput = ccToolInput(input.args);
      const payload = {
        session_id: input.sessionID,
        hook_event_name: 'PostToolUse',
        tool_name: toolName,
        tool_input: toolInput,
        tool_output: output?.output ?? '',
        cwd: projectDir
      };
      // fpa post-tool-use validation
      for (const entry of TABLE.postToolUse) {
        if (disabled.includes(entry.plugin)) continue;
        const matcher = new RegExp(entry.matcher);
        if (!matcher.test(toolName)) continue;
        const res = await runHook(entry, payload, { cwd: projectDir, capture: true });
        if (res.code === 2) {
          // spike① verified: throw surfaces error to model in OC
          throw new Error(res.stderr || 'FPA validation failed');
        }
      }
    }
  };
};
