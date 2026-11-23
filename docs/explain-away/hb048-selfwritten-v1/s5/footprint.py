import sys, json, itertools, collections

with open(sys.argv[1], encoding="utf-8") as fh:
    data = json.load(fh)

rows = []
for f in data:
    try:
        mz = float(f["mz"])
        rt = [float(x) for x in f.get("rt", [0, 0])]
        if len(rt) < 2:
            rt = [0.0, 0.0]
    except (KeyError, TypeError, ValueError):
        continue
    ions = {}
    for ion in f.get("fragments", []):
        try:
            x, intensity = float(ion[0]), float(ion[1])
        except (TypeError, ValueError, IndexError):
            continue
        # Bin product ions to 0.01 Da and keep the strongest entry per bin.
        k = int(round(x * 100))
        if k not in ions or intensity > ions[k][1]:
            ions[k] = (x, intensity)
    rows.append({"id": str(f.get("id", "?")), "mz": mz, "rt": rt,
                 "n": f.get("n_ms2", 0), "ions": ions})

# Collect features sharing each pair of product-ion bins.
motif_members = collections.defaultdict(list)
for i, row in enumerate(rows):
    for motif in itertools.combinations(sorted(row["ions"]), 2):
        motif_members[motif].append(i)

# Motifs with the same feature membership describe one candidate group.
groups = collections.defaultdict(set)
for motif, members in motif_members.items():
    if 2 <= len(members) <= 20:
        groups[tuple(members)].add(motif)

prevalence = collections.Counter(k for row in rows for k in row["ions"])
ranked = []
for members in groups:
    core = set(rows[members[0]]["ions"])
    for i in members[1:]:
        core.intersection_update(rows[i]["ions"])
    distinct_mz = len({round(rows[i]["mz"], 4) for i in members})
    if len(core) < 2 or distinct_mz < 2:
        continue

    shifts = collections.Counter()
    pairs = []
    overlaps = 0
    for i, j in itertools.combinations(members, 2):
        d = abs(rows[i]["mz"] - rows[j]["mz"])
        key = round(d, 2)
        shifts[key] += 1
        pairs.append((i, j, d, key))
        a, b = rows[i]["rt"], rows[j]["rt"]
        if max(a[0], b[0]) <= min(a[1], b[1]):
            overlaps += 1

    n_pairs = len(pairs)
    repeated = sum(n for n in shifts.values() if n >= 2)
    rare_core = sum(prevalence[k] <= max(3, len(rows) * 0.05) for k in core)
    score = (rare_core, len(core), repeated / n_pairs,
             overlaps / n_pairs, len(members), distinct_mz)
    ranked.append((score, members, core, shifts, pairs, overlaps, repeated))
ranked.sort(key=lambda x: x[0], reverse=True)

header = "Groups: shared product-ion bins, precursor m/z and pairwise differences (Da), and RT intervals."
out = [header]
for rank, item in enumerate(ranked[:10], 1):
    score, members, core, shifts, pairs, overlaps, repeated = item
    units = "; ".join(
        "{}:{:.4f}@{:.2f}-{:.2f}/MS2{}".format(
            rows[i]["id"], rows[i]["mz"], rows[i]["rt"][0],
            rows[i]["rt"][1], rows[i]["n"])
        for i in members)
    chosen = sorted(core, key=lambda k: (prevalence[k], k))[:4]
    ion_evidence = []
    for k in chosen:
        vals = [rows[i]["ions"][k] for i in members]
        mean_mz = sum(v[0] for v in vals) / len(vals)
        mean_int = sum(v[1] for v in vals) / len(vals)
        ion_evidence.append("{:.4f} (mean {:.1f}%)".format(mean_mz, mean_int))
    ordered = sorted(pairs, key=lambda p: (shifts[p[3]] < 2,
                                           -shifts[p[3]], p[3]))
    pair_evidence = ["{}-{}:{:.4f}".format(rows[i]["id"], rows[j]["id"], d)
                     for i, j, d, key in ordered[:5]]
    shift_evidence = ["{:.2f}x{}".format(d, n) for d, n in
                      sorted(shifts.items(), key=lambda x: (-x[1], x[0]))[:3]]
    entry = ("G{} n={} shared_ions={} repeated_delta_pairs={} RT_overlap={}/{}\n"
             "  units: {}\n  shared ions: {}\n"
             "  pair delta m/z: {}; delta bins: {}".format(
                 rank, len(members), len(core), repeated, overlaps, len(pairs),
                 units, ", ".join(ion_evidence), ", ".join(pair_evidence),
                 ", ".join(shift_evidence)))
    candidate = "\n".join(out + [entry])
    if len(candidate) > 6000:
        compact = "; ".join("{}:{:.4f}".format(rows[i]["id"], rows[i]["mz"])
                             for i in members)
        entry = ("G{} n={} shared_ions={} repeated_delta_pairs={} RT_overlap={}/{}\n"
                 "  units: {}\n  shared ions: {}\n  pair delta m/z: {}".format(
                     rank, len(members), len(core), repeated, overlaps,
                     len(pairs), compact, ", ".join(ion_evidence[:2]),
                     ", ".join(pair_evidence[:2])))
        candidate = "\n".join(out + [entry])
    if len(candidate) > 6000:
        break
    out.append(entry)

if len(out) == 1:
    out.append("No groups with at least two shared product-ion bins and distinct precursor masses were found.")
print("\n".join(out))