import { test } from 'node:test';
import assert from 'node:assert/strict';
import { matchesEpisode } from './filter.js';

const row = { episodeIndex: 12, label: 'Pick cup 11-08 14:03', length: 300 }; // 10 s at 30 fps

test('no filter keeps every row', () => {
  assert.equal(matchesEpisode(row, {}), true);
});

test('search matches label text and "# n"', () => {
  assert.equal(matchesEpisode(row, { query: 'CUP' }), true);
  assert.equal(matchesEpisode(row, { query: '# 12' }), true);
  assert.equal(matchesEpisode(row, { query: 'drawer' }), false);
});

test('duration bounds are inclusive seconds from length / fps', () => {
  assert.equal(matchesEpisode(row, { minS: '10', maxS: '10', fps: 30 }), true);
  assert.equal(matchesEpisode(row, { minS: '10.1', fps: 30 }), false);
  assert.equal(matchesEpisode(row, { maxS: '9.9', fps: 30 }), false);
  assert.equal(matchesEpisode(row, { maxS: '5', fps: 60 }), true);
});

test('empty or non-numeric bounds are ignored; fps defaults to 30', () => {
  assert.equal(matchesEpisode(row, { minS: '', maxS: 'abc' }), true);
  assert.equal(matchesEpisode(row, { minS: '10' }), true);
  assert.equal(matchesEpisode(row, { minS: '11' }), false);
});

test('search and duration combine', () => {
  assert.equal(matchesEpisode(row, { query: 'cup', maxS: '5', fps: 30 }), false);
});
