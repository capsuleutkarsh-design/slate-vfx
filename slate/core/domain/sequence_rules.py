"""
When numbered files are one clip: the stock library's rule, shared by the
ingest and the preview player so the two never disagree.

sequence_utils.group_frames() already insists on two or more frames with the
same name and padding. Stock libraries are full of numbered variants
(fire_burst_01, _03, _05...; sparks_1, sparks_2), so this also asks that the
numbers run on - at least 90% of the range present - and that there are three
frames, or the numbers are padded like frame numbers (0001, 1001), before
treating them as one clip (MED-010).
"""

MIN_COVERAGE = 0.9


def is_real_sequence(seq) -> bool:
    count = len(seq.frames)
    span = seq.end - seq.start + 1
    if count < 2 or span <= 0:
        return False
    if count / span < MIN_COVERAGE:
        return False
    looks_like_frames = seq.padding >= 3 or seq.start >= 100
    return count >= 3 or looks_like_frames
