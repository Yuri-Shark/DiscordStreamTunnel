"""Bounded RTP header counters; never export SSRCs, sequences or payload bytes.

Sequence gaps are observations, not a measure of unrecoverable video loss:
late packets within the window reduce them, but RTX uses a different stream.
"""
import struct

WINDOW = 2048
MASK = (1 << WINDOW) - 1
MAX_FLOWS = 32
MAX_TYPES = 8


class Sequences:
    def __init__(self):
        self.flows = {}
        self.types = set()

    def observe(self, data):
        if len(data) < 12 or data[0] >> 6 != 2 or 192 <= data[1] <= 223:
            return
        pt = data[1] & 127
        # Discord dynamically assigns audio/video payload types in this range.
        if pt < 96:
            return
        sequence = struct.unpack_from('!H', data, 2)[0]
        key = data[8:12], pt
        flow = self.flows.get(key)
        if flow is None:
            if len(self.flows) >= MAX_FLOWS or pt not in self.types and len(self.types) >= MAX_TYPES:
                return
            self.types.add(pt)
            self.flows[key] = dict(high=sequence, bits=1, span=1, packets=1,
                gaps=0, late=0, duplicates=0, resets=0)
            return
        flow['packets'] += 1
        delta = (sequence - flow['high'] + 32768) % 65536 - 32768
        if delta > 10000:
            # A discontinuous sender restart is not ten thousand lost packets.
            flow.update(high=sequence, bits=1, span=1, resets=flow['resets']+1)
        elif delta > 0:
            flow['gaps'] += delta-1
            flow['bits'] = ((flow['bits'] << delta) | 1) & MASK
            flow['span'] = min(WINDOW, flow['span']+delta)
            flow['high'] = sequence
        elif -delta < flow['span']:
            bit = 1 << -delta
            if flow['bits'] & bit:
                flow['duplicates'] += 1
            else:
                flow['bits'] |= bit
                flow['late'] += 1

    def snapshot(self):
        result = []
        for pt in sorted(self.types):
            flows = [flow for (_, kind), flow in self.flows.items() if kind == pt]
            result.append({'pt': pt, 'packets': sum(f['packets'] for f in flows),
                'sequenceMissing': sum(f['gaps']-f['late'] for f in flows),
                'lateRecovered': sum(f['late'] for f in flows),
                'duplicates': sum(f['duplicates'] for f in flows),
                'resets': sum(f['resets'] for f in flows)})
        return result
