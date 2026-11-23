"""fs42_live: stretches of a file that don't play (ads recorded with an old broadcast).

A show's "content time" runs with those stretches taken out; the schedule is planned in
content time and each piece is mapped back to the file's own time when it plays."""


def clean(cuts, end):
    """The cuts sorted, merged where they touch, and kept inside [0, end]."""
    out = []
    for a, b in sorted(cuts):
        a, b = max(0.0, a), min(end, b)
        if b <= a:
            continue
        if out and a <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


def cut_total(cuts, end):
    return sum(b - a for a, b in clean(cuts, end))


def to_content(t, cuts):
    """File time t in content time; None if t falls inside a cut."""
    gone = 0.0
    for a, b in cuts:
        if t >= b:
            gone += b - a
        elif t > a:
            return None
    return t - gone


def to_file(c, cuts):
    """Content time c in file time (a cut's spot maps to just after it)."""
    t = c
    for a, b in cuts:
        if t >= a:
            t += b - a
        else:
            break
    return t


def cut_spots(cuts):
    """Where each cut was, in content time: the breaks a show had there."""
    spots, gone = [], 0.0
    for a, b in cuts:
        spots.append(a - gone)
        gone += b - a
    return spots


def file_pieces(skip, duration, cuts):
    """A stretch of content time as (skip, duration) pieces of the file, jumping over cuts."""
    start, stop = skip, skip + duration
    pieces, t, gone = [], 0.0, 0.0
    for a, b in cuts + [(float("inf"), float("inf"))]:
        # content time [t, a - gone) is file time [t + gone, a)
        lo, hi = max(start, t), min(stop, a - gone)
        if hi - lo > 0.01:
            pieces.append((lo + gone, hi - lo))
        t = a - gone
        gone += b - a
        if t >= stop:
            break
    return pieces
