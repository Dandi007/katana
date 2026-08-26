import { describe, test, expect, beforeEach, afterEach, mock } from 'bun:test';
import { KatanaParity } from './index.ts';
import { spawn } from 'node:child_process';
import * as fs from 'node:fs';
import * as path from 'node:path';

// Mock child_process.spawn
mock.module('node:child_process', () => ({
  spawn: mock()
}));

describe('KatanaParity adapter', () => {
  let mockSpawn: any;
  let mockChild: any;

  beforeEach(() => {
    mockSpawn = mock((cmd: string, args: string[], opts: any) => {
      mockChild = {
        stdout: { on: mock() },
        stderr: { on: mock() },
        on: mock((event: string, cb: Function) => {
          if (event === 'close') {
            setTimeout(() => cb(0), 10);
          }
        }),
        unref: mock()
      };
      return mockChild;
    });
    (spawn as any).mockImplementation(mockSpawn);
  });

  afterEach(() => {
    mockSpawn.mockClear();
  });

  test('exports KatanaParity function', () => {
    expect(typeof KatanaParity).toBe('function');
  });

  test('initializes with directory context', async () => {
    const plugin = await KatanaParity({ directory: '/test/project' });
    expect(plugin).toBeDefined();
    expect(typeof plugin.event).toBe('function');
    expect(typeof plugin['chat.message']).toBe('function');
    expect(typeof plugin['tool.execute.after']).toBe('function');
  });

  test('handles session.created event', async () => {
    const plugin = await KatanaParity({ directory: '/test/project' });

    await plugin.event({
      event: {
        type: 'session.created',
        properties: { sessionID: 'test-session-123' }
      }
    });

    // Should spawn session-start hooks for all 7 plugins
    expect(mockSpawn).toHaveBeenCalledTimes(7);

    // Verify first call is for guide plugin
    const firstCall = mockSpawn.mock.calls[0];
    expect(firstCall[0]).toBe('bash');
    expect(firstCall[1][0]).toContain('plugins/guide/hooks/session-start');
  });

  test('injects context on first user message', async () => {
    // Mock spawn to return hook output with additionalContext
    mockSpawn.mockImplementation((cmd: string, args: string[], opts: any) => {
      const child = {
        stdout: {
          on: (event: string, cb: Function) => {
            if (event === 'data') {
              setTimeout(() => cb(Buffer.from(JSON.stringify({
                hookSpecificOutput: { additionalContext: 'Test context from guide' }
              }))), 5);
            }
          }
        },
        stderr: { on: () => {} },
        on: (event: string, cb: Function) => {
          if (event === 'close') {
            setTimeout(() => cb(0), 10);
          }
        },
        unref: () => {}
      };
      return child;
    });

    const plugin = await KatanaParity({ directory: '/test/project' });

    // Trigger session creation
    await plugin.event({
      event: {
        type: 'session.created',
        properties: { sessionID: 'test-session-123' }
      }
    });

    // Wait for hooks to complete
    await new Promise(resolve => setTimeout(resolve, 50));

    // Send first user message
    const output = {
      message: {
        id: 'msg-1',
        sessionID: 'test-session-123',
        role: 'user'
      },
      parts: [
        { type: 'text', text: 'Hello', id: 'part-1', sessionID: 'test-session-123', messageID: 'msg-1' }
      ]
    };

    await plugin['chat.message']({}, output);

    // Should prepend katana-context to first text part
    expect(output.parts[0].text).toContain('<katana-context>');
    expect(output.parts[0].text).toContain('Hello');
  });

  test('does not inject on second user message', async () => {
    const plugin = await KatanaParity({ directory: '/test/project' });

    await plugin.event({
      event: {
        type: 'session.created',
        properties: { sessionID: 'test-session-123' }
      }
    });

    await new Promise(resolve => setTimeout(resolve, 50));

    const output1 = {
      message: { id: 'msg-1', sessionID: 'test-session-123', role: 'user' },
      parts: [{ type: 'text', text: 'First', id: 'p1', sessionID: 'test-session-123', messageID: 'msg-1' }]
    };

    await plugin['chat.message']({}, output1);
    const firstText = output1.parts[0].text;

    const output2 = {
      message: { id: 'msg-2', sessionID: 'test-session-123', role: 'user' },
      parts: [{ type: 'text', text: 'Second', id: 'p2', sessionID: 'test-session-123', messageID: 'msg-2' }]
    };

    await plugin['chat.message']({}, output2);

    // Second message should not have injection
    expect(output2.parts[0].text).toBe('Second');
    expect(output2.parts[0].text).not.toContain('<katana-context>');
  });

  test('ignores non-user messages', async () => {
    const plugin = await KatanaParity({ directory: '/test/project' });

    const output = {
      message: { id: 'msg-1', sessionID: 'test-session-123', role: 'assistant' },
      parts: [{ type: 'text', text: 'Response', id: 'p1', sessionID: 'test-session-123', messageID: 'msg-1' }]
    };

    await plugin['chat.message']({}, output);

    // Should not modify assistant messages
    expect(output.parts[0].text).toBe('Response');
  });

  test('tool.execute.after no-ops when postToolUse table is empty', async () => {
    // fpa 退役（30bb6a7）后 table.json 的 postToolUse 为空：
    // Write/Edit 的 after 事件必须干净 resolve 且不 spawn 任何 hook。
    const plugin = await KatanaParity({ directory: '/test/project' });

    await plugin['tool.execute.after'](
      {
        tool: 'write',
        sessionID: 'test-session-123',
        callID: 'call-1',
        args: { filePath: '/test/file.md', content: 'test' }
      },
      { output: 'File written' }
    );

    expect(mockSpawn).not.toHaveBeenCalled();
  });

  test('ignores tool.execute.after for non-Write/Edit tools', async () => {
    const plugin = await KatanaParity({ directory: '/test/project' });

    await plugin['tool.execute.after'](
      {
        tool: 'read',
        sessionID: 'test-session-123',
        callID: 'call-1',
        args: { filePath: '/test/file.md' }
      },
      { output: 'File content' }
    );

    // Should not spawn fpa validation
    expect(mockSpawn).not.toHaveBeenCalled();
  });

  test('respects KATANA_DISABLED_PLUGINS', async () => {
    process.env.KATANA_DISABLED_PLUGINS = 'wiki,retrieval';

    const plugin = await KatanaParity({ directory: '/test/project' });

    await plugin.event({
      event: {
        type: 'session.created',
        properties: { sessionID: 'test-session-123' }
      }
    });

    // Should only spawn 5 hooks (guide, memory, work-folder, feishu-docs, writing), not 7
    expect(mockSpawn).toHaveBeenCalledTimes(5);

    delete process.env.KATANA_DISABLED_PLUGINS;
  });

  test('maps OpenCode tool names to Claude Code names', async () => {
    const plugin = await KatanaParity({ directory: '/test/project' });

    await plugin['tool.execute.after'](
      {
        tool: 'bash',
        sessionID: 'test-session-123',
        callID: 'call-1',
        args: { command: 'echo test' }
      },
      { output: 'test' }
    );

    // Should not spawn fpa validation (bash is not Write|Edit)
    expect(mockSpawn).not.toHaveBeenCalled();
  });

});
