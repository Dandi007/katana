#!/usr/bin/env node
// Generate sandbox OpenCode config: New API gateway provider only, allow-all.
// The gateway token is NOT written into the file — OpenCode resolves
// `{env:NEW_API_GATEWAY_TOKEN}` from the sandbox process env at runtime.
'use strict';
const [model, gatewayUrl] = process.argv.slice(2);
const modelId = model.replace(/^gateway\//, '');

const cfg = {
  $schema: 'https://opencode.ai/config.json',
  permission: { '*': 'allow' },
  enabled_providers: ['gateway'],
  model,
  provider: {
    gateway: {
      name: 'New API Gateway',
      npm: '@ai-sdk/openai-compatible',
      options: { apiKey: '{env:NEW_API_GATEWAY_TOKEN}', baseURL: `${gatewayUrl}/v1` },
      models: { [modelId]: { name: modelId, limit: { context: 200000, output: 64000 } } }
    }
  },
  plugin: ['.opencode/plugin']
};

process.stdout.write(JSON.stringify(cfg, null, 2) + '\n');
