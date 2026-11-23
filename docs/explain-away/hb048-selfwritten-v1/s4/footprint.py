#!/usr/bin/env python3
import json
import math
import sys
from bisect import bisect_left, bisect_right
from collections import Counter, defaultdict, deque

# Common accurate-mass differences associated with small-molecule
# biotransformations. These are analytical relations, not candidate names.
DELTAS = (2.01565, 14.01565, 15.99491, 28.03130, 42.01056,
          43.98983, 57.02146, 79.95682, 132.04226, 162.05282, 176.03209)


def load_features(path):
    with open(path, encoding='utf-8') as handle:
        rows = json.load(handle)
    out = []
    if not isinstance(rows, list):
        return out
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            mz = float(row['mz'])
            rt = row.get('rt', [])
            rt = [float(x) for x in rt[:2]] if isinstance(rt, list) else []
            raw = row.get('fragments', [])
            frags = sorted((float(p[0]), float(p[1])) for p in raw
                           if isinstance(p, list) and len(p) >= 2)
            out.append({'id': str(row.get('id', 'row' + str(len(out) + 1))),
                        'mz': mz, 'rt': rt,
                        'n': int(row.get('n_ms2', 0)), 'frags': frags})
        except (TypeError, ValueError, KeyError, IndexError):
            continue
    return out


def shared_rare_fragments(a, b, prevalence, rare_limit):
    x, y = a['frags'], b['frags']
    i = j = 0
    found = []
    while i < len(x) and j < len(y):
        mx, ix = x[i]
        my, iy = y[j]
        tol = max(0.003, max(mx, my) * 20e-6)
        if abs(mx - my) <= tol:
            # Use the more prevalent of the two nearby bins conservatively.
            freq = max(prevalence[round(mx, 2)], prevalence[round(my, 2)])
            if freq <= rare_limit:
                found.append(((mx + my) / 2.0, ix, iy))
            i += 1
            j += 1
        elif mx < my:
            i += 1
        else:
            j += 1
    return found


def main(path):
    features = load_features(path)
    if len(features) < 2:
        return

    prevalence = Counter()
    for feature in features:
        for mz, intensity in feature['frags']:
            prevalence[round(mz, 2)] += 1
    rare_limit = max(6, math.ceil(0.12 * len(features)))

    order = sorted(range(len(features)), key=lambda k: features[k]['mz'])
    masses = [features[k]['mz'] for k in order]
    edge_map = {}
    for pos, i in enumerate(order):
        low = features[i]['mz']
        for delta in DELTAS:
            tol = max(0.003, delta * 15e-6)
            left = bisect_left(masses, low + delta - tol)
            right = bisect_right(masses, low + delta + tol)
            for q in range(max(pos + 1, left), right):
                j = order[q]
                observed = features[j]['mz'] - low
                if abs(observed - delta) > tol:
                    continue
                shared = shared_rare_fragments(features[i], features[j],
                                                prevalence, rare_limit)
                strong_one = any(min(v1, v2) >= 20.0 for _, v1, v2 in shared)
                if len(shared) < 2 and not strong_one:
                    continue
                key = (min(i, j), max(i, j))
                edge = {'i': key[0], 'j': key[1], 'delta': observed,
                        'shared': shared}
                old = edge_map.get(key)
                if old is None or abs(observed - delta) < abs(old['delta'] - delta):
                    edge_map[key] = edge

    adjacency = defaultdict(list)
    for edge in edge_map.values():
        adjacency[edge['i']].append(edge['j'])
        adjacency[edge['j']].append(edge['i'])

    groups = []
    visited = set()
    for start in adjacency:
        if start in visited:
            continue
        todo = deque([start])
        visited.add(start)
        nodes = []
        while todo:
            node = todo.popleft()
            nodes.append(node)
            for other in adjacency[node]:
                if other not in visited:
                    visited.add(other)
                    todo.append(other)
        node_set = set(nodes)
        edges = [e for e in edge_map.values()
                 if e['i'] in node_set and e['j'] in node_set]
        if len(nodes) >= 2 and edges:
            delta_types = len({round(e['delta'], 2) for e in edges})
            shared_count = sum(len(e['shared']) for e in edges)
            # Completeness only: no precursor or fragment intensity ranking.
            groups.append((nodes, edges,
                           (len(nodes), delta_types, len(edges), shared_count)))

    groups.sort(key=lambda g: g[2], reverse=True)
    output = []
    for rank, (nodes, edges, score) in enumerate(groups[:10], 1):
        units = []
        for k in sorted(nodes, key=lambda z: features[z]['mz'])[:5]:
            f = features[k]
            rt = ('%.2f-%.2f' % (f['rt'][0], f['rt'][1])) if len(f['rt']) >= 2 else '?'
            units.append('%s@%.5f rt=%s ms2=%d' % (f['id'], f['mz'], rt, f['n']))
        edges.sort(key=lambda e: (-len(e['shared']), e['delta']))
        links = []
        for e in edges[:4]:
            a, b = features[e['i']], features[e['j']]
            peaks = ','.join('%.4f(%.1f/%.1f)' % (mz, v1, v2)
                             for mz, v1, v2 in e['shared'][:2])
            links.append('%s-%s d=%.4f ions=%s' %
                         (a['id'], b['id'], e['delta'], peaks))
        text = ('G%d units=%d links=%d delta_types=%d\n  U %s\n  L %s' %
                (rank, len(nodes), len(edges), score[1], '; '.join(units),
                 '; '.join(links)))
        if len(text) > 577:
            text = text[:574] + '...'
        output.append(text)
    print('\n'.join(output)[:6000])


if __name__ == '__main__':
    if len(sys.argv) != 2:
        raise SystemExit('usage: python3 footprint.py /data/features.json')
    main(sys.argv[1])