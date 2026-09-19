#!/usr/bin/env node
// Injection forensics — the first-principles parity gate.
//
// Both CC (`claude -p`) and OC (`opencode run`) persist the full conversation
// they sent to the model in their own local session store. We read each side's
// store from the sandbox and grep the WHOLE record (system + every message)
// for the 4 deterministic session-start segment fingerprints — OC injects into
// the first user message, CC into the system/context, so a system-only scan
// would miss OC.
//
//   CC: $SANDBOX/cc/home/.claude/projects/**/*.jsonl   (transcript, one JSON per line;
//       SessionStart hook output lands in the first user turn as a system-reminder)
//   OC: $SANDBOX/oc/xdg-data/opencode.db  (sqlite; `message.data` + `part.data`
//       JSON columns hold every text part, including the adapter-prepended one)
//
// This replaces the retired cc-switch payload DB (ccs :15721) — the New API
// gateway (:15722) only logs usage metadata, not request bodies.
'use strict';

const fs = require('fs');
const path = require('path');
const { execFileSync } = require('child_process');

// Fingerprint = a stable header string in each plugin's injected SKILL/rule text.
const FINGERPRINTS = {
  guide: 'Using Katana',
  'work-folder': 'Work Folder',
  retrieval: 'Using Retrieval',
  memory: '<memory-index>',
};

function walk(dir, out) {
  if (!fs.existsSync(dir)) return out;
  for (const ent of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, ent.name);
    if (ent.isDirectory()) walk(p, out);
    else if (ent.isFile() && p.endsWith('.jsonl')) out.push(p);
  }
  return out;
}

// CC: concatenate every transcript jsonl under the sandboxed ~/.claude/projects.
function ccRecords(sandbox) {
  const root = path.join(sandbox, 'cc', 'home', '.claude', 'projects');
  const files = walk(root, []);
  if (!files.length) return { error: `no CC transcript jsonl under ${root}` };
  return { text: files.map((f) => fs.readFileSync(f, 'utf8')).join('\n') };
}

// OC: dump message/part JSON from the sandboxed sqlite store.
// sqlite3 CLI keeps this dependency-free (no better-sqlite3).
function ocRecords(sandbox) {
  const candidates = [
    process.env.OPENCODE_DB,
    path.join(sandbox, 'oc', 'xdg-data', 'opencode.db'),
    path.join(sandbox, 'oc', 'xdg-data', 'opencode', 'opencode.db'),
  ].filter(Boolean);
  const db = candidates.find((p) => fs.existsSync(p));
  if (!db) return { error: `no OC sqlite store; looked at ${candidates.join(', ')}` };
  try {
    const sql = 'SELECT data FROM message; SELECT data FROM part;';
    const out = execFileSync('sqlite3', ['-readonly', '-noheader', db, sql], {
      encoding: 'utf8', maxBuffer: 256 * 1024 * 1024,
    });
    return { text: out };
  } catch (err) {
    return { error: `sqlite3 query failed on ${db}: ${err.message}` };
  }
}

function segmentsFor(text) {
  return Object.fromEntries(
    Object.entries(FINGERPRINTS).map(([seg, fp]) => [seg, text.includes(fp)]),
  );
}

// diff(sandbox): returns { cc:{seg:bool}, oc:{seg:bool}, cc_bytes, oc_bytes } or { error }.
function diff(sandbox) {
  const result = {};
  for (const [side, reader] of [['cc', ccRecords], ['oc', ocRecords]]) {
    const got = reader(sandbox);
    if (got.error) return { error: `${side}: ${got.error}` };
    if (!got.text.trim()) return { error: `${side}: session store is empty` };
    result[side] = segmentsFor(got.text);
    result[`${side}_bytes`] = got.text.length;
  }
  return result;
}

module.exports = { diff, segmentsFor, FINGERPRINTS, ccRecords, ocRecords };
