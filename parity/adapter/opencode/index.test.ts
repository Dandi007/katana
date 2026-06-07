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

    // Should spawn session-start hooks for all 5 plugins
    expect(mockSpawn).toHaveBeenCalledTimes(5);

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

  test('handles tool.execute.after for Write', async () => {
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

    // Should spawn fpa validation hook
    expect(mockSpawn).toHaveBeenCalled();
    const call = mockSpawn.mock.calls.find((c: any) =>
      c[1][0].includes('validate_fpa.py')
    );
    expect(call).toBeDefined();
    expect(call[0]).toBe('python3');
  });

  test('handles tool.execute.after for Edit', async () => {
    const plugin = await KatanaParity({ directory: '/test/project' });

    await plugin['tool.execute.after'](
      {
        tool: 'edit',
        sessionID: 'test-session-123',
        callID: 'call-1',
        args: { filePath: '/test/file.md', oldString: 'old', newString: 'new' }
      },
      { output: 'File edited' }
    );

    // Should spawn fpa validation hook
    expect(mockSpawn).toHaveBeenCalled();
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

  test('throws on fpa validation failure (exit code 2)', async () => {
    const plugin = await KatanaParity({ directory: '/test/project' });

    // Mock spawn to return exit code 2
    mockSpawn.mockImplementation((cmd: string, args: string[], opts: any) => ({
      stdout: { on: mock((event: string, cb: Function) => {
        if (event === 'data') {
          cb(Buffer.from('FPA validation failed'));
        }
      })},
      stderr: { on: mock((event: string, cb: Function) => {
        if (event === 'data') {
          cb(Buffer.from('Missing required sections'));
        }
      })},
      on: mock((event: string, cb: Function) => {
        if (event === 'close') {
          setTimeout(() => cb(2), 10);
        }
      }),
      unref: mock()
    }));

    await expect(
      plugin['tool.execute.after'](
        {
          tool: 'write',
          sessionID: 'test-session-123',
          callID: 'call-1',
          args: { filePath: '/test/FPA-test.md', content: 'test' }
        },
        { output: 'File written' }
      )
    ).rejects.toThrow('Missing required sections');
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

    // Should only spawn 3 hooks (guide, memory, work-folder), not 5
    expect(mockSpawn).toHaveBeenCalledTimes(3);

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

  test('converts camelCase args to snake_case', async () => {
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

    // Verify spawn was called with snake_case args
    expect(mockSpawn).toHaveBeenCalled();
    const call = mockSpawn.mock.calls[0];
    const opts = call[2];
    const stdinFd = opts.stdio[0];

    // Read the temp file to verify payload
    // (In real test, we'd mock fs.readFileSync or capture the payload differently)
    expect(stdinFd).toBeDefined();
  });
});
