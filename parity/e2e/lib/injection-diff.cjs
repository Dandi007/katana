#!/usr/bin/env node
// Injection forensics — the first-principles parity gate.
//
// Both CC (`claude -p`) and OC (`opencode run`) send their requests to the SAME
// ccs proxy (15721), which records every request body. Empirically BOTH sides
// register as app_type='claude' with the same model, so they CANNOT be told
// apart by app_type/model. They ARE separable by TIME: run.sh runs the two
// sides sequentially and stamps a disjoint [start,end] epoch window per side
// (sandbox/<side>/window). We pull each side's request bodies by its window and
// grep the WHOLE body (system + every message) for the 4 deterministic
// session-start segment fingerprints — OC injects into the first user message,
// CC into the system/context, so a system-only scan would miss OC.
'use strict';

const fs = require('fs');
const path = require('path');
const { execFileSync } = require('child_process');

const CCS_DB_PATH = process.env.CCS_DB_PATH || '/Volumes/Data/cc-switch/cc-switch.db';

// Fingerprint = a stable header string in each plugin's injected SKILL/rule text.
const FINGERPRINTS = {
  guide: 'Using Katana',
  'work-folder': 'Work Folder',
  retrieval: 'Using Retrieval',
  wiki: 'Using Wiki',
};

// fpa PostToolUse exit-2 stderr is fed back to the model, so it lands in a
// SUBSEQUENT request body within the same window — same forensic channel as
// injection. validate_fpa.py emits this exact phrase on structure failure.
const FPA_FINGERPRINT = '机械验收失败';

// Pull the concatenated request bodies whose created_at falls in [t0,t1].
// sqlite3 CLI keeps this dependency-free (no better-sqlite3).
function bodiesInWindow(t0, t1) {
  if (!fs.existsSync(CCS_DB_PATH)) {
    return { error: `ccs DB not found at ${CCS_DB_PATH}` };
  }
  try {
    const sql =
      `SELECT request_body FROM proxy_request_payloads ` +
      `WHERE created_at >= ${t0 - 1} AND created_at <= ${t1 + 1};`;
    const out = execFileSync('sqlite3', ['-noheader', CCS_DB_PATH, sql], {
      encoding: 'utf8', maxBuffer: 256 * 1024 * 1024,
    });
    return { text: out };
  } catch (err) {
    return { error: `sqlite3 query failed: ${err.message}` };
  }
}

function readWindow(sandbox, side) {
  const p = path.join(sandbox, side, 'window');
  const raw = fs.readFileSync(p, 'utf8').trim().split(/\s+/).map(Number);
  if (raw.length < 2 || raw.some(Number.isNaN)) {
    throw new Error(`bad window file ${p}: ${JSON.stringify(raw)}`);
  }
  return [raw[0], raw[1]];
}

function segmentsFor(text) {
  return Object.fromEntries(
    Object.entries(FINGERPRINTS).map(([seg, fp]) => [seg, text.includes(fp)]),
  );
}

// diff(sandbox): returns { cc:{seg:bool}, oc:{seg:bool}, cc_bytes, oc_bytes } or { error }.
function diff(sandbox) {
  const result = {};
  for (const side of ['cc', 'oc']) {
    let win;
    try {
      win = readWindow(sandbox, side);
    } catch (err) {
      return { error: `${side}: ${err.message}` };
    }
    const got = bodiesInWindow(win[0], win[1]);
    if (got.error) return { error: `${side}: ${got.error}` };
    if (!got.text.trim()) return { error: `${side}: no ccs payloads recorded in window [${win[0]},${win[1]}]` };
    result[side] = segmentsFor(got.text);
    result[`${side}_fpa`] = got.text.includes(FPA_FINGERPRINT);
    result[`${side}_bytes`] = got.text.length;
  }
  return result;
}

module.exports = { diff, segmentsFor, FINGERPRINTS, FPA_FINGERPRINT };
