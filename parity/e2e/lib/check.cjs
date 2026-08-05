#!/usr/bin/env node
// Verdict: end-to-end parity between Claude Code and OpenCode on the same harness.
// Two deterministic layers, each PASS only when CC and OC agree:
//   1. INJECTION-PARITY   — the katana session-start segments reached the LLM
//      (forensic, from ccs payload recording, per-side time window; whole-body).
//   2. TOOL-EFFECT-PARITY — both sides produced the shared write side effect.
// NOTE: PostToolUse parity had a third layer until the fpa validator hook was
// retired (fpa became prompt-only). The adapter still supports postToolUse but
// no katana plugin registers one, so there is nothing live to compare.
// usage: node check.js <scenarioPath> <sandbox>
'use strict';

const fs = require('fs');
const path = require('path');
const injectionDiff = require('./injection-diff.cjs');

const [scenarioPath, sandbox] = process.argv.slice(2);
const scenario = JSON.parse(fs.readFileSync(scenarioPath, 'utf8'));

const results = [];
const record = (name, ok, detail) => results.push({ name, ok, detail: detail || '' });
const read = (side, file) => {
  const p = path.join(sandbox, side, 'collected', file);
  return fs.existsSync(p) ? fs.readFileSync(p, 'utf8') : '';
};

// ---- Layer 1: injection parity (ccs payload forensics) ----
const injDiff = injectionDiff.diff(sandbox);
console.log('[injection-diff] ' + JSON.stringify(injDiff));
if (injDiff.error) {
  record('INJECTION-PARITY', false, injDiff.error);
} else {
  const segs = scenario.checks?.injection?.segments || Object.keys(injectionDiff.FINGERPRINTS);
  const missing = [];
  for (const s of segs) {
    if (!injDiff.cc[s]) missing.push(`cc:${s}`);
    if (!injDiff.oc[s]) missing.push(`oc:${s}`);
  }
  record('INJECTION-PARITY', missing.length === 0,
    missing.length === 0 ? `all ${segs.length} segments present on both sides` : `missing ${missing.join(', ')}`);
}

// ---- Layer 2: tool-effect parity (deterministic shared side effect) ----
// Final prose is non-deterministic and not a parity signal. The deterministic
// shared effect is the test.md write, present in both projs.
const fs2 = require('fs');
const ccWrote = fs2.existsSync(path.join(sandbox, 'cc', 'proj', 'test.md'));
const ocWrote = fs2.existsSync(path.join(sandbox, 'oc', 'proj', 'test.md'));
record('TOOL-EFFECT-PARITY', ccWrote && ocWrote,
  ccWrote === ocWrote ? `both wrote test.md=${ccWrote}` : `mismatch cc=${ccWrote} oc=${ocWrote}`);

// ---- report (literal contract: "<NAME> PASS|FAIL", final "PARITY PASS"/"PARITY FAIL") ----
let failed = 0;
for (const r of results) {
  if (!r.ok) failed++;
  console.log(`${r.name} ${r.ok ? 'PASS' : 'FAIL'}${r.detail ? '  — ' + r.detail : ''}`);
}
console.log(`\n[e2e] ${results.length - failed}/${results.length} checks passed`);
if (failed === 0) { console.log('\nPARITY PASS'); process.exit(0); }
console.log('\nPARITY FAIL'); process.exit(1);
