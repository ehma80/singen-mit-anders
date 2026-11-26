"""Vector-PDF music reader for scores exported from notation software (Opus/Finale-style fonts).
Reads glyph positions + vector drawings directly from the PDF (no image recognition), so for
such PDFs pitches/rhythms/lyrics are exact. Output: dict score (see to_score)."""
import sys, json, re
from fractions import Fraction as F
import pymupdf as fitz

# Sibelius exports use Opus (classic) or Norfolk (jazz) music fonts with the same glyph codes; text in Times or Academico
MUSIC_FONTS, TEXT_FONTS = ('Opus', 'Norfolk'), ('Times', 'Academico')
def is_music(f): return f.startswith(MUSIC_FONTS) and not f.startswith(('OpusText', 'OpusMetro', 'NorfolkText', 'NorfolkMetro'))
def is_text(f): return f.startswith(TEXT_FONTS) and 'Italic' not in f and 'Bold' not in f

NOTEHEADS = {'œ': 'black', '˙': 'half', 'w': 'whole'}
RESTS = {'∑': 'measure', 'Ó': F(2), 'Œ': F(1), '‰': F(1, 2), '≈': F(1, 4), 'Â': F(1, 8)}
ACC = {'b': -1, 'n': 0, '#': 1}
STEP_NAMES = ['C', 'D', 'E', 'F', 'G', 'A', 'B']
STEP_PC = [0, 2, 4, 5, 7, 9, 11]
FLAT_ORDER = [6, 2, 5, 1, 4, 0, 3]   # B E A D G C F (diatonic idx)
SHARP_ORDER = [3, 0, 4, 1, 5, 2, 6]


def diatonic(octave, step):
    return octave * 7 + step


class Staff:
    def __init__(self, ys, x0, x1):
        self.ys = sorted(ys)          # top..bottom
        self.top, self.bot = self.ys[0], self.ys[-1]
        self.sp = (self.bot - self.top) / 4
        self.x0, self.x1 = x0, x1
        self.clef = None
        self.key = 0                  # negative = flats

    def pos(self, y):                 # half-steps above bottom line
        return round((self.bot - y) / (self.sp / 2))

    def dia(self, y):
        p = self.pos(y)
        base = {'treble': diatonic(4, 2), 'treble8': diatonic(3, 2)}.get(self.clef, diatonic(2, 4))  # E4 / E3 / G2
        return base + p


def gather(page):
    chars = []
    for b in page.get_text('rawdict')['blocks']:
        for l in b.get('lines', []):
            for s in l['spans']:
                for ch in s['chars']:
                    if ch['c'] == ' ' and not is_text(s['font']):
                        continue
                    chars.append(dict(c=ch['c'], font=s['font'], size=s['size'], x=ch['origin'][0], y=ch['origin'][1],
                                      x1=ch['bbox'][2]))
    drw = page.get_drawings()
    return chars, drw


