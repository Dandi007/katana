#!/usr/bin/env node
// Verdict: check injection parity + fpa validation + skill exposure
// Reads ccs payload DB, extracts injected segments, compares CC vs OC
'use strict';

const fs = require('fs');
const path = require('path');
const { normalize } = require('./normalize');
const injectionDiff = require('./injection-diff');

const [scenarioPath, sandbox, model, startTime] = process.argv.slice(2);
const scenario = JSON.parse(fs.readFileSync(scenarioPath, 'utf8'));

const results = [];

function record(name, ok, detail) {
  results.push({ name, ok, detail: detail || '' });
}

function read(side, file) {
  const p = path.join(sandbox, side, 'collected', file);
  return fs.existsSync(p) ? fs.readFileSync(p, 'utf8') : null;
}

// ---- Layer 1: Injection parity (via ccs payload forensics) ----
const injDiff = injectionDiff.diff('cc', 'oc', model, startTime);

// Emit standalone [injection-diff] JSON line for forensics
console.log('[injection-diff] ' + JSON.stringify(injDiff));

if (injDiff.error) {
  record('INJECTION-PARITY', false, injDiff.error);
} else {
  const expectedSegments = scenario.checks?.injection?.segments || ['guide', 'work-folder', 'retrieval', 'wiki'];
  const missing = [];

  for (const seg of expectedSegments) {
    if (!injDiff.cc[seg]) missing.push(`cc:${seg}`);
    if (!injDiff.oc[seg]) missing.push(`oc:${seg}`);
  }

  const pass = missing.length === 0;
  record('INJECTION-PARITY', pass, pass ? 'All segments present on both sides' : `Missing: ${missing.join(', ')}`);
}

// ---- Layer 2: FPA validation ----
const ccLog = read('cc', 'log.txt') || '';
const ocLog = read('oc', 'log.txt') || '';

const ccFpaTriggered = /validate_fpa\.py/.test(ccLog);
const ocFpaTriggered = /validate_fpa\.py/.test(ocLog);

if (!ccFpaTriggered && !ocFpaTriggered) {
  record('FPA-VALIDATION', true, 'No FPA documents written (expected for basic scenario)');
} else if (ccFpaTriggered !== ocFpaTriggered) {
  record('FPA-VALIDATION', false, `FPA trigger mismatch: cc=${ccFpaTriggered}, oc=${ocFpaTriggered}`);
} else {
  record('FPA-VALIDATION', true, 'Both sides triggered FPA validation');
}

// ---- Layer 3: Skill exposure ----
const ccOutput = read('cc', 'output.txt') || '';
const ocOutput = read('oc', 'output.txt') || '';

const expectedSkills = scenario.checks?.skills?.exposed || [];
const ccSkills = new Set();
const ocSkills = new Set();

// Extract skill mentions from output (heuristic: skill names in backticks or mentioned)
for (const skill of expectedSkills) {
  if (new RegExp(`\\b${skill}\\b`, 'i').test(ccOutput)) ccSkills.add(skill);
  if (new RegExp(`\\b${skill}\\b`, 'i').test(ocOutput)) ocSkills.add(skill);
}

const missingSkills = [];
for (const skill of expectedSkills) {
  if (!ccSkills.has(skill)) missingSkills.push(`cc:${skill}`);
  if (!ocSkills.has(skill)) missingSkills.push(`oc:${skill}`);
}

if (missingSkills.length === 0) {
  record('SKILL-EXPOSURE', true, 'All expected skills exposed on both sides');
} else {
  record('SKILL-EXPOSURE', false, `Missing skills: ${missingSkills.join(', ')}`);
}

// ---- Layer 4: Output equivalence (normalized) ----
const ccNorm = normalize(ccOutput);
const ocNorm = normalize(ocOutput);

// Check structural equivalence: both created test.md
const ccHasTestMd = /created.*test\.md/i.test(ccNorm);
const ocHasTestMd = /created.*test\.md/i.test(ocNorm);

if (ccHasTestMd === ocHasTestMd) {
  record('OUTPUT-EQUIVALENCE', true, `Both sides ${ccHasTestMd ? 'created' : 'did not create'} test.md`);
} else {
  record('OUTPUT-EQUIVALENCE', false, `cc created test.md: ${ccHasTestMd}, oc created test.md: ${ocHasTestMd}`);
}

// ---- Report ----
let failed = 0;
for (const r of results) {
  if (!r.ok) failed++;
  // Emit exact literal format: "INJECTION-PARITY PASS" or "INJECTION-PARITY FAIL"
  console.log(`${r.name} ${r.ok ? 'PASS' : 'FAIL'}${r.detail ? '  — ' + r.detail : ''}`);
}

console.log(`\n[e2e] ${results.length - failed}/${results.length} checks passed`);

if (failed === 0) {
  console.log('\n✅ PARITY PASS');
  process.exit(0);
} else {
  console.log('\n❌ PARITY FAIL');
  process.exit(1);
}
