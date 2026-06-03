# scripts/gen_seed.py — CSV → concept_map.json
import csv, json, sys, datetime


def parse_key(theme_key: str):
    if "/" in theme_key:
        parent, sub = theme_key.split("/", 1)
        return parent, sub
    return theme_key, None


def build_concept_map(rows):
    themes = {}   # parent_key -> theme dict
    order = []
    for r in rows:
        pkey, skey = parse_key(r["theme_key"].strip())
        if pkey not in themes:
            themes[pkey] = {"key": pkey, "name": r["theme"].strip(), "is_custom": False,
                            "sub_themes": [], "constituents": [], "_subidx": {}}
            order.append(pkey)
        t = themes[pkey]
        cons = {"code": r["code"].strip(), "name": r["name"].strip()}
        if skey:
            if skey not in t["_subidx"]:
                sub = {"key": skey, "name": r["sub_theme"].strip(), "constituents": []}
                t["_subidx"][skey] = sub
                t["sub_themes"].append(sub)
            t["_subidx"][skey]["constituents"].append(cons)
        else:
            t["constituents"].append(cons)
    for t in themes.values():
        t.pop("_subidx", None)
    return {"version": 1, "exported_at": datetime.date.today().isoformat(),
            "themes": [themes[k] for k in order]}


def main(csv_path, out_path):
    with open(csv_path, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    cm = build_concept_map(rows)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(cm, f, ensure_ascii=False, indent=2)
    print(f"wrote {len(cm['themes'])} themes -> {out_path}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