def parse_page(page):
    chars, drw = gather(page)
    hlines, vlines, beams, curves, brackets = [], [], [], [], []
    for d in drw:
        kinds = [i[0] for i in d['items']]
        r = d['rect']
        w = d.get('width') or 0
        if kinds == ['l']:
            p1, p2 = d['items'][0][1], d['items'][0][2]
            if abs(p1.y - p2.y) < 0.01:
                hlines.append((min(p1.x, p2.x), max(p1.x, p2.x), p1.y, w))
            elif abs(p1.x - p2.x) < 0.01:
                vlines.append((p1.x, min(p1.y, p2.y), max(p1.y, p2.y), w))
            if (abs(w - 0.43) < 0.03 or abs(w - 0.53) < 0.03) and abs(p1.x - p2.x) < 150 and abs(p1.y - p2.y) < 5:
                brackets.append((min(p1.x, p2.x), max(p1.x, p2.x), min(p1.y, p2.y), max(p1.y, p2.y)))
        elif kinds == ['re'] and d.get('fill') is not None and r.height < 2.5:
            beams.append(('rect', r.x0, r.x1, (r.y0 + r.y1) / 2, (r.y0 + r.y1) / 2))
        elif kinds == ['l', 'l', 'l', 'l'] and d.get('fill') is not None:
            pts = [d['items'][0][1]] + [i[2] for i in d['items']]
            xs = sorted(set(round(p.x, 2) for p in pts))
            xl, xr = min(p.x for p in pts), max(p.x for p in pts)
            yl = sum(p.y for p in pts if abs(p.x - xl) < 0.3) / max(1, sum(1 for p in pts if abs(p.x - xl) < 0.3))
            yr = sum(p.y for p in pts if abs(p.x - xr) < 0.3) / max(1, sum(1 for p in pts if abs(p.x - xr) < 0.3))
            beams.append(('poly', xl, xr, yl, yr))
        elif 'c' in kinds and d.get('fill') is not None:
            curves.append((r.x0, r.x1, r.y0, r.y1))
    # staves
    long = sorted([h for h in hlines if h[1] - h[0] > 150 and h[3] < 0.5], key=lambda h: h[2])
    staves, cur = [], []
    for h in long:
        if cur and (abs(h[2] - cur[-1][2] - 3.4) > 0.6 or len(cur) == 5):
            if len(cur) == 5:
                staves.append(cur)
            cur = []
        cur.append(h)
    if len(cur) == 5:
        staves.append(cur)
    staves = [Staff([h[2] for h in s], s[0][0], s[0][1]) for s in staves]
    # music glyphs
    music = [c for c in chars if is_music(c['font'])]
    text = [c for c in chars if is_text(c['font']) and 7 < c['size'] < 8.5]
    tuplets = [c for c in chars if c['font'].startswith(('OpusText', 'NorfolkText')) and c['c'] == '3']

    def staff_of(y, margin=14):
        best = None
        for i, s in enumerate(staves):
            if s.top - margin <= y <= s.bot + margin:
                dd = 0 if s.top <= y <= s.bot else min(abs(y - s.top), abs(y - s.bot))
                if best is None or dd < best[0]:
                    best = (dd, i)
        return None if best is None else best[1]

    per = [dict(glyphs=[], lyr=[]) for _ in staves]
    for c in music:
        si = staff_of(c['y'])
        if si is not None:
            per[si]['glyphs'].append(c)
    for c in text:
        # lyric line: below a staff
        cands = [(c['y'] - s.bot, i) for i, s in enumerate(staves) if 4 < c['y'] - s.bot < 32]
        if cands:
            per[min(cands)[1]]['lyr'].append(c)
    # systems: staves sharing a barline
    bars = [v for v in vlines if abs(v[3] - 0.53) < 0.05 or abs(v[3] - 1.7) < 0.1 or v[3] > 0.45]
    out = []
    for t in tuplets:
        def dd(st):
            if st.top <= t['y'] <= st.bot: return 0
            return st.top - t['y'] if t['y'] < st.top else 1.5 * (t['y'] - st.bot)
        t['owner'] = min(staves, key=dd) if staves else None
    for i, s in enumerate(staves):
        g = sorted(per[i]['glyphs'], key=lambda c: c['x'])
        t8 = [c for c in g if c['c'] == '#' and 'SpecialII' in c['font'] and c['x'] < s.x0 + 15]  # tenor clef (treble 8vb)
        g = [c for c in g if not (c in t8)]
        clefg = [c for c in g if c['c'] in '&?' and c['x'] < s.x0 + 15] or t8
        s.clef = 'treble8' if t8 else ('treble' if clefg and clefg[0]['c'] == '&' else 'bass')
        # key signature: accidentals right after clef before first non-acc glyph
        key = 0
        start = clefg[0]['x'] + 8 if clefg else s.x0
        lastx = start
        for c in g:
            if c['x'] < start:
                continue
            if c['c'] in 'b#' and c['x'] - lastx < 6:
                key += -1 if c['c'] == 'b' else 1
                lastx = c['x']
            elif c['x'] >= start:
                break
        s.key = key
        # barlines for this staff
        bx = sorted(set(round(v[0], 1) for v in bars if v[1] <= s.top + 0.5 and v[2] >= s.bot - 0.5 and v[0] > s.x0 + 5))
        # merge double barlines
        merged = []
        for x in bx:
            if merged and x - merged[-1] < 4:
                merged[-1] = x
            else:
                merged.append(x)
        if not merged or s.x1 - merged[-1] > 4:
            merged.append(s.x1)
        stems = [v for v in vlines if (abs(v[3] - 0.32) < 0.05 or abs(v[3] - 0.43) < 0.03) and v[2] - v[1] > 6 and s.top - 30 < v[1] and v[2] < s.bot + 30]
        out.append(dict(staff=s, glyphs=g, lyr=sorted(per[i]['lyr'], key=lambda c: c['x']), bars=merged,
                        stems=stems))
    return out, beams, curves, tuplets, brackets, hlines


