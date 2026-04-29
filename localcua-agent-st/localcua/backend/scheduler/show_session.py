
# Purpose: Prints a saved session file in a human-readable summary format.
import json

s = json.load(open("sessions/test_session.json"))
print(f"Session: {s['name']}")
print(f"Platform: {s['platform']}")
print(f"Screen: {s['screen_width']}x{s['screen_height']}")
print(f"Total packets: {s['packet_count']}")
print()

counts = {}
for p in s["packets"]:
    t = p["action_type"]
    counts[t] = counts.get(t, 0) + 1
print("Action breakdown:")
for k, v in sorted(counts.items(), key=lambda x: -x[1]):
    print(f"  {k:20s} {v}")
print()

print("Non-hover packets:")
print("-" * 90)
for p in s["packets"]:
    if p["action_type"] == "hover":
        continue
    sem = p.get("semantic", {})
    win = sem.get("window_title", "")[:50]
    proc = sem.get("process_name", "")
    x = p.get("x", "-")
    y = p.get("y", "-")
    btn = p.get("button", "")
    text = p.get("text", "")
    keys = p.get("keys", [])
    detail = ""
    if btn:
        detail = f"btn={btn}"
    elif text:
        detail = f'text="{text}"'
    elif keys:
        detail = f"keys={'+'.join(keys)}"
    has_vis = "before" in p.get("visual", {}).get("before_b64", "")
    vis_tag = "[IMG]" if p.get("visual", {}).get("before_b64") else ""

    print(f"  #{p['seq']:3d} | {p['action_type']:10s} | x={str(x):>5} y={str(y):>5} | {detail:20s} | {vis_tag:5s} | {win}")

print()
size_kb = len(json.dumps(s)) / 1024
print(f"Session file size: {size_kb:.0f} KB")
