import json
import sys
import math
from collections import defaultdict, Counter
from itertools import combinations

# Generic mass shifts associated with common biotransformations; not candidate-specific.
TRANSFORMS = [
    ('oxidation/hydroxylation', 15.994915),
    ('methylation', 14.015650),
    ('reduction', 2.015650),
    ('acetylation', 42.010565),
    ('hydrolysis', 18.010565),
    ('hexose conjugation', 162.052824),
    ('glucuronide conjugation', 176.032088),
    ('sulfate conjugation', 79.956815),
    ('glycine conjugation', 57.021464),
]
# These precursor differences commonly indicate isotopes/adducts, not metabolites.
ARTIFACT_DELTAS = [1.003355, 2.006710, 17.026549, 21.981942, 37.955882]


def close(a, b, floor=0.0015, ppm=15):
    return abs(a - b) <= max(floor, max(abs(a), abs(b)) * ppm * 1e-6)


def center(rt):
    return (rt[0] + rt[1]) / 2.0


def main():
    if len(sys.argv) < 2:
        print('[]')
        return
    with open(sys.argv[1], encoding='utf-8') as f:
        raw = json.load(f)

    feats = []
    for row in raw:
        try:
            mz = float(row['mz'])
            rt = [float(row['rt'][0]), float(row['rt'][1])]
            frags = []
            for item in row.get('fragments', []):
                fm = float(item[0])
                # Discard precursor carry-through and products above a singly
                # charged precursor; retain the recorded relative intensities.
                if 40 <= fm < mz - 0.01:
                    frags.append((fm, float(item[1])))
            if mz > 0:
                feats.append({
                    'id': str(row.get('id', '?')), 'mz': mz, 'rt': rt,
                    'n_ms2': int(row.get('n_ms2', 0)), 'frags': frags
                })
        except (KeyError, TypeError, ValueError, IndexError):
            continue

    n = len(feats)
    if n < 2:
        print('[]')
        return

    # Suppress frequently recurring product-ion masses so ubiquitous signals
    # do not dominate pair formation. Rounded bins are only for prevalence;
    # actual matches below use a mass tolerance.
    prevalence = Counter()
    feature_bins = []
    for feat in feats:
        bins = {round(m, 2) for m, _ in feat['frags']}
        feature_bins.append(bins)
        prevalence.update(bins)
    common_cutoff = max(4, math.ceil(0.25 * n))

    # Generate possible pairs from informative bins and neighboring bins;
    # neighboring bins accommodate rounding at bin boundaries.
    postings = defaultdict(set)
    for i, bins in enumerate(feature_bins):
        for b in bins:
            if prevalence[b] <= common_cutoff:
                k = int(round(b * 100))
                for neighbor in (k - 1, k, k + 1):
                    postings[neighbor].add(i)
    candidate_pairs = set()
    for members in postings.values():
        if len(members) > 1:
            candidate_pairs.update(combinations(sorted(members), 2))

    def transform_for(delta):
        for label, expected in TRANSFORMS:
            if abs(delta - expected) <= max(0.003, expected * 25e-6):
                return label
        return None

    def artifact_delta(delta):
        return any(abs(delta - x) <= max(0.003, x * 20e-6)
                   for x in ARTIFACT_DELTAS)

    edges = []
    for i, j in sorted(candidate_pairs):
        a, b = feats[i], feats[j]
        delta = abs(a['mz'] - b['mz'])
        if artifact_delta(delta):
            continue
        label = transform_for(delta)

        # Greedy one-to-one matching prevents one product ion matching twice.
        fa = sorted(a['frags'])
        fb = sorted(b['frags'])
        x = y = 0
        shared = []
        while x < len(fa) and y < len(fb):
            ma, ia = fa[x]
            mb, ib = fb[y]
            tol = max(0.0015, max(ma, mb) * 15e-6)
            if abs(ma - mb) <= tol:
                ba, bb = round(ma, 2), round(mb, 2)
                if min(prevalence[ba], prevalence[bb]) <= common_cutoff:
                    shared.append(round((ma + mb) / 2.0, 4))
                x += 1
                y += 1
            elif ma < mb:
                x += 1
            else:
                y += 1

        rt_sep = abs(center(a['rt']) - center(b['rt']))
        # A plausible transformation needs at least one shared product ion.
        # Otherwise require several shared ions and distinct RTs to reduce
        # coeluting-adduct and nonspecific-similarity explanations.
        if (label and shared) or (len(shared) >= 3 and rt_sep > 0.25):
            edges.append({
                'i': i, 'j': j, 'shared': sorted(set(shared)),
                'transform': label, 'delta': round(delta, 5),
                'rt_sep': round(rt_sep, 3)
            })

    adjacency = defaultdict(set)
    for e in edges:
        adjacency[e['i']].add(e['j'])
        adjacency[e['j']].add(e['i'])

    components = []
    seen = set()
    for start in sorted(adjacency):
        if start in seen:
            continue
        stack, members = [start], set()
        while stack:
            node = stack.pop()
            if node in members:
                continue
            members.add(node)
            seen.add(node)
            stack.extend(adjacency[node] - members)
        group_edges = [e for e in edges if e['i'] in members and e['j'] in members]
        if len(members) >= 2 and group_edges:
            components.append((members, group_edges))

    ranked = []
    for members, es in components:
        transform_edges = sum(e['transform'] is not None for e in es)
        multi_edges = sum(len(e['shared']) >= 2 for e in es)
        rt_edges = sum(e['rt_sep'] > 0.25 for e in es)
        unique_ions = sorted({ion for e in es for ion in e['shared']})
        criteria = {
            'multiple_precursor_features': len(members) >= 2,
            'repeated_shared_fragments': multi_edges > 0,
            'plausible_biotransformation_difference': transform_edges > 0,
            'rt_separated_relation': rt_edges > 0,
        }
        completeness = sum(criteria.values()) / len(criteria)
        ranked.append((
            (-completeness, -transform_edges, -multi_edges,
             -len(unique_ions), -len(members),
             tuple(sorted(feats[i]['id'] for i in members))),
            members, es, criteria, unique_ions,
            transform_edges, multi_edges, rt_edges
        ))
    ranked.sort(key=lambda x: x[0])

    results = []
    for _, members, es, criteria, ions, transform_edges, multi_edges, rt_edges in ranked:
        ordered = sorted(members, key=lambda i: (feats[i]['mz'], feats[i]['id']))
        examples = sorted(
            es, key=lambda e: (e['transform'] is None, -len(e['shared']),
                               feats[e['i']]['id'], feats[e['j']]['id'])
        )[:8]
        item = {
            'criteria_met': sum(criteria.values()),
            'criteria_total': len(criteria),
            'criteria': criteria,
            'features': [
                {'id': feats[i]['id'], 'mz': round(feats[i]['mz'], 5),
                 'rt': [round(feats[i]['rt'][0], 3), round(feats[i]['rt'][1], 3)],
                 'n_ms2': feats[i]['n_ms2']}
                for i in ordered
            ],
            'relations': {
                'pair_links': len(es),
                'transformation_links': transform_edges,
                'links_with_at_least_2_shared_fragments': multi_edges,
                'rt_separated_links': rt_edges,
                'distinct_shared_fragment_mz': ions[:12],
                'examples': [
                    {'features': [feats[e['i']]['id'], feats[e['j']]['id']],
                     'precursor_delta_mz': e['delta'],
                     'transformation': e['transform'],
                     'shared_fragment_mz': e['shared'][:8],
                     'rt_center_difference': e['rt_sep']}
                    for e in examples
                ]
            }
        }
        # Keep the complete groups and evidence within the required output cap.
        trial = results + [item]
        text = json.dumps(trial, separators=(',', ':'), ensure_ascii=False)
        if len(trial) <= 10 and len(text) <= 5999:
            results.append(item)
        if len(results) >= 10:
            break
    print(json.dumps(results, separators=(',', ':'), ensure_ascii=False))


if __name__ == '__main__':
    main()
