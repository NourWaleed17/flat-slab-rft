# -*- coding: utf-8 -*-
"""Stage 2: Split long segments at standard bar length with splice overlap."""
from __future__ import print_function

TOLERANCE = 0.001   # feet


def process_splices(segments, params):
    """Split any segment longer than standard_bar_length into lapped sub-segments.

    Splice position is placed as close to midspan as practical.
    Adjacent bar rows (index even/odd) are staggered by half a spacing to avoid
    all splices landing at the same cross-section.

    Spliced ends carry no hook — they are straight lapping ends.
    Original start_hook / end_hook flags from Stage 1 are preserved at the real
    slab/shaft boundaries.
    """
    bar_length    = params['bar_length']
    splice_length = params['splice_length']
    spacing       = params['spacing']
    stagger_splices = params.get('stagger_splices', False)
    ld = params.get('ld', splice_length)

    result = []
    for seg in segments:
        result.extend(
            _split_segment(seg, bar_length, splice_length, spacing, stagger_splices, ld)
        )
    return result


def _split_segment(seg, bar_length, splice_length, spacing, stagger_splices, ld):
    """Recursively split one segment until every piece fits within bar_length."""
    seg_len = seg['end'] - seg['start']

    if seg_len <= bar_length + TOLERANCE:
        return [seg]

    # Correct stagger concept:
    # adjacent staggered rows should have splice-center spacing of Ld.
    # This is achieved with +/- Ld/2 offsets around the row midspan.
    if stagger_splices:
        row_phase = -0.5 if (seg.get('index', 0) % 2) == 0 else 0.5
        stagger = row_phase * ld
    else:
        stagger = 0.0

    sub_segs = []
    current_start      = seg['start']
    current_start_hook = seg['start_hook']
    first_cut = True

    while True:
        remaining = seg['end'] - current_start

        if remaining <= bar_length + TOLERANCE:
            # Final piece — preserves the original end hook
            sub = dict(seg)
            sub['start']      = current_start
            sub['end']        = seg['end']
            sub['start_hook'] = current_start_hook
            sub['end_hook']   = seg['end_hook']
            sub.pop('splice_end', None)
            sub_segs.append(sub)
            break

        # Choose splice position
        if first_cut:
            # Aim for midspan of the whole remaining length, shifted by stagger
            midspan_pos = current_start + remaining / 2.0
            splice_pos  = midspan_pos + stagger
            # Never exceed bar_length from current start, never fall below 50 %
            splice_pos  = min(splice_pos, current_start + bar_length)
            splice_pos  = max(splice_pos, current_start + bar_length * 0.5)
            first_cut   = False
        else:
            # Subsequent cuts at exact bar_length intervals
            splice_pos = current_start + bar_length

        # Current sub-segment ends at splice_pos (straight splice end)
        sub = dict(seg)
        sub['start']      = current_start
        sub['end']        = splice_pos
        sub['start_hook'] = current_start_hook
        sub['end_hook']   = False   # splice end is always straight
        sub['splice_end'] = True
        sub_segs.append(sub)

        # Next sub-segment overlaps by splice_length
        new_start = splice_pos - splice_length

        # Safety guard: if splice_length >= the advance we made, we would loop forever.
        # In that degenerate case (e.g. extremely long splice vs short bar), emit the
        # remaining length as a single final bar rather than looping infinitely.
        if new_start <= current_start + TOLERANCE:
            sub = dict(seg)
            sub['start']      = max(new_start, seg['start'])
            sub['end']        = seg['end']
            sub['start_hook'] = False
            sub['end_hook']   = seg['end_hook']
            sub.pop('splice_end', None)
            sub_segs.append(sub)
            break

        current_start      = new_start
        current_start_hook = False   # splice start is always straight

    return sub_segs
