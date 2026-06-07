#!/usr/bin/env node
// Normalize text for diff comparison - strip volatile identifiers
'use strict';

function normalize(text) {
  return text
    // Session IDs
    .replace(/ses_[a-z0-9]{12,}/g, '<SESSION_ID>')
    .replace(/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/g, '<UUID>')
    // Timestamps
    .replace(/\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?/g, '<TIMESTAMP>')
    .replace(/\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}/g, '<DATETIME>')
    // Paths (sandbox-specific)
    .replace(/\/tmp\/[^/\s]+/g, '<TMPDIR>')
    .replace(/\/var\/folders\/[^/\s]+\/[^/\s]+\/[^/\s]+/g, '<VARDIR>')
    // Model-specific noise
    .replace(/claude-[a-z0-9-]+/gi, '<MODEL>')
    .replace(/lingzhi\/[a-z0-9-]+/gi, '<MODEL>')
    // Whitespace normalization
    .replace(/\r\n/g, '\n')
    .replace(/[ \t]+/g, ' ')
    .trim();
}

module.exports = { normalize };
