import { describe, it } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import {
  OVERLAY_VERSION,
  cameraPayloadFromSub,
  handsToDraw,
  buildLookup,
  parseEpisodeFromLabel,
  resolveActiveEpisodeIndex,
  resolveActiveEpisodeByFingerprint,
  buildEpisodeFingerprintIndex,
  scoreHeaderEpisodes,
  resolveEpisodeMap,
  resolveEpisodeCameraPayloads,
  shouldDrawHand,
  validateOverlayPayload,
  reprojThresholdForHand,
} from "./overlay-hand-keypoints-lib.mjs";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

describe("overlay-hand-keypoints-lib", () => {
  it("exports stable overlay version", () => {
    assert.equal(typeof OVERLAY_VERSION, "number");
    assert.ok(OVERLAY_VERSION >= 16);
  });

  it("cameraPayloadFromSub passes layered reproj thresholds (regression: left was 12px)", () => {
    const root = { hands_mode: "both", fps: 30 };
    const sub = {
      hands_mode: "both",
      reproj_threshold_px_left: 96,
      reproj_threshold_px_right: 12,
      frame_index: [0],
      kp2d: [[]],
    };
    const cam = cameraPayloadFromSub(sub, root);
    assert.equal(cam.reproj_threshold_px_left, 96);
    assert.equal(cam.reproj_threshold_px_right, 12);
    assert.equal(reprojThresholdForHand(cam, "left"), 96);
    assert.equal(reprojThresholdForHand(cam, "right"), 12);
  });

  it("shouldDrawHand uses left threshold 96px not 12px", () => {
    const payload = {
      hands_mode: "both",
      reproj_threshold_px_left: 96,
      reproj_threshold_px_right: 12,
    };
    assert.equal(shouldDrawHand("left", 0, 67, payload, 1.0), true);
    assert.equal(shouldDrawHand("left", 0, 100, payload, 1.0), false);
    assert.equal(shouldDrawHand("right", 0, 13, payload, 1.0), false);
    assert.equal(shouldDrawHand("right", 0, 8, payload, 1.0), true);
  });

  it("handsToDraw renders both hands with distinct sides", () => {
    const flatL = Array(42).fill(0);
    flatL[0] = 100;
    flatL[1] = 200;
    const flatR = Array(42).fill(0);
    flatR[0] = 500;
    flatR[1] = 600;
    const payload = {
      hands_mode: "both",
      frame_index: [0],
      reproj_threshold_px_left: 96,
      reproj_threshold_px_right: 12,
      hands: {
        left: {
          kp2d: [flatL],
          hand_confidence: [1.0],
          reprojection_error: [70],
          frame_quality: [0],
        },
        right: {
          kp2d: [flatR],
          hand_confidence: [1.0],
          reprojection_error: [0],
          frame_quality: [0],
        },
      },
    };
    const lookup = buildLookup(payload);
    const hands = handsToDraw(payload, 0, lookup);
    assert.equal(hands.length, 2);
    const sides = hands.map((h) => h.side).sort();
    assert.deepEqual(sides, ["left", "right"]);
  });

  it("parseEpisodeFromLabel decodes LeRobot #100:10 as episode 1", () => {
    assert.equal(parseEpisodeFromLabel("选择 Episode 1 # 100:10EGO-214 · f2710d2c"), "1");
    assert.equal(parseEpisodeFromLabel("# 000:10EGO-214 · 081a430e"), "0");
    assert.notEqual(parseEpisodeFromLabel("# 100:10EGO-214 · f2710d2c"), "100");
  });

  it("resolveActiveEpisodeIndex uses aria-current Episode 1 over stale last", () => {
    const ep = resolveActiveEpisodeIndex({
      lastEpisodeIndex: "0",
      rootPayload: {
        episode_meta: {
          "000000": { episode_index: 0, session_fingerprint: "081a430e" },
          "000001": { episode_index: 1, session_fingerprint: "f2710d2c" },
        },
      },
      episodeAriaNodes: [
        {
          ariaLabel: "选择 Episode 1",
          text: "# 100:10EGO-214 · f2710d2c · 07-07 · 300f (300f)",
          ariaCurrent: true,
        },
        {
          ariaLabel: "选择 Episode 0",
          text: "# 000:10EGO-214 · 081a430e · 07-05 (300f)",
          ariaCurrent: false,
        },
      ],
    });
    assert.equal(ep, "1");
  });

  it("resolveActiveEpisodeByFingerprint picks f2710d2c over sidebar 081a430e", () => {
    const root = {
      episode_meta: {
        "000000": { episode_index: 0, session_fingerprint: "081a430e" },
        "000001": { episode_index: 1, session_fingerprint: "f2710d2c" },
      },
    };
    const ep = resolveActiveEpisodeByFingerprint(root, [
      {
        text: "# 0 00:10 EGO-214 · 081a430e · 07-05 (300f)",
        inEpisodeSidebar: true,
        inNavigation: true,
      },
      {
        text: "# 1 00:10 EGO-214 · f2710d2c · 07-07 · 300f (300f)",
        tagName: "H2",
        inMainContent: true,
      },
    ]);
    assert.equal(ep, "1");
  });

  it("resolveActiveEpisodeIndex uses fingerprint when aria has no episode", () => {
    const root = {
      episode_meta: {
        "000000": { episode_index: 0, session_fingerprint: "081a430e" },
        "000001": { episode_index: 1, session_fingerprint: "f2710d2c" },
      },
    };
    const ep = resolveActiveEpisodeIndex({
      lastEpisodeIndex: "0",
      rootPayload: root,
      headerNodes: [
        {
          text: "# 0 00:10 EGO-214 · 081a430e · 07-05 (300f)",
          inEpisodeSidebar: true,
        },
        {
          text: "# 1 00:10 EGO-214 · f2710d2c · 07-07 · 300f (300f)",
          tagName: "H2",
          inMainContent: true,
        },
      ],
      episodeAriaNodes: [{ ariaLabel: "任务", text: "任务", ariaCurrent: true }],
    });
    assert.equal(ep, "1");
  });

  it("resolveActiveEpisodeIndex uses main header when aria has no episode", () => {
    const ep = resolveActiveEpisodeIndex({
      lastEpisodeIndex: "0",
      headerNodes: [
        {
          text: "# 0 00:10 EGO-214 · 081a430e · 07-05 (300f)",
          tagName: "DIV",
          inEpisodeSidebar: true,
          inNavigation: true,
        },
        {
          text: "# 1 00:10 EGO-214 · f2710d2c · 07-07 · 300f (300f)",
          tagName: "H2",
          inMainContent: true,
        },
      ],
      episodeAriaNodes: [],
    });
    assert.equal(ep, "1");
  });

  it("resolveActiveEpisodeIndex prefers URL episode over aria-current", () => {
    const ep = resolveActiveEpisodeIndex({
      search: "?episode=1",
      lastEpisodeIndex: "0",
      episodeAriaNodes: [{ ariaLabel: "选择 Episode 0", text: "# 0", ariaCurrent: true }],
    });
    assert.equal(ep, "1");
  });

  it("resolveActiveEpisodeIndex prefers header #N when aria has no episode", () => {
    const ep = resolveActiveEpisodeIndex({
      lastEpisodeIndex: "0",
      headerNodes: [
        {
          text: "# 1 00:10 EGO-214 · f2710d2c · 07-07 · 300f (300f)",
          tagName: "H2",
          inMainContent: true,
        },
      ],
      episodeAriaNodes: [],
    });
    assert.equal(ep, "1");
  });

  it("scoreHeaderEpisodes ranks main H2 above sidebar list item", () => {
    const ep = scoreHeaderEpisodes([
      { text: "# 0 00:10 EGO-214 · 081a430e · 07-05 (300f)", inEpisodeSidebar: true },
      { text: "# 1 00:10 EGO-214 · f2710d2c · 07-07 · 300f (300f)", tagName: "H2", inMainContent: true },
    ]);
    assert.equal(ep, "1");
  });

  it("resolveEpisodeMap finds padded episode keys", () => {
    const root = {
      version: 4,
      episodes: {
        "000000": { "observation.images.camera_front_left": { episode_index: 0 } },
        "000001": { "observation.images.camera_front_left": { episode_index: 1 } },
      },
    };
    const r0 = resolveEpisodeMap(root, "0");
    assert.equal(r0.key, "000000");
    const r1 = resolveEpisodeMap(root, "1");
    assert.equal(r1.key, "000001");
    assert.notEqual(r0.map, r1.map);
  });

  it("legacy .js loader does not use static import syntax", () => {
    const jsPath = path.join(__dirname, "overlay-hand-keypoints.js");
    const text = fs.readFileSync(jsPath, "utf8");
    assert.ok(!/^\s*import\s/m.test(text), "classic script must not use import");
    assert.ok(text.includes('type = "module"'), "must bootstrap module entry");
  });

  it("overlay-hand-keypoints.mjs entry has no duplicate imports", () => {
    const mjsPath = path.join(__dirname, "overlay-hand-keypoints.mjs");
    const text = fs.readFileSync(mjsPath, "utf8");
    const imports = [...text.matchAll(/^\s{2}(\w+),/gm)].map((m) => m[1]);
    const dups = imports.filter((name, i) => imports.indexOf(name) !== i);
    assert.deepEqual(dups, [], `duplicate imports: ${dups.join(", ")}`);
  });

  it("resolveEpisodeCameraPayloads does not cross-wire episodes", () => {
    const mkCam = (ep, wristX) => ({
      episode_index: ep,
      hands_mode: "both",
      video_key: "observation.images.camera_front_left",
      frame_index: [50],
      reproj_threshold_px_left: 96,
      reproj_threshold_px_right: 12,
      hands: {
        left: { kp2d: [[wristX, 100]], hand_confidence: [1], reprojection_error: [70], frame_quality: [0] },
        right: { kp2d: [[wristX + 10, 110]], hand_confidence: [1], reprojection_error: [0], frame_quality: [0] },
      },
      kp2d: [[wristX + 10, 110]],
    });
    const root = {
      version: 4,
      hands_mode: "both",
      video_keys: ["observation.images.camera_front_left"],
      reproj_threshold_px_left: 96,
      reproj_threshold_px_right: 12,
      episodes: {
        "000000": { "observation.images.camera_front_left": mkCam(0, 508) },
        "000001": { "observation.images.camera_front_left": mkCam(1, 108) },
      },
    };
    const ep0 = resolveEpisodeCameraPayloads(root, "0")[0].payload;
    const ep1 = resolveEpisodeCameraPayloads(root, "1")[0].payload;
    assert.equal(ep0.hands.right.kp2d[0][0], 518);
    assert.equal(ep1.hands.right.kp2d[0][0], 118);
  });

  it("scheme A excludes camera_front_right from hand overlay", () => {
    const root = {
      version: 4,
      video_keys: [
        "observation.images.camera_front_left",
        "observation.images.camera_front_right",
      ],
      episodes: {
        "000000": {
          "observation.images.camera_front_left": {
            episode_index: 0,
            hands_mode: "both",
            video_key: "observation.images.camera_front_left",
            frame_index: [0],
            hands: { left: { kp2d: [[1, 2]] }, right: { kp2d: [[3, 4]] } },
          },
          "observation.images.camera_front_right": {
            episode_index: 0,
            hands_mode: "both",
            video_key: "observation.images.camera_front_right",
            frame_index: [0],
            hands: { left: { kp2d: [[5, 6]] }, right: { kp2d: [[7, 8]] } },
          },
        },
      },
    };
    const cams = resolveEpisodeCameraPayloads(root, "0");
    assert.equal(cams.length, 1);
    assert.equal(cams[0].video_key, "observation.images.camera_front_left");
  });
});

describe("ego_214_hand_pose sample overlay (if present)", () => {
  const samplePath = path.resolve(
    __dirname,
    "../../../data-storage/samples/ego_214_hand_pose_hand_kp2d.json"
  );

  it("validates production overlay JSON invariants", () => {
    if (!fs.existsSync(samplePath)) return;
    const payload = JSON.parse(fs.readFileSync(samplePath, "utf8"));
    const errors = validateOverlayPayload(payload);
    assert.deepEqual(errors, []);
    const ep0 = payload.episodes["000000"]["observation.images.camera_front_left"];
    const ep1 = payload.episodes["000001"]["observation.images.camera_front_left"];
    const w0 = ep0.hands.right.kp2d[50][0];
    const w1 = ep1.hands.right.kp2d[50][0];
    assert.ok(w0 > 1 || w1 > 1, "at least one episode should have visible right wrist at frame 50");
    assert.notEqual(w0, w1, "episodes must not share identical skeleton at frame 50");
  });
});
