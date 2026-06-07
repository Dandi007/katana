#!/usr/bin/env node
// Generate sandbox OpenCode config: ccs provider only, no auth, allow-all
'use strict';
const [model, ccsUrl] = process.argv.slice(2);
const modelId = model.replace(/^ccs\//, '');

const cfg = {
  $schema: 'https://opencode.ai/config.json',
  permission: { '*': 'allow' },
  enabled_providers: ['ccs'],
  model,
  provider: {
    ccs: {
      name: 'CC Switch Proxy',
      npm: '@ai-sdk/anthropic',
      options: { apiKey: 'katana-parity', baseURL: `${ccsUrl}/v1` },
      models: { [modelId]: { name: modelId, limit: { context: 200000, output: 64000 } } }
    }
  },
  plugin: ['.opencode/plugin']
};

process.stdout.write(JSON.stringify(cfg, null, 2) + '\n');
