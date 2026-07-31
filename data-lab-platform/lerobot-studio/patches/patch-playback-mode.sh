#!/bin/sh
# Default playback: loop current episode; add "none" (play once, no loop).
set -eu

ROOT="${1:-/srv/lerobot}"
[ -d "$ROOT" ] || exit 0

patch_file() {
  f="$1"
  [ -f "$f" ] || return 0

  sed -i \
    -e 's/\[A,te\]=(0,W\.useState)(`sequential`)/[A,te]=(0,W.useState)(`loop`)/g' \
    -e 's/if(e>=_\.length)if(A===`loop`)e=0,R\.current=e,H(e);else{E(!1);let e=-1;if(A===`sequential`)/if(e>=_.length)if(A===`loop`)e=0,R.current=e,H(e);else if(A===`none`)E(!1);else{E(!1);let e=-1;if(A===`sequential`)/g' \
    -e 's/c(s===`sequential`?`shuffle`:s===`shuffle`?`loop`:`sequential`)/c(s===`loop`?`none`:s===`none`?`sequential`:s===`sequential`?`shuffle`:`loop`)/g' \
    -e 's#case`loop`:return(0,K\.jsx)(ve,{className:`h-4 w-4`});case`shuffle`:return(0,K\.jsx)(He,{className:`h-4 w-4`});case`sequential`:return(0,K\.jsx)(Ke,{className:`h-4 w-4`})#case`loop`:return(0,K.jsx)(ve,{className:`h-4 w-4`});case`none`:return(0,K.jsxs)(`svg`,{xmlns:`http://www.w3.org/2000/svg`,viewBox:`0 0 24 24`,fill:`none`,stroke:`currentColor`,strokeWidth:2,strokeLinecap:`round`,strokeLinejoin:`round`,className:`h-4 w-4`,children:[(0,K.jsx)(`path`,{d:`M5 5v14`}),(0,K.jsx)(`path`,{d:`m9 5 10 7-10 7V5z`})]});case`shuffle`:return(0,K.jsx)(He,{className:`h-4 w-4`});case`sequential`:return(0,K.jsx)(Ke,{className:`h-4 w-4`})#g' \
    -e 's#case`none`:return(0,K\.jsx)(Je,{className:`h-4 w-4`});#case`none`:return(0,K.jsxs)(`svg`,{xmlns:`http://www.w3.org/2000/svg`,viewBox:`0 0 24 24`,fill:`none`,stroke:`currentColor`,strokeWidth:2,strokeLinecap:`round`,strokeLinejoin:`round`,className:`h-4 w-4`,children:[(0,K.jsx)(`path`,{d:`M5 5v14`}),(0,K.jsx)(`path`,{d:`m9 5 10 7-10 7V5z`})]});#g' \
    -e 's/case`loop`:return e(`playback\.mode\.loop`);case`shuffle`:return e(`playback\.mode\.shuffle`);case`sequential`:return e(`playback\.mode\.sequential`)/case`loop`:return e(`playback.mode.loop`);case`none`:return e(`playback.mode.none`);case`shuffle`:return e(`playback.mode.shuffle`);case`sequential`:return e(`playback.mode.sequential`)/g' \
    -e 's/loop:`单个 Episode 循环`,shuffle:`列表随机播放`,sequential:`列表顺序播放`/loop:`单个 Episode 循环`,none:`不循环播放`,shuffle:`列表随机播放`,sequential:`列表顺序播放`/g' \
    -e 's/loop:`Loop current episode`,shuffle:`Shuffle playlist`,sequential:`Play in order`/loop:`Loop current episode`,none:`No loop`,shuffle:`Shuffle playlist`,sequential:`Play in order`/g' \
    -e 's/loop:`現在のエピソードをループ`,shuffle:`シャッフル再生`,sequential:`順番に再生`/loop:`現在のエピソードをループ`,none:`ループなし`,shuffle:`シャッフル再生`,sequential:`順番に再生`/g' \
    -e 's/className:`h-8 w-8 ${s===`sequential`?`text-muted-foreground`:`text-primary`}/className:`h-8 w-8 ${s===`loop`?`text-muted-foreground`:`text-primary`}/g' \
    "$f" || true

  node - "$f" <<'NODE' || true
const fs = require("fs");
const path = process.argv[2];
let t = fs.readFileSync(path, "utf8");

const noneSkipForward =
  "case`none`:return(0,K.jsxs)(`svg`,{xmlns:`http://www.w3.org/2000/svg`,viewBox:`0 0 24 24`,fill:`none`,stroke:`currentColor`,strokeWidth:2,strokeLinecap:`round`,strokeLinejoin:`round`,className:`h-4 w-4`,children:[(0,K.jsx)(`path`,{d:`M5 5v14`}),(0,K.jsx)(`path`,{d:`m9 5 10 7-10 7V5z`})]});";

for (const old of [
  "case`none`:return(0,K.jsx)(Je,{className:`h-4 w-4`});",
  "case`none`:return(0,K.jsx)(`span`,{className:`text-sm leading-none`,children:`▶`});",
  "case`none`:return(0,K.jsx)(`span`,{className:`inline-flex items-center gap-0.5 text-xs leading-none`,children:`▶ 一次`});",
  noneSkipForward.replace("m9 5 10 7-10 7V5z", "m19 5-10 7 10 7V5z"),
]) {
  if (t.includes(old)) t = t.split(old).join(noneSkipForward);
}

const btnWide =
  '(0,K.jsx)(q,{variant:`ghost`,size:s===`none`?`sm`:`icon`,className:s===`none`?`h-8 px-1.5 min-w-[3rem] text-xs font-medium ${s===`loop`?`text-muted-foreground`:`text-primary`} hover:text-foreground`:`h-8 w-8 ${s===`loop`?`text-muted-foreground`:`text-primary`} hover:text-foreground`,onClick:E,disabled:o,"aria-label":e(`playback.mode.toggle`),children:D()})';
const btnIcon =
  '(0,K.jsx)(q,{variant:`ghost`,size:`icon`,className:`h-8 w-8 ${s===`loop`?`text-muted-foreground`:`text-primary`} hover:text-foreground`,onClick:E,disabled:o,"aria-label":e(`playback.mode.toggle`),children:D()})';
if (t.includes(btnWide)) t = t.split(btnWide).join(btnIcon);

fs.writeFileSync(path, t);
NODE
}

for f in "${ROOT}"/assets/index-*.js; do
  [ -f "$f" ] && patch_file "$f"
done

echo "playback mode patch applied under ${ROOT}"
