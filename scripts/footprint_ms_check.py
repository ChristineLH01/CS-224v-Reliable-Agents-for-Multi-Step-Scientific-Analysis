"""Offline feasibility check: can a generic 'administered compound' footprint predicate
surface the right precursor family in an mzML file without using intensity?

Pre-registered rules (fixed before looking at the result):
  features   MS2 precursors merged within 5 ppm; consensus fragments = peaks >= 5% of the
             scan's base peak, m/z >= 100, present in >= 50% of the feature's MS2 scans
  generic    a fragment (0.003 Da bins) shared by > 10% of all features is dropped
  adduct     |dm| = Na-H, K-H, NH4-H: same compound, merged, never counted as a metabolite
  edge       |dm| matches a standard phase I/II biotransformation (list below, 5 ppm of the
             larger mass) AND the two features share >= 1 non-generic consensus fragment
  family     connected component over edges; footprint score = members, then edges
  intensity  not used for ranking (reported only, for contrast)
Stdlib only. Usage: python3 scripts/footprint_ms_check.py <file.mzML>
"""
import base64, struct, sys, zlib
import xml.etree.ElementTree as ET
from collections import defaultdict

PPM = 5e-6
BIOTRANSFORMATIONS = {  # standard drug-metabolism mass deltas (monoisotopic, Da)
    "+O (hydroxylation / N-oxide)": 15.99491, "+2O": 31.98983, "-CH2 (demethylation)": 14.01565,
    "-C2H4 (deethylation)": 28.03130, "+/-H2": 2.01565, "+H2O (hydration)": 18.01056,
    "+glucuronide": 176.03209, "+sulfate": 79.95682, "+acetyl": 42.01057, "+glycine": 57.02146,
    "+glutathione": 305.06808, "+O+glucuronide": 192.02700, "-CH2+glucuronide": 162.01644,
}
ADDUCTS = {"Na-H": 21.98194, "K-H": 37.95588, "NH4-H": 17.02655}


class _Patched:
    """The scrubbed files use `xsi:` on the root before declaring it; strict XML refuses that."""
    def __init__(self, path):
        self.f, self.first = open(path, "rb"), True
    def read(self, n=-1):
        data = self.f.read(n if n and n > 0 else 1 << 20)
        if self.first:
            self.first = False
            data = data.replace(b'<indexedmzML xmlns="UNK" xsi:schemaLocation="UNK">',
                                b'<indexedmzML xmlns="UNK">', 1)
        return data


def spectra(path):
    for _, el in ET.iterparse(_Patched(path), events=("end",)):
        if not el.tag.endswith("}spectrum") and el.tag != "spectrum":
            continue
        cv = {}
        for p in el.iter():
            if p.tag.endswith("cvParam"):
                cv.setdefault(p.get("name"), p.get("value"))
        if cv.get("ms level") != "2":
            el.clear(); continue
        arrays = {}
        for b in el.iter():
            if not b.tag.endswith("binaryDataArray"):
                continue
            names = {p.get("name") for p in b.iter() if p.tag.endswith("cvParam")}
            raw = base64.b64decode(next(x for x in b.iter() if x.tag.endswith("binary")).text or "")
            if "zlib compression" in names:
                raw = zlib.decompress(raw)
            fmt = "d" if "64-bit float" in names else "f"
            vals = struct.unpack(f"<{len(raw) // struct.calcsize(fmt)}{fmt}", raw)
            arrays["mz" if "m/z array" in names else "i"] = vals
        yield dict(prec=float(cv["selected ion m/z"]), pint=float(cv.get("peak intensity") or 0),
                   rt=float(cv.get("scan start time") or 0), mz=arrays.get("mz", ()), it=arrays.get("i", ()))
        el.clear()


def main(path):
    scans = list(spectra(path))
    feats = []  # merge precursors within 5 ppm
    for s in sorted(scans, key=lambda s: s["prec"]):
        if feats and abs(s["prec"] - feats[-1]["mz"]) <= PPM * s["prec"]:
            feats[-1]["scans"].append(s)
        else:
            feats.append({"mz": s["prec"], "scans": [s]})
    for f in feats:
        f["mz"] = sum(s["prec"] for s in f["scans"]) / len(f["scans"])
        f["pint"] = max(s["pint"] for s in f["scans"])
        counts = defaultdict(int)
        for s in f["scans"]:
            if not s["it"]:
                continue
            base = max(s["it"])
            bins = {round(m / 0.003) for m, i in zip(s["mz"], s["it"]) if i >= 0.05 * base and m >= 100}
            for b in bins:
                counts[b] += 1
        f["frags"] = {b for b, c in counts.items() if c >= 0.5 * len(f["scans"])}
    share = defaultdict(int)
    for f in feats:
        for b in f["frags"]:
            share[b] += 1
    generic = {b for b, c in share.items() if c > 0.10 * len(feats)}

    def common(a, b):  # tolerant to bin edges
        fa = a["frags"] - generic
        return {x for x in fa if ({x - 1, x, x + 1} & (b["frags"] - generic))}

    # adduct merge (union-find)
    parent = list(range(len(feats)))
    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]; i = parent[i]
        return i
    for i, a in enumerate(feats):
        for j in range(i + 1, len(feats)):
            d = feats[j]["mz"] - a["mz"]
            if d > 40: break
            if any(abs(d - v) <= PPM * feats[j]["mz"] for v in ADDUCTS.values()):
                parent[find(j)] = find(i)
    edges = []
    for i, a in enumerate(feats):
        for j in range(i + 1, len(feats)):
            d = feats[j]["mz"] - a["mz"]
            if d > 310: break
            for name, v in BIOTRANSFORMATIONS.items():
                if abs(d - v) <= PPM * feats[j]["mz"] and find(i) != find(j):
                    sh = common(a, feats[j])
                    if sh:
                        edges.append((i, j, name, sorted(round(x * 0.003, 3) for x in sh)))
    fam_parent = list(range(len(feats)))
    def ffind(i):
        while fam_parent[i] != i:
            fam_parent[i] = fam_parent[fam_parent[i]]; i = fam_parent[i]
        return i
    for i, j, *_ in edges:
        fam_parent[ffind(j)] = ffind(i)
    fams = defaultdict(set)
    for i, j, *_ in edges:
        fams[ffind(i)] |= {i, j}
    ranked = sorted(fams.values(), key=lambda m: (len(m), sum(1 for e in edges if e[0] in m)), reverse=True)

    print(f"{len(scans)} MS2 scans -> {len(feats)} precursor features; {len(generic)} generic fragment bins dropped; "
          f"{len(edges)} footprint edges; {len(ranked)} families")
    by_int = sorted(range(len(feats)), key=lambda i: feats[i]["pint"], reverse=True)
    for k, m in enumerate(ranked[:8], 1):
        print(f"\n#{k} family, {len(m)} members:")
        for i in sorted(m, key=lambda i: feats[i]["mz"]):
            f = feats[i]
            print(f"   m/z {f['mz']:.4f}  MS2 scans {len(f['scans']):3d}  max prec int {f['pint']:.2e}  "
                  f"(intensity rank {by_int.index(i) + 1}/{len(feats)})")
        for i, j, name, sh in edges:
            if i in m:
                print(f"   edge {feats[i]['mz']:.4f} -> {feats[j]['mz']:.4f}: {name}; shared fragments {sh[:4]}")


if __name__ == "__main__":
    main(sys.argv[1])
