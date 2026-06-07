#!/usr/bin/env node
// Injection forensics: extract and diff injected context from ccs payload recording
// Reads ccs payload DB, filters by time window + model, extracts 4 deterministic segments
'use strict';

const fs = require('fs');
const path = require('path');

const CCS_DB_PATH = process.env.CCS_DB_PATH || path.join(process.env.HOME, '.cc-switch/cc-switch.db');

function extractSegments(systemPrompt) {
  const segments = {
    guide: false,
    'work-folder': false,
    retrieval: false,
    wiki: false
  };

  if (!systemPrompt) return segments;

  // guide: using-katana skill
  if (systemPrompt.includes('using-katana') || systemPrompt.includes('katana plugin')) {
    segments.guide = true;
  }

  // work-folder: work folder convention
  if (systemPrompt.includes('work folder') || systemPrompt.includes('work-folder')) {
    segments['work-folder'] = true;
  }

  // retrieval: multi-source retrieval
  if (systemPrompt.includes('retrieval') || systemPrompt.includes('/retrieval:')) {
    segments.retrieval = true;
  }

  // wiki: wiki engine
  if (systemPrompt.includes('wiki') || systemPrompt.includes('WIKI.md')) {
    segments.wiki = true;
  }

  return segments;
}

function queryPayloads(since, model, appType) {
  if (!fs.existsSync(CCS_DB_PATH)) {
    console.error(`[injection-diff] ccs DB not found at ${CCS_DB_PATH}`);
    return [];
  }

  try {
    const Database = require('better-sqlite3');
    const db = new Database(CCS_DB_PATH, { readonly: true });
    const stmt = db.prepare(`
      SELECT prp.request_body, prp.created_at
      FROM proxy_request_payloads prp
      JOIN proxy_request_logs prl ON prp.request_id = prl.request_id
      WHERE prp.created_at >= ?
        AND prl.model LIKE ?
        AND prl.app_type = ?
      ORDER BY prp.created_at DESC
      LIMIT 10
    `);

    const rows = stmt.all(since, `%${model}%`, appType);
    db.close();

    return rows.map(r => ({
      payload: JSON.parse(r.request_body),
      created_at: r.created_at
    }));
  } catch (err) {
    console.error(`[injection-diff] Failed to query ccs DB: ${err.message}`);
    return [];
  }
}

function extractSystemPrompt(payload) {
  // OpenAI format: messages[0].content (if role=system)
  // Anthropic format: system field
  const msg = payload.payload || payload;

  if (msg.system) {
    return typeof msg.system === 'string' ? msg.system : JSON.stringify(msg.system);
  }

  if (msg.messages && msg.messages.length > 0) {
    const first = msg.messages[0];
    if (first.role === 'system') {
      return typeof first.content === 'string' ? first.content : JSON.stringify(first.content);
    }
  }

  return '';
}

function diff(ccSide, ocSide, model, startTime) {
  const ccPayloads = queryPayloads(startTime, model, 'claude');
  const ocPayloads = queryPayloads(startTime, model, 'codex');

  if (ccPayloads.length === 0) {
    return { error: 'No CC payloads found in time window' };
  }
  if (ocPayloads.length === 0) {
    return { error: 'No OC payloads found in time window' };
  }

  // Take the first (most recent) session-start payload from each side
  const ccSystem = extractSystemPrompt(ccPayloads[0].payload);
  const ocSystem = extractSystemPrompt(ocPayloads[0].payload);

  const ccSegments = extractSegments(ccSystem);
  const ocSegments = extractSegments(ocSystem);

  return {
    cc: ccSegments,
    oc: ocSegments,
    cc_payload_count: ccPayloads.length,
    oc_payload_count: ocPayloads.length
  };
}

module.exports = { diff, extractSegments, queryPayloads };
