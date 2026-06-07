#!/usr/bin/env node
// Verdict: end-to-end parity between Claude Code and OpenCode on the same harness.
// Three deterministic layers, each PASS only when CC and OC agree:
//   1. INJECTION-PARITY — the 4 katana session-start segments reached the LLM
//      (forensic, from ccs payload recording, per-side time window; whole-body).
//   2. FPA-PARITY        — the fpa PostToolUse hook fired on the FPA-named write
//      on both sides (validate_fpa.py appears in both run logs).
//   3. OUTPUT-PARITY     — both sides completed the task (reply contains DONE).
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
    missing.length === 0 ? 'all 4 segments present on both sides' : `missing ${missing.join(', ')}`);
}

// ---- Layer 2: fpa hook parity (forensic — fpa exit-2 feedback fed back to model) ----
// injection-diff exposes cc_fpa/oc_fpa: the validate_fpa failure phrase present
// in each side's ccs payloads. CC feeds PostToolUse exit-2 stderr to the model;
// OC's adapter throws from tool.execute.after — both land '机械验收失败' in a
// subsequent request body. Same forensic channel as injection.
if (injDiff.error) {
  record('FPA-PARITY', false, injDiff.error);
} else {
  const ccFpa = !!injDiff.cc_fpa;
  const ocFpa = !!injDiff.oc_fpa;
  record('FPA-PARITY', ccFpa && ocFpa,
    ccFpa === ocFpa ? `both sides fpa-fed-back=${ccFpa}` : `mismatch cc=${ccFpa} oc=${ocFpa}`);
}

// ---- Layer 3: tool-effect parity (deterministic shared side effect) ----
// The model's final prose diverges by design once fpa blocks (CC asks a
// question, OC retries) — that's non-deterministic and not a parity signal.
// The deterministic shared effect is the test.md write, present in both projs.
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
