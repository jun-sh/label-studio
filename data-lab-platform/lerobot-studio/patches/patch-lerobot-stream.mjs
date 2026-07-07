#!/usr/bin/env node
/** Patch LeRobot index bundle for HTTP stream + collection empty-episode sidebar. */
import fs from "node:fs";
import path from "node:path";

const root = process.argv[2] || "/srv/lerobot";
const assets = path.join(root, "assets");
if (!fs.existsSync(assets)) process.exit(0);

const patches = [
  {
    tag: "httpDataset-vo",
    old: "if(t.startsWith(`sample://`)){let e=yo(t.replace(/^sample:\\/\\//,``));return{kind:`sample`,raw:t,sampleId:e,hint:e}}return{kind:`unknown`,raw:t}}",
    neu: "if(t.startsWith(`sample://`)){let e=yo(t.replace(/^sample:\\/\\//,``));return{kind:`sample`,raw:t,sampleId:e,hint:e}}if(t.includes(`/api/stream/`)){let e=t.startsWith(`http`)?t:new URL(t,window.location.origin).href;return{kind:`httpDataset`,raw:e}}return{kind:`unknown`,raw:t}}",
  },
  {
    tag: "httpDataset-open",
    old: "openFromUrl(e,t=`replace`){let n=vo(e||``);if(n.kind===`remoteArchive`){",
    neu: "openFromUrl(e,t=`replace`){let n=vo(e||``);if(n.kind===`httpDataset`&&typeof window.__datalabOpenHttpDataset==`function`){this.deps.setWelcomeRequest(null),await window.__datalabOpenHttpDataset(this,n.raw,t);return}if(n.kind===`remoteArchive`){",
  },
  {
    tag: "collection-empty-sidebar",
    old: "N=a!==null&&i.length>0,P=N&&h",
    neu: "N=a!==null&&(i.length>0||window.__DATALAB_COLLECTION_MODE__),P=N&&h",
  },
  {
    tag: "zh-empty-no-dataset",
    old: "emptyNoDataset:`请先加载一个 LeRobot 数据集`",
    neu: "emptyNoDataset:`请先加载一个数据集`",
  },
];

let patched = 0;
for (const name of fs.readdirSync(assets)) {
  if (!name.startsWith("index-") || !name.endsWith(".js")) continue;
  const file = path.join(assets, name);
  let s = fs.readFileSync(file, "utf8");
  let changed = false;
  for (const p of patches) {
    if (!s.includes(p.old)) continue;
    s = s.replace(p.old, p.neu);
    changed = true;
  }
  if (!changed) continue;
  fs.writeFileSync(file, s);
  patched += 1;
}
console.log(`lerobot stream/collection patch applied (${patched} file(s))`);
