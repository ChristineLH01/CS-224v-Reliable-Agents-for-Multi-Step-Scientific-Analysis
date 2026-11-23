import json, sys

# Generic exact-mass changes seen in drug biotransformation. Signed values
# are changes in precursor m/z for singly charged ions of the same adduct type.
SHIFTS = [
    (15.994915, "oxidation"), (-2.015650, "dehydrogenation"),
    (2.015650, "reduction"), (-14.015650, "demethylation"),
    (14.015650, "methylation"), (-28.031300, "dealkylation"),
    (176.032089, "glucuronide addition"), (-176.032089, "glucuronide loss"),
    (79.956815, "sulfate addition"), (-79.956815, "sulfate loss"),
    (42.010565, "acetylation"), (-42.010565, "deacetylation"),
    (57.021464, "glycine conjugation"), (-57.021464, "glycine conjugate loss"),
    (107.004100, "taurine conjugation"), (-107.004100, "taurine conjugate loss"),
    (305.068200, "glutathione conjugation"), (-305.068200, "glutathione conjugate loss"),
    (18.010565, "water addition"), (-18.010565, "water loss"),
]

with open(sys.argv[1], encoding="utf-8") as f:
    rows = json.load(f)

features = []
for r in rows:
    rt = r.get("rt", [None, None])
    fr = sorted((float(m), float(i)) for m, i in r.get("fragments", []))
    features.append({"id": r.get("id"), "mz": float(r["mz"]),
                     "rt": rt, "fr": fr})

def fragment_matches(a, b):
    """Shared qualifying product ions: (m/z, intensity in a, intensity in b)."""
    out, i, j = [], 0, 0
    while i < len(a) and j < len(b):
        ma, ia = a[i]
        mb, ib = b[j]
        tol = max(0.008, max(ma, mb) * 20e-6)
        if abs(ma - mb) <= tol:
            if (ma + mb) / 2 >= 60 and ia >= 2 and ib >= 2:
                out.append(((ma + mb) / 2, ia, ib))
            i += 1
            j += 1
        elif ma < mb:
            i += 1
        else:
            j += 1
    return out

def precursor_fragment_links(a, b):
    """Find either precursor represented as a product ion of the other."""
    found = []
    for label, prec, spectrum in (("a_product_matches_b_precursor", b["mz"], a["fr"]),
                                  ("b_product_matches_a_precursor", a["mz"], b["fr"])):
        for m, intensity in spectrum:
            if abs(m - prec) <= max(0.008, prec * 20e-6) and intensity >= 2:
                found.append((label, m, intensity))
                break
    return found

adj = [[] for _ in features]
for i in range(len(features)):
    a = features[i]
    for j in range(i + 1, len(features)):
        b = features[j]
        delta = b["mz"] - a["mz"]
        tol = max(0.0035, max(a["mz"], b["mz"]) * 10e-6)
        hits = [(abs(delta - shift), shift, label) for shift, label in SHIFTS
                if abs(delta - shift) <= tol]
        if not hits:
            continue
        _, shift, label = min(hits)
        shared = fragment_matches(a["fr"], b["fr"])
        cross = precursor_fragment_links(a, b)
        if not shared and not cross:
            continue
        edge = {"other": j, "delta": delta, "shift": label,
                "shared": shared, "cross": cross}
        adj[i].append(edge)
        adj[j].append({"other": i, "delta": -delta, "shift": label,
                       "shared": [(m, ib, ia) for m, ia, ib in shared],
                       "cross": cross})

candidates = []
for i, links in enumerate(adj):
    if not links:
        continue
    classes = {e["shift"] for e in links}
    # Relational completeness only; precursor and product-ion intensities do not
    # enter candidate ranking.
    support = sum(len(e["shared"]) + len(e["cross"]) for e in links)
    candidates.append(((-len(classes), -len(links), -support,
                        str(features[i]["id"])), i, links, len(classes), support))
candidates.sort(key=lambda x: x[0])

def rt_value(rt):
    if isinstance(rt, list):
        return [round(float(x), 3) if x is not None else None for x in rt]
    return rt

groups = []
for _, i, links, nclasses, support in candidates[:10]:
    root = features[i]
    # Show a compact evidence sample; the score and link count use every edge.
    shown = sorted(links, key=lambda e: (-(len(e["shared"]) + len(e["cross"])),
                                         e["shift"], str(features[e["other"]]["id"])))[:4]
    evidence = []
    for e in shown:
        other = features[e["other"]]
        evidence.append({
            "id": other["id"], "mz": round(other["mz"], 5),
            "rt": rt_value(other["rt"]), "delta": round(e["delta"], 5),
            "change": e["shift"],
            "shared_ions_mz_intensities": [[round(m, 4), round(ia, 1), round(ib, 1)]
                                               for m, ia, ib in e["shared"][:2]],
            "precursor_product_matches": [[label, round(m, 4), round(intensity, 1)]
                                            for label, m, intensity in e["cross"][:2]]
        })
    groups.append({
        "rank_criteria": [nclasses, len(links), support],
        "root": {"id": root["id"], "mz": round(root["mz"], 5),
                 "rt": rt_value(root["rt"])},
        "links_total": len(links), "shift_classes_total": nclasses,
        "evidence_sample": evidence
    })

result = {"ranking": "distinct shift classes, then supported links, then shared-ion evidence; no intensity ranking",
          "groups": groups}
text = json.dumps(result, separators=(",", ":"), ensure_ascii=False)
while len(text) > 5900 and result["groups"]:
    result["groups"].pop()
    text = json.dumps(result, separators=(",", ":"), ensure_ascii=False)
print(text)