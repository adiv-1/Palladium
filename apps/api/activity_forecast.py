"""CPU-only, pooled seasonal report-count forecasts for the full UPC boundary.

Empirical-Bayes weighted Poisson rates give a moment-matched Gamma-Poisson
predictive distribution. Intervals are conditional approximations, evaluated
retrospectively; they are not personal risk probabilities.
"""
from datetime import date, timedelta
import json
import math

import numpy as np

from .activity_geo import cell_id, xy
from .activity_taxonomy import FAMILIES, VERSION, families

MODEL_VERSION = 'pooled-seasonal-gamma-poisson-v1'
MATURE_DAYS = 30


def records(db):
    result = []
    for row in db.execute('SELECT id,occurred,reported,time,lon,lat,offenses,system FROM incidents'):
        item = dict(row)
        item['families'] = sorted(families(json.loads(item.pop('offenses'))))
        if not item['families'] or not item['time']:
            continue
        item['day'] = date.fromisoformat(item['occurred'])
        item['weekday'] = item['day'].weekday()
        item['slot'] = int(item['time'][:2]) // 3
        result.append(item)
    return result


def fit(items, cells, cutoff, *, half_life=730, pooling=40, modern=False, seasonal=True, size=250):
    """Train with only reported information available by cutoff and mature events.

    Observation-day denominators include zero-report days. A ten-year window
    is truncated at a 30-day maturity buffer. Modern-only is evaluated separately.
    """
    end = cutoff - timedelta(days=MATURE_DAYS)
    start = date(cutoff.year-10, cutoff.month, min(cutoff.day, 28))
    if modern:
        start = max(start, date(2025, 1, 1))
    if start > end:
        raise ValueError('Insufficient mature history')
    index = {c['id']: i for i, c in enumerate(cells)}
    counts = np.zeros((2, len(cells), 7, 8))
    support = np.zeros((2, len(cells)), dtype=int)
    recent = np.zeros_like(support)
    exposures = np.zeros(7)
    for offset in range((end-start).days+1):
        day = start + timedelta(days=offset)
        exposures[day.weekday()] += 2**(-(end-day).days/half_life)
    usable = 0
    for r in items:
        if not start <= r['day'] <= end or not r['reported'] or r['reported'] > cutoff.isoformat():
            continue
        i = index.get(cell_id(r['lon'], r['lat'], size))
        if i is None:
            continue
        usable += 1
        w = 2**(-(end-r['day']).days/half_life)
        for family in r['families']:
            k = list(FAMILIES).index(family)
            counts[k, i, r['weekday'], r['slot']] += w
            support[k, i] += 1
            recent[k, i] += r['day'] >= end-timedelta(days=364)
    # A mass-preserving Gaussian kernel borrows from neighboring areas. Edge
    # normalization prevents losing reports outside the clipped study polygon.
    centers = np.array([xy(c['center']) for c in cells])
    d2 = ((centers[:, None]-centers[None, :])**2).sum(axis=2)
    areas = np.array([c['area_m2'] for c in cells])
    kernel = np.exp(-d2/(2*200**2)) * areas[:, None]
    kernel /= kernel.sum(axis=0, keepdims=True)
    smooth = np.einsum('ij,kjdh->kidh', kernel, counts)
    means, shapes = [], []
    for k in range(2):
        global_counts = counts[k].sum(axis=0)
        daily_rate = global_counts.sum()/max(exposures.sum(), 1)
        # Three equivalent days regularize each weekday/time global rate.
        gshape = global_counts + 3*daily_rate/8 + 1e-6
        grate = exposures[:, None] + 3
        global_mean = gshape/grate
        if not seasonal:
            global_mean[:] = daily_rate/8
        spatial = smooth[k].sum(axis=(1, 2)) + 10*areas/areas.sum()
        spatial /= spatial.sum()
        alpha = smooth[k] + pooling*spatial[:, None, None]
        if not seasonal:
            alpha = np.broadcast_to(spatial[:, None, None], alpha.shape).copy()*max(counts[k].sum(), 1)
        alpha_total = alpha.sum(axis=0)
        p = alpha/alpha_total
        mu = p*global_mean
        var_p = p*(1-p)/(alpha_total+1)
        var_global = global_mean**2/gshape
        rate_variance = var_global*(var_p+p*p) + global_mean**2*var_p
        shape = np.clip(mu*mu/np.maximum(rate_variance, 1e-12), .01, 1e6)
        means.append(mu.tolist())
        shapes.append(shape.tolist())
    return {'version': MODEL_VERSION, 'taxonomy_version': VERSION, 'cutoff': cutoff.isoformat(),
        'training_start': start.isoformat(), 'training_end': end.isoformat(), 'maturity_days': MATURE_DAYS,
        'half_life_days': half_life, 'pooling': pooling, 'modern_only': modern, 'seasonal': seasonal,
        'grid_size_m': size, 'training_reports': usable, 'cells': cells,
        'means': means, 'shapes': shapes, 'support': support.tolist(), 'recent_support': recent.tolist()}


def distribution(mu, shape):
    """Central 80% count interval, using a stable NB recurrence at our low rates."""
    p0 = math.exp(-shape*math.log1p(mu/shape))
    p, cumulative, lower, upper = p0, p0, 0, 0
    found_lower = cumulative >= .1
    while cumulative < .9 and upper < 1000:
        upper += 1
        p *= (upper-1+shape)/upper * mu/(shape+mu)
        cumulative += p
        if not found_lower and cumulative >= .1:
            lower = upper
            found_lower = True
    return {'expected_reports': round(mu, 5), 'probability_at_least_one': round(1-p0, 5),
        'prediction_interval_80': [lower, upper]}


def nll(y, mu, shape):
    return -(math.lgamma(y+shape)-math.lgamma(shape)-math.lgamma(y+1)
        + shape*math.log(shape/(shape+mu)) + y*math.log(max(mu, 1e-12)/(shape+mu)))


def evaluate(model, items, start, days=7):
    cells = model['cells']
    lookup = {c['id']: i for i, c in enumerate(cells)}
    actual = np.zeros((days, 2, len(cells), 8), dtype=int)
    for r in items:
        offset = (r['day']-start).days
        i = lookup.get(cell_id(r['lon'], r['lat'], model['grid_size_m']))
        if 0 <= offset < days and i is not None:
            for family in r['families']:
                actual[offset, list(FAMILIES).index(family), i, r['slot']] += 1
    loss = error = covered = total = hits = events = 0
    areas = np.array([c['area_m2'] for c in cells])
    for day in range(days):
        d = (start+timedelta(days=day)).weekday()
        for k in range(2):
            mu = np.array(model['means'][k])[:, d, :]
            shape = np.array(model['shapes'][k])[:, d, :]
            for h in range(8):
                # Select at most 20% of study area, ordered by forecast density.
                order = np.argsort(-(mu[:, h]/areas))
                selected = []
                budget = areas.sum()*.2
                for i in order:
                    if areas[i] <= budget:
                        selected.append(i)
                        budget -= areas[i]
                hits += actual[day, k, selected, h].sum()
                events += actual[day, k, :, h].sum()
                for i in range(len(cells)):
                    y, m, s = int(actual[day, k, i, h]), float(mu[i, h]), float(shape[i, h])
                    loss += nll(y, m, s)
                    error += abs(y-m)
                    lo, hi = distribution(m, s)['prediction_interval_80']
                    covered += lo <= y <= hi
                    total += 1
    return {'mean_negative_log_score': loss/total, 'mean_absolute_count_error': error/total,
        'interval_80_coverage': covered/total, 'report_family_occurrences': int(events),
        'top_20_percent_area_capture': float(hits/events) if events else None, 'prediction_windows': total}


