#!/usr/bin/env python3
"""One-shot SAM PR1/PR2 acceptance checks against a running Label Studio instance."""
import json
import os
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

BASE = os.environ.get("LS_URL", "http://127.0.0.1:8000/api").rstrip("/")
if not BASE.endswith("/api"):
    BASE = BASE + "/api"
TOKEN = os.environ["LS_TOKEN"]
PROJECT_ID = int(os.environ.get("LS_PROJECT_ID", "1"))
TASK_ID = int(os.environ.get("LS_TASK_ID", "6"))
HDR = {"Authorization": f"Token {TOKEN}", "Content-Type": "application/json"}


def req(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(f"{BASE}{path}", data=data, headers=HDR, method=method)
    try:
        with urllib.request.urlopen(request, timeout=180) as resp:
            payload = resp.read().decode()
            return (json.loads(payload) if payload else {}), resp.status
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode()
        try:
            return json.loads(detail), exc.code
        except json.JSONDecodeError:
            return {"detail": detail[:300]}, exc.code


def label_signatures(xml):
    root = ET.fromstring(xml)
    sigs = {}
    for tag in ("BrushLabels", "KeyPointLabels", "RectangleLabels"):
        for el in root.iter(tag):
            name = el.get("name")
            if name in ("tag", "tag2", "tag3"):
                sigs[name] = [(x.get("value"), x.get("background")) for x in el.findall("Label")]
    return sigs, root.get("samManaged") == "true"


def groups_match(xml):
    sigs, sam = label_signatures(xml)
    brush = sigs.get("tag", [])
    return sam and brush == sigs.get("tag2", []) == sigs.get("tag3", []) and len(brush) > 0, sigs


def main():
    results = []
    base_classes = [
        {"value": "Bowl", "background": "#FFA39E"},
        {"value": "Cup", "background": "#40A9FF"},
        {"value": "Knife", "background": "#FFC069"},
        {"value": "Plate", "background": "#B37FEB"},
        {"value": "Fork", "background": "#73D13D"},
        {"value": "Spoon", "background": "#FF85C0"},
    ]
    classes = base_classes + [{"value": "Mango", "background": "#36CFC9"}]

    put_res, code = req("PUT", f"/projects/{PROJECT_ID}/sam-classes/", {"classes": classes})
    proj, _ = req("GET", f"/projects/{PROJECT_ID}/")
    ok, sigs = groups_match(proj["label_config"])
    t1 = code == 200 and put_res.get("sam_managed") and ok and "Mango" in [x[0] for x in sigs["tag"]]
    results.append(("TEST1 add class + three-group sync", t1))
    print("TEST1", "PASS" if t1 else "FAIL", "| labels:", [x[0] for x in sigs.get("tag", [])])

    root = ET.fromstring(proj["label_config"])
    for el in root.iter("KeyPointLabels"):
        if el.get("name") == "tag2" and el.find("Label") is not None:
            el.find("Label").set("background", "#000000")
    req("PATCH", f"/projects/{PROJECT_ID}/", {"label_config": ET.tostring(root, encoding="unicode")})
    out_sync, _ = req("GET", f"/projects/{PROJECT_ID}/sam-classes/")
    req("PUT", f"/projects/{PROJECT_ID}/sam-classes/", {"classes": classes})
    in_sync, _ = req("GET", f"/projects/{PROJECT_ID}/sam-classes/")
    proj2, _ = req("GET", f"/projects/{PROJECT_ID}/")
    ok2, _ = groups_match(proj2["label_config"])
    t2 = out_sync.get("in_sync") is False and in_sync.get("in_sync") is True and ok2
    results.append(("TEST2 out-of-sync + resync", t2))
    print("TEST2", "PASS" if t2 else "FAIL")

    ml, _ = req("GET", f"/ml?project={PROJECT_ID}")
    backend_id = next(b["id"] for b in ml if b.get("is_interactive"))
    pred, code = req(
        "POST",
        f"/ml/{backend_id}/interactive-annotating",
        {
            "task": TASK_ID,
            "context": {
                "result": [
                    {
                        "id": "kp1",
                        "type": "keypointlabels",
                        "from_name": "tag2",
                        "to_name": "image",
                        "original_width": 800,
                        "original_height": 600,
                        "image_rotation": 0,
                        "value": {
                            "x": 50.0,
                            "y": 50.0,
                            "width": 0.01,
                            "keypointlabels": ["Bowl"],
                        },
                    }
                ]
            },
        },
    )
    if pred.get("errors"):
        t3 = False
        print("TEST3 FAIL errors:", pred["errors"])
    else:
        data = pred.get("data", {})
        results_list = data.get("result", data) if isinstance(data, dict) else data
        brush = [x for x in results_list if x.get("type") == "brushlabels"]
        t3 = code == 200 and len(brush) >= 1
        print("TEST3", "PASS" if t3 else "FAIL", "| brush:", len(brush))
    results.append(("TEST3 SAM interactive mask", t3))

    print("\nSUMMARY")
    for name, passed in results:
        print(f"  [{'OK' if passed else 'FAIL'}] {name}")
    if not all(p for _, p in results):
        sys.exit(1)


if __name__ == "__main__":
    main()
