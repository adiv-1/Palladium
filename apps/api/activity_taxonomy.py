"""Versioned report families for forecasting, independent of browsing categories."""
import re

VERSION = 'report-families-v1'
FAMILIES = {
    'person': 'Person-directed reports',
    'property': 'Property / vehicle reports',
}


def families(offenses):
    """Return a set: multi-offense reports count once within a selected family.

    Context (indoors, relationship, passing pedestrian) is generally unavailable.
    Financial, drug, warrant and administrative labels alone create no hotspot.
    """
    result = set()
    for original in offenses:
        text = original.upper()
        match = re.search(r'(?:^|\s-\s)(\d{2}[A-Z]|\d{3})\s*$', text)
        prefix = re.match(r'^(\d{2}[A-Z]|\d{3})\s*-', text)
        code = (match or prefix).group(1) if match or prefix else ''
        if code.startswith('26') or any(s in text for s in ('IDENTITY', 'FRAUD', 'FORGER', 'EMBEZZL', 'CREDIT CARD')):
            continue
        if code.startswith(('90', '35')) or any(s in text for s in ('WARRANT', 'PAROLE', 'PROBATION', 'IMPOUND', 'TRESPASS', 'CONTROLLED SUB', 'PARAPHERNALIA')):
            continue
        if code in {'09A', '09B', '09C', '11A', '11B', '11C', '11D', '13A', '13B', '13C', '120'} or any(
            s in text for s in ('ROBBERY', 'ASSAULT', 'BATTERY', 'HOMICIDE', 'MURDER', 'RAPE', 'SEXUAL', 'CRIMINAL THREAT', 'KIDNAP')):
            result.add('person')
        elif code in {'200', '220', '23A', '23B', '23C', '23D', '23E', '23F', '23G', '23H', '240', '290'} or any(
            s in text for s in ('THEFT', 'BURGLARY', 'STOLEN', 'VANDAL', 'ARSON', 'SHOPLIFT')):
            result.add('property')
    return result