def beam_y_at(b, x):
    kind, xl, xr, yl, yr = b
    if xr - xl < 1e-6:
        return yl
    return yl + (yr - yl) * (x - xl) / (xr - xl)


def analyze_staff(sd, beams, curves, tuplets, brackets, hlines):
    s = sd['staff']
    g = sd['glyphs']
    first_note_x = None
    clef_x = min([c['x'] for c in g if c['c'] in '&?'] or [s.x0])
    # skip key/time sig region: time signature digits '4' etc at size 13.7
    tsig = [c for c in g if c['c'].isdigit()]
    content_start = max([c['x1'] for c in g if c['c'] in '&?' and c['x'] < s.x0 + 15] + [c['x1'] for c in tsig] + [s.x0])
    if s.key:
        ks = [c for c in g if c['c'] in 'b#' and c['x'] < content_start + 15]
    # events
    events = []
    noteheads = [c for c in g if c['c'] in NOTEHEADS]
    used_acc = set()
    for c in g:
        if c['c'] in NOTEHEADS:
            x, y = c['x'], c['y']
            ev = dict(type='note', x=x, y=y, head=NOTEHEADS[c['c']], dia=s.dia(y), acc=None)
            # accidental to the left
            for a in g:
                if a['c'] in ACC and 0 < x - a['x'] < 9 and abs(a['y'] - (y if a['c'] != 'b' else y + 0)) < 1.3 and id(a) not in used_acc:
                    ev['acc'] = ACC[a['c']]
                    used_acc.add(id(a))
            # stem
            st = None
            for v in sd['stems']:
                if x - 0.6 <= v[0] <= x + 6.4 and v[1] - 1.5 <= y <= v[2] + 1.5:
                    st = v
                    break
            nb = 0
            flag = 0
            if st is not None:
                up = abs(v[2] - y) < abs(v[1] - y)  # note at bottom end -> stem up
                end = st[1] if up else st[2]
                ys = set()
                for b in beams:
                    if b[1] - 0.6 <= st[0] <= b[2] + 0.6:
                        by = beam_y_at(b, st[0])
                        if st[1] - 1.6 <= by <= st[2] + 1.6:
                            ys.add(round(by, 0))
                nb = len(ys)
                for f in g:
                    if f['c'] in 'Jj' and abs(f['x'] - st[0]) < 4 and abs(f['y'] - end) < 14:
                        flag = max(flag, 1)
                    if f['c'] in 'Rr' and abs(f['x'] - st[0]) < 4 and abs(f['y'] - end) < 14:
                        flag = max(flag, 2)
            ev['stem'] = st
            ev['nb'] = max(nb, flag)
            events.append(ev)
        elif c['c'] in RESTS:
            events.append(dict(type='rest', x=c['x'], y=c['y'], kind=c['c']))
    # dots
    dots = [c for c in g if c['c'] == '™']
    for ev in events:
        ev['dots'] = 0
        right = ev['x'] + (5.9 if ev['type'] == 'note' else 4)
        for d in dots:
            if 0 < d['x'] - right < 9 and abs(d['y'] - ev['y']) < 2.6:
                ev['dots'] += 1
    # chords: same x -> keep highest
    events.sort(key=lambda e: (e['x'], -(e.get('dia') or 0)))
    ded = []
    for e in events:
        if ded and e['type'] == 'note' and ded[-1]['type'] == 'note' and abs(e['x'] - ded[-1]['x']) < 1.5:
            continue
        ded.append(e)
    events = ded
    # base durations
    for e in events:
        if e['type'] == 'note':
            d = {'whole': F(4), 'half': F(2)}.get(e['head'], F(1) / (2 ** e['nb']))
        else:
            d = RESTS[e['kind']] if e['kind'] != '∑' else 'measure'
        if d != 'measure' and e['dots']:
            d = d * (F(3, 2) if e['dots'] == 1 else F(7, 4))
        e['dur'] = d
    # tuplets
    for t in tuplets:
        tx = t['x'] + 1.5
        if not (s.top - 30 < t['y'] < s.bot + 30):
            continue
        # nearest staff check: tuplet belongs to this staff if closest
        span = None
        if t.get('owner') is not sd['staff']:
            continue
        parts = [b for b in brackets if abs(b[2] - t['y']) < 9 and abs((b[0] + b[1]) / 2 - tx) < 40]
        lp = [b for b in parts if b[1] <= tx + 1]
        rp = [b for b in parts if b[0] >= tx - 1]
        if lp and rp:
            span = (min(b[0] for b in parts) - 2, max(b[1] for b in parts) + 2)
        if span is None:
            cand = [b for b in beams if b[1] - 2 <= tx <= b[2] + 2]
            cand = [(abs(beam_y_at(b, tx) - t['y']), b) for b in cand]
            cand = [c for c in cand if c[0] < 14]
            if cand:
                b = min(cand, key=lambda c: c[0])[1]
                span = (b[1] - 7, b[2] + 1)
        if span is None:
            continue
        t['_staffcheck'] = True
        for e in events:
            ex = e['stem'][0] if e.get('stem') else e['x'] + 3
            if span[0] <= ex <= span[1] and e['dur'] != 'measure':
                if not e.get('tup'):
                    e['tup'] = True
                    e['dur'] = e['dur'] * F(2, 3)
    # ties: curve starting near note, next note same pitch, no lyric on next (checked later)
    for i, e in enumerate(events):
        e['tie_start'] = False
        if e['type'] != 'note':
            continue
        for c in curves:
            if e['x'] - 3 <= c[0] <= e['x'] + 7 and abs((c[2] + c[3]) / 2 - e['y']) < 6 and c[1] - c[0] > 4:
                e['tie_start'] = True
                e['curve_end'] = c[1]
    # lyrics tokens: words from letters (split at spaces/gaps); hyphens are placed afterwards, because some
    # exports draw a hyphen overlapping the next syllable ("Zeh ner-spring'n" with the '-' inside "ner")
    toks = []
    cur = None
    hyphens = [c for c in sd['lyr'] if c['c'] == '-']
    for c in sd['lyr']:
        if c['c'] == '-':
            continue
        if c['c'] == ' ' or (cur and c['x'] - cur['x1'] > 0.9):
            if cur:
                toks.append(cur)
            cur = None
            if c['c'] == ' ':
                continue
        if cur is None:
            cur = dict(text=c['c'], x=c['x'], x1=c['x1'], y=c['y'], hyph=False)
        else:
            cur['text'] += c['c']
            cur['x1'] = c['x1']
    if cur:
        toks.append(cur)
    for h in hyphens:  # the hyphen belongs to the last syllable that starts left of it
        left = [t for t in toks if t['x'] < h['x'] and abs(t['y'] - h['y']) < 4 and h['x'] - t['x1'] < 14]
        if left:
            max(left, key=lambda t: t['x'])['hyph'] = True
    # multiple lyric lines? keep the first line (smallest y)
    if toks:
        y0 = min(t['y'] for t in toks)
        toks = [t for t in toks if abs(t['y'] - y0) < 4]
    return events, toks


