#!/usr/bin/env node
// Generate sandbox Claude Code settings.json wiring katana hooks
'use strict';
const root = process.argv[2];

const settings = {
  hooks: {
    SessionStart: [
      {
        matcher: 'startup|clear|compact',
        hooks: [
          { type: 'command', command: `"${root}/plugins/guide/hooks/session-start"` },
          { type: 'command', command: `"${root}/plugins/memory/hooks/session-start"` },
          { type: 'command', command: `"${root}/plugins/work-folder/hooks/session-start"` },
          { type: 'command', command: `"${root}/plugins/retrieval/hooks/session-start"` },
          { type: 'command', command: `"${root}/plugins/wiki/hooks/session-start"` }
        ]
      }
    ]
  },
  env: {
    CLAUDE_PLUGIN_ROOT: root
  }
};

process.stdout.write(JSON.stringify(settings, null, 2) + '\n');
