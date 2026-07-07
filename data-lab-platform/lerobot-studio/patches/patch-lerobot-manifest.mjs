#!/usr/bin/env node
/** Point sample manifest loader at same-origin /lerobot/ (avoid COS + placeholder fallback). */
import fs from "node:fs";
import path from "node:path";

const root = process.argv[2] || "/srv/lerobot";
const assets = path.join(root, "assets");
if (!fs.existsSync(assets)) process.exit(0);

const patches = [
  {
    tag: "Ni-cos-to-lerobot",
    old: "let e=`https://io-lerobot-examples-1328702871.cos.accelerate.myqcloud.com/`",
    neu: "let e=`/lerobot/`",
  },
  {
    tag: "Pi-manifest-local",
    old: "function Pi(){let e=Ni();if(e)try{return new URL(`sample-datasets.manifest.json`,e).toString()}catch{}try{return new URL(`/sample-datasets.manifest.json`,window.location.origin).toString()}catch{return null}}",
    neu: 'function Pi(){try{return new URL("/lerobot/sample-datasets.manifest.json",window.location.origin).href}catch(e){return null}}',
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
console.log(`lerobot manifest patch applied (${patched} file(s))`);