def to_score(pdf_path, nstaves_per_system=None, names=None):
    doc = fitz.open(pdf_path)
    voices = None
    measures_all = []
    meta = dict(title=None, tempo=None)
    sys_counter = 0
    for pno, page in enumerate(doc):
        sds, beams, curves, tuplets, brackets, hlines = parse_page(page)
        if not sds:
            continue
        if pno == 0:
            # title: largest Times text
            big = [b for b in page.get_text('dict')['blocks'] for l in b.get('lines', []) for b2 in [l] for s in l['spans'] if s['size'] > 14]
            ttl = [s['text'] for b in page.get_text('dict')['blocks'] for l in b.get('lines', []) for s in l['spans'] if s['size'] > 14 and s['font'].startswith(TEXT_FONTS)]
            meta['title'] = ' '.join(ttl).strip() or None
            m = re.search(r'([qhe]?)\s*=\s*(\d+)', page.get_text())
            if m:  # metronome mark: q = quarter, h = half, e = eighth (music text font)
                meta['tempo'] = round(int(m.group(2)) * {'h': 2, 'e': 0.5}.get(m.group(1), 1))
        # group staves into systems by shared barline sets / vertical proximity
        gaps = [b['staff'].top - a['staff'].bot for a, b in zip(sds, sds[1:])]
        thr = min(gaps) * 1.35 if gaps else 0
        groups, cur = [], []
        for sd in sds:
            if cur and sd['staff'].top - cur[-1]['staff'].bot > thr and (voices is None or len(cur) == len(voices)):
                groups.append(cur)
                cur = []
            cur.append(sd)
        if cur:
            groups.append(cur)
        for grp in groups:
            n = len(grp)
            if voices is None:
                voices = [[] for _ in range(n)]
            # take barlines from the top staff (the union)
            bars = sorted(set(x for sd in grp for x in sd['bars']))
            merged = []
            for x in bars:
                if merged and x - merged[-1] < 4:
                    merged[-1] = max(merged[-1], x)
                else:
                    merged.append(x)
            bars = merged
            if sys_counter == 0:
                for vi, sd in enumerate(grp):
                    LAST_KEYS[vi] = sd['staff'].key
                # staff names: Times text left of the first system's staves
                LAST_NAMES.clear()
                for sd in grp:
                    st = sd['staff']
                    nm = ''.join(c['c'] for c in sorted([c for c in gather(page)[0] if is_text(c['font']) and c['x'] < st.x0 and st.top - 2 < c['y'] < st.bot + 2], key=lambda c: c['x'])).strip()
                    LAST_NAMES.append(nm or None)
            for vi, sd in enumerate(grp):
                events, toks = analyze_staff(sd, beams, curves, tuplets, brackets, hlines)
                # tuplet ownership: a '3' between two staves may wrongly hit both; handled by beam/bracket proximity
                left = -1
                ms = []
                for bi, bx in enumerate(bars):
                    evs = [e for e in events if left < e['x'] < bx]
                    ms.append(dict(events=evs))
                    left = bx
                # assign lyrics to notes
                notes = [e for e in events if e['type'] == 'note']
                last = -1
                for t in toks:
                    tc = (t['x'] + t['x1']) / 2
                    best = None
                    for ni, e in enumerate(notes):
                        if ni <= last:
                            continue
                        d = abs(t['x'] - e['x']) if t['x1'] - t['x'] > 22 else min(abs(t['x'] - e['x']), abs(tc - (e['x'] + 2.9)))
                        if best is None or d < best[0]:
                            best = (d, e, ni)
                    if best and best[0] < 12:
                        last = best[2]
                    if best and best[0] < 12:
                        e = best[1]
                        if 'lyric' in e:
                            e['lyric']['text'] += ' ' + t['text']
                            e['lyric']['hyph'] = t['hyph']
                        else:
                            e['lyric'] = dict(text=t['text'], hyph=t['hyph'])
                voices[vi].append(ms)
            sys_counter += 1
    # flatten measures per voice
    out = dict(meta=meta, voices=[])
    for vi, systems in enumerate(voices):
        mlist = [m for sysm in systems for m in sysm]
        out['voices'].append(mlist)
    return out


