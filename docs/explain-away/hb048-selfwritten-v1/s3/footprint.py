import sys, json, math
from collections import defaultdict

# Signed mass changes from a proposed parent to a related feature.
TRANSFORMS = [
    ('oxygenation', 15.994915), ('hydrogenation', 2.015650),
    ('dehydrogenation', -2.015650), ('methylation', 14.015650),
    ('demethylation', -14.015650), ('glucuronidation', 176.032090),
    ('sulfation', 79.956815), ('acetylation', 42.010565),
    ('deacetylation', -42.010565), ('hydration', 18.010565),
    ('dehydration', -18.010565), ('glycine_conjugation', 57.021464),
    ('cysteine_conjugation', 119.004100),
    ('glutathione_conjugation', 305.068160),
]

with open(sys.argv[1], encoding='utf-8') as f:
    rows = json.load(f)

# Centi-Dalton bins permit a small tolerance for product-ion matching.
spectra = []
for r in rows:
    d = {}
    for m, inten in r.get('fragments', []):
        m = float(m)
        if 40 <= m <= float(r['mz']) + 0.02:
            b = round(m * 100)
            if b not in d or float(inten) > d[b][1]:
                d[b] = (m, float(inten))
    spectra.append(d)

post = defaultdict(set)
for i, d in enumerate(spectra):
    for b in d:
        post[b].add(i)
# Snapshot the keys before neighbor lookups: defaultdict lookups may add keys.
freq = {b: len(post[b-1] | post[b] | post[b+1]) for b in list(post)}
rare_cut = max(4, math.ceil(0.08 * len(rows)))

edges = defaultdict(list)
for i in range(len(rows)):
    for j in range(i + 1, len(rows)):
        shared, used = [], set()
        for b, (mi, ii) in spectra[i].items():
            choices = [c for c in (b-1, b, b+1)
                       if c in spectra[j] and c not in used]
            if not choices:
                continue
            c = min(choices, key=lambda x: abs(x-b))
            used.add(c)
            mj, ij = spectra[j][c]
            common = min(freq.get(b, len(rows)), freq.get(c, len(rows)))
            shared.append((common <= rare_cut, mi, mj, ii, ij, common))
        if len(shared) < 2 or not any(x[0] for x in shared):
            continue
        delta = float(rows[j]['mz']) - float(rows[i]['mz'])
        for parent, child, change in ((i, j, delta), (j, i, -delta)):
            for label, expected in TRANSFORMS:
                if abs(change - expected) <= max(0.008, abs(expected) * 20e-6):
                    edges[parent].append((child, label, change, shared))
                    break

# A group is a proposed parent and up to eight directly related features.
# Rank on structural coverage, never on signal intensity.
groups = {}
for p, es in edges.items():
    es.sort(key=lambda e: (len(e[3]), sum(x[0] for x in e[3])), reverse=True)
    es = es[:8]
    if not es:
        continue
    labels = {e[1] for e in es}
    score = (len(es), len(labels), sum(len(e[3]) for e in es),
             sum(sum(x[0] for x in e[3]) for e in es))
    units_idx = [p] + [e[0] for e in es]
    key = tuple(sorted(str(rows[k]['id']) for k in units_idx))
    units = [{'id': rows[k]['id'], 'mz': rows[k]['mz'],
              'rt': rows[k].get('rt'), 'n_ms2': rows[k].get('n_ms2')}
             for k in units_idx]
    rels = []
    for child, label, delta, ions in es:
        ions.sort(key=lambda x: (not x[0], x[5]))
        rels.append({'parent': rows[p]['id'], 'related_unit': rows[child]['id'],
                     'transformation': label, 'delta_mz': round(delta, 5),
                     'shared_ions': [[round(x[1], 4), round(x[2], 4),
                                      round(x[3], 1), round(x[4], 1), x[5]]
                                     for x in ions[:4]],
                     'n_shared_ions': len(ions)})
    rec = {'rank_relations_types_shared_ions_rare_ions': list(score),
           'units': units, 'relations': rels}
    if key not in groups or score > groups[key][0]:
        groups[key] = (score, rec)

ranked = [v[1] for v in sorted(groups.values(),
         key=lambda x: x[0], reverse=True)[:10]]
out = {'groups': ranked}
text = json.dumps(out, separators=(',', ':'))
while len(text) > 6000 and out['groups']:
    out['groups'].pop()
    text = json.dumps(out, separators=(',', ':'))
print(text)