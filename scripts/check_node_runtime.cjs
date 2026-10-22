// Offline smoke for the exact Node runtime shipped by Docker and selected in CI.
'use strict';
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { join } = require('node:path');
const { execFileSync } = require('node:child_process');
const { brotliCompressSync, brotliDecompressSync } = require('node:zlib');
const { randomBytes, createHash } = require('node:crypto');

const expected = readFileSync(join(__dirname, '..', '.node-version'), 'utf8').trim();
assert.equal(process.versions.node, expected, 'Node differs from the reviewed .node-version');
const input = Buffer.from('Hermes WebUI Node runtime smoke');
assert.deepEqual(brotliDecompressSync(brotliCompressSync(input)), input);
assert.equal(randomBytes(32).length, 32);
assert.equal(createHash('sha256').update(input).digest('hex').length, 64);
const command = name => execFileSync(name, ['--version'], {
  encoding: 'utf8', timeout: 15000, stdio: ['ignore', 'pipe', 'pipe'],
}).trim();
console.log(JSON.stringify({ node: process.version, npm: command('npm'), npx: command('npx'),
  arch: process.arch, brotli: 'ok', crypto: 'ok' }));