def build(pdf_path, names=('Sopran', 'Alt', 'Bariton')):
    raw = to_score(pdf_path)
    meta = raw['meta']
    nm = len(raw['voices'][0])
    report = []
    voices = []
    for vi, mlist in enumerate(raw['voices']):
        notes = []
        t = F(0)
        for mi, m in enumerate(mlist):
            tot = F(0)
            acc_state = {}
            for e in m['events']:
                if e['dur'] == 'measure':
                    e['dur'] = F(4)
                if e['type'] == 'note':
                    st = None
                    # key + accidentals
                    dia = e['dia']
                    step = dia % 7
                    alter = 0
                    keyn = getstaff_key(e)
                    if e['acc'] is not None:
                        acc_state[dia] = e['acc']
                    if dia in acc_state:
                        alter = acc_state[dia]
                    else:
                        alter = key_alter(KEY[vi], step)
                    midi = 12 * (dia // 7 + 1) + STEP_PC[step] + alter
                    notes.append(dict(t=t + tot, d=e['dur'], midi=midi, m=mi + 1,
                                      lyric=e.get('lyric'), tie=e.get('tie_start', False)))
                tot += e['dur']
            if tot != 4:
                report.append(f'{names[vi]} Takt {mi+1}: Summe {tot} statt 4')
            t += 4
        voices.append(notes)
    return meta, voices, report, nm


KEY = {}
LAST_KEYS = {}
LAST_NAMES = []


def getstaff_key(e):
    return None


def key_alter(key, step):
    if key < 0:
        return -1 if step in FLAT_ORDER[:-key] else 0
    if key > 0:
        return 1 if step in SHARP_ORDER[:key] else 0
    return 0


if __name__ == '__main__':
    KEY.update({0: -3, 1: -3, 2: -3})
    meta, voices, report, nm = build(sys.argv[1])
    print(meta, nm, 'measures')
    print('\n'.join(report))
