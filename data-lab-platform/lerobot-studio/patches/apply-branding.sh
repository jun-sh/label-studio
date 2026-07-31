#!/bin/sh
# Neutralize upstream strings in downloaded static assets.
set -eu

ROOT="${1:-/srv/lerobot}"
[ -d "$ROOT" ] || exit 0

SUBTITLE_EN="Preview robot datasets in browser"
BUNDLED_ZIP="/lerobot/bundled/sensexperience_ego.zip"

patch_file() {
  f="$1"
  [ -f "$f" ] || return 0
  sed -i \
    -e 's/Access more advanced capabilities in EmbodiFlow//g' \
    -e 's/EmbodiFlow//g' \
    -e 's/IO-AI\.TECH//g' \
    -e 's/io-ai\.tech//g' \
    -e 's/LeRobot Studio/Data Viewer/g' \
    -e 's/LeRobot Visualizer/Data Viewer/g' \
    -e 's/具身智能数据集可视化平台/Data Viewer/g' \
    -e "s/Visualize LeRobot datasets locally, right in your browser/${SUBTITLE_EN}/g" \
    -e 's/Preview and play robot datasets in your browser/Preview robot datasets in browser/g' \
    -e 's/本地浏览器一键预览与播放机器人数据集/Preview robot datasets in browser/g' \
    -e 's/预览与播放机器人数据集/Preview robot datasets in browser/g' \
    -e 's/Click a card to 打开数据set/Click a card to open dataset/g' \
    -e 's/打开数据set/open dataset/g' \
    -e 's/companyName:`艾欧智能`/companyName:`Data Lab`/g' \
    -e 's/companyName:``/companyName:`Data Lab`/g' \
    -e 's/companyWebsite:`https:\/\/`/companyWebsite:`\/`/g' \
    -e "s#w=d||v(\`common.companyName\`)#w=d||v(\`common.appName\`)#g" \
    -e 's|https://huggingface.co/datasets/lerobot/lerobot/resolve/main/lerobotv3.zip|'"${BUNDLED_ZIP}"'|g' \
    -e 's/samples:{title:`样例数据`,description:`点击卡片浏览数据集`/samples:{title:`当前数据集`,description:`点击卡片浏览数据集`/g' \
    -e 's/samples:{title:`样例数据集`,description:`Click a card to open dataset`/samples:{title:`Current Datasets`,description:`Click a card to open dataset`/g' \
    -e 's/LeRobot v3 Sample/v3 样例数据集/g' \
    -e 's/LeRobot v3/v3/g' \
    -e 's/languages:{zh:`中文`,en:`English`,ja:`日本語`}/languages:{zh:`中文`,en:`English`}/g' \
    -e 's/,{code:`ja`,labelKey:`common.languages.ja`}//g' \
    -e 's/无需上传，直接在浏览器中查看 LeRobot 数据集/无需上传，直接在浏览器中查看数据集/g' \
    -e 's|if(t.startsWith(`sample://`)){let e=yo(t.replace(/^sample:\/\//,``));return{kind:`sample`,raw:t,sampleId:e,hint:e}}return{kind:`unknown`,raw:t}}|if(t.startsWith(`sample://`)){let e=yo(t.replace(/^sample:\/\//,``));return{kind:`sample`,raw:t,sampleId:e,hint:e}}if(t.includes(`/api/stream/`)){let e=t.startsWith(`http`)?t:new URL(t,window.location.origin).href;return{kind:`httpDataset`,raw:e}}return{kind:`unknown`,raw:t}}|g' \
    -e 's|async openFromUrl(e,t=`replace`){let n=vo(e||``);if(n.kind===`remoteArchive`)|async openFromUrl(e,t=`replace`){let n=vo(e||``);if(n.kind===`httpDataset`&&typeof window.__datalabOpenHttpDataset==`function`){this.deps.setWelcomeRequest(null),await window.__datalabOpenHttpDataset(this,n.raw,t);return}if(n.kind===`remoteArchive`)|g' \
    -e 's/selectAll:`全选`/selectAll:`显示全部通道`/g' \
    -e 's/selectAll:`Select All`/selectAll:`Show all channels`/g' \
  "$f" || true
}

patch_file "${ROOT}/index.html"
for f in "${ROOT}"/assets/index-*.js; do
  [ -f "$f" ] && patch_file "$f"
done

sh "$(dirname "$0")/patch-dockview-scalar-chart.sh" "${ROOT}"
sh "$(dirname "$0")/patch-playback-mode.sh" "${ROOT}"
node "$(dirname "$0")/patch-lerobot-manifest.mjs" "${ROOT}" || true
node "$(dirname "$0")/patch-lerobot-stream.mjs" "${ROOT}" || true

echo "Branding patch applied under ${ROOT}"
