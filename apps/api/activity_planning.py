"""Budgeted maximum-coverage planning over a fixed walking-network snapshot."""
import heapq
import math

from .activity_geo import cell_id, xy


def greedy_posts(candidates, weights, budget=8):
    """Each additional post maximizes demand not covered by earlier posts."""
    covered, selected, available = set(), [], list(candidates)
    for _ in range(budget):
        if not available:
            break
        def gain(candidate):
            return sum(weights[i] for i in candidate['covers'] if i not in covered)
        best = max(available, key=lambda c: (gain(c), -c['cell']))
        extra = gain(best)
        if extra <= 0:
            break
        covered.update(best['covers'])
        selected.append({**best, 'incremental_expected_reports': round(extra, 5),
            'cumulative_expected_reports': round(sum(weights[i] for i in covered), 5),
            'covered_cells': sorted(covered)})
        available.remove(best)
    return selected


def planning_bundle(graph, cells, weekly, radius=400):
    lookup = {c['id']: i for i, c in enumerate(cells)}
    nodes = graph['nodes']
    node_cells = [lookup.get(cell_id(*p)) for p in nodes]
    adjacency = [[] for _ in nodes]
    names = [[] for _ in nodes]
    for u, v, length, _, name in graph['edges']:
        adjacency[u].append((v, length))
        adjacency[v].append((u, length))
        names[u].append(graph['names'][name])
        names[v].append(graph['names'][name])
    cell_nodes = [[] for _ in cells]
    for n in graph['largest_component']:
        if node_cells[n] is not None:
            cell_nodes[node_cells[n]].append(n)
    representatives = {}
    for i, options in enumerate(cell_nodes):
        if not options:
            continue
        center = xy(cells[i]['center'])
        def distance(n):
            p = xy(nodes[n])
            return math.hypot(p[0]-center[0], p[1]-center[1])
        representatives[i] = min(options, key=distance)
    candidates = []
    for cell, root in representatives.items():
        distances, heap = {root: 0}, [(0, root)]
        while heap:
            meters, u = heapq.heappop(heap)
            if meters != distances[u]:
                continue
            for v, length in adjacency[u]:
                next_distance = meters+length
                if next_distance <= radius and next_distance < distances.get(v, math.inf):
                    distances[v] = next_distance
                    heapq.heappush(heap, (next_distance, v))
        covers = [i for i, n in representatives.items() if n in distances]
        name = next((n for n in names[root] if not n.startswith(('Unnamed', 'Campus path'))), None)
        if not name:
            nearby = [(meters, n) for n, meters in distances.items()
                if any(not s.startswith(('Unnamed', 'Campus path')) for s in names[n])]
            if nearby:
                meters, n = min(nearby)
                street = next(s for s in names[n] if not s.startswith(('Unnamed', 'Campus path')))
                name = f'{street} · connecting path {round(meters)} m away'
            else:
                name = f'Mapped path in area {cells[cell]["id"]}'
        candidates.append({'cell': cell, 'coordinates': nodes[root], 'name': name, 'covers': covers})
    scenarios = {family: [greedy_posts(candidates, [r[0] for r in slot]) for slot in slots]
        for family, slots in weekly.items()}
    return {'radius_m': radius, 'maximum_staff': 8, 'candidates': len(candidates), 'scenarios': scenarios,
        'method': 'Greedy maximum coverage of forecast report counts at representative area connections within a 400 m network walk.',
        'notice': 'Hypothetical ambassador posts, not actual DPS assignments. Area coverage is approximate. Staffing feasibility, existing posts, events, and closures require human review. Pedestrian flow is not included.'}
