#!/usr/bin/env node
/** Missing bundled sample zip → welcome screen (not Range error). */
import fs from "node:fs";
import path from "node:path";

const root = process.argv[2] || "/srv/lerobot";
const assets = path.join(root, "assets");
if (!fs.existsSync(assets)) process.exit(0);

const patches = [
  {
    tag: "preflight-not-found",
    old: "if(t.status!==206)return{ok:!1,kind:`range`,message:`Range 预检失败：HTTP ${t.status}`}",
    neu: "if(t.status===404||t.status===410)return{ok:!1,kind:`not_found`};if(t.status!==206)return{ok:!1,kind:`range`,message:`Range 预检失败：HTTP ${t.status}`}",
  },
  {
    tag: "open-remote-not-found",
    old: "if(!e.ok){let t=this.tDefault(`dialogs.remoteArchive.error.`+e.kind);this.deps.upsertTask({id:r,title:n,phase:`error`,error:t,message:void 0}),this.deps.failTask(r,t),this.deps.showToast?.(t,`error`);return}",
    neu:
      "if(!e.ok){if(e.kind===`not_found`&&document.documentElement.getAttribute(`data-datalab-embed`)===`1`){try{let p=new URLSearchParams(window.location.search);p.delete(`url`),window.history.replaceState({},``,window.location.pathname+(p.toString()?`?`+p:``))}catch{}this.deps.clearTasks(),await this.resetToWelcome(null),this.deps.showToast?.(this.tDefault(`dialogs.samples.empty`),`warning`);return}let t=e.kind===`not_found`?this.tDefault(`dialogs.samples.empty`):this.tDefault(`dialogs.remoteArchive.error.`+e.kind);this.deps.upsertTask({id:r,title:n,phase:`error`,error:t,message:void 0}),this.deps.failTask(r,t),this.deps.showToast?.(t,e.kind===`not_found`?`warning`:`error`);return}",
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
console.log(`lerobot sample-missing patch applied (${patched} file(s))`);
