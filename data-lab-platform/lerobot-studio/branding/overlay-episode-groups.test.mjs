import assert from "node:assert/strict";
import {
  buildDateGroups,
  formatDateGroupLabel,
  formatSessionDateFromIso,
  parseSessionDateFromTaskText,
  parseSessionIdFromTaskText,
  resolveEpisodeDate,
  unknownDateKey,
} from "./overlay-episode-groups-lib.mjs";

assert.equal(
  parseSessionDateFromTaskText("EGO-001 · 2a17d33d · 09-07 (173f)"),
  "09-07",
);
assert.equal(parseSessionIdFromTaskText("sess_2a17d33d715743bdb5b243c029174b9a"), "sess_2a17d33d715743bdb5b243c029174b9a");
assert.equal(formatSessionDateFromIso("2026-09-07T01:34:21.811Z"), "09-07");
assert.equal(
  resolveEpisodeDate({
    taskText: "sess_2a17d33d715743bdb5b243c029174b9a",
    episodeIndex: 0,
    uploadDateByIndex: { 0: "09-07" },
  }),
  "09-07",
);
assert.equal(
  parseSessionDateFromTaskText("EGO-001 · accc02fb · 09-07 (125f)"),
  "09-07",
);
assert.equal(
  parseSessionDateFromTaskText("EGO-001 · bae2b20e · 08-28 · 156f"),
  "08-28",
);
assert.equal(parseSessionDateFromTaskText("no date here"), null);

assert.equal(formatDateGroupLabel("09-07", "zh"), "9月7日");
assert.equal(formatDateGroupLabel("09-07", "en"), "09-07");

const groups = buildDateGroups([
  { episodeIndex: 0, date: "09-05", label: "a" },
  { episodeIndex: 1, date: "09-05", label: "b" },
  { episodeIndex: 2, date: "09-07", label: "c" },
]);
assert.equal(groups.length, 2);
assert.deepEqual(groups[0], { date: "09-05", episodeIndices: [0, 1] });
assert.deepEqual(groups[1], { date: "09-07", episodeIndices: [2] });
assert.equal(unknownDateKey(), "__unknown__");

console.log("overlay-episode-groups.test.mjs: ok");
