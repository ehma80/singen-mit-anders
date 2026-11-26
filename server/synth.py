"""Singing synthesis with lyrics: espeak-ng (German phonemes) + MBROLA diphone voices with explicit
note pitches and durations. Robotic but intelligible; runs on any small server, no GPU."""
import subprocess, re, math, os, functools, tempfile

VOICES = {'female': ('mb-de7', '/usr/share/mbrola/de7/de7'), 'male': ('mb-de6', '/usr/share/mbrola/de6/de6')}

# respellings for English words / vocalises so the German phonemizer pronounces them as sung
RESPELL = {  # '/...' = exact phonemes (MBROLA German set)
    'welcome': '/v E l k @ m', 'to': '/t u:', 'paradise': '/p E R @ d aI s', 'beats': '/b i: t s', 'tight': '/t aI t',
    'dood': '/d u: t', 'hooh': '/h u:', 'oh': '/? o:', 'uh': '/? u:', 'aw': '/? O', 'ah': '/? a:', 'wou': '/v o: U',
    "komm'": 'komm', "schenk'": 'schenk', "schnenk'": 'schenk', "hinter'm": 'hinterm', 'mirnoch': 'mir noch',
}
DIPH = {'aI': ('a', 'I'), 'aU': ('a', 'U'), 'OY': ('O', 'Y')}
VOWEL_RE = re.compile(r'[aeiouyAEIOUY@269]')


def is_vowel(ph):
    return bool(VOWEL_RE.search(ph)) and ph not in ('?',)


@functools.lru_cache(maxsize=4096)
def phonemize(word, voice):
    w = re.sub(r"[.,;:!?\"()„“”]", '', word).strip()
    w = RESPELL.get(w.lower(), w)
    if not w:
        return ()
    if w.startswith('/'):
        return tuple((p, 80 if not is_vowel(p) else 150) for p in w[1:].split())
    out = subprocess.run(['espeak-ng', '-v', voice, '-q', '--pho', w], capture_output=True, text=True).stdout
    phs = []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0] != '_':
            phs.append((parts[0], int(parts[1])))
    # drop glottal stop inside words (legato singing) but keep a short one at word start
    phs = [p for i, p in enumerate(phs) if not (p[0] == '?' and i > 0)]
    # sung German: word-final r after a vowel is vocalised ("Meer" -> me:6), like in pop/choir diction
    if len(phs) >= 2 and phs[-1][0] == 'R' and is_vowel(phs[-2][0]):
        phs[-1] = ('6', 90)
    return tuple(phs)


def is_offglide(phs, i):
    p = phs[i][0]
    return i > 0 and is_vowel(phs[i - 1][0]) and (p == '6' or (p == 'U' and phs[i - 1][0] == 'o:'))


def split_syllables(phs, k):
    """Split phoneme list of a word into k syllables: (onset, vowel, coda) each."""
    nuc = [i for i, (p, _) in enumerate(phs) if is_vowel(p) and not is_offglide(phs, i)]
    if len(nuc) < k:  # e.g. "Blau-er": the vocalised r is a syllable of its own here
        nuc = [i for i, (p, _) in enumerate(phs) if is_vowel(p)]
    if k <= 0:
        return []
    if len(nuc) < k:
        return None
    if len(nuc) > k:  # merge extra nuclei (diphthong spellings etc.) into previous syllables
        keep = nuc[:k - 1] + [nuc[-1]] if k > 1 else [nuc[0]]
        nuc = sorted(keep)
    sylls = []
    bounds = [0]
    for a, b in zip(nuc, nuc[1:]):
        cluster = b - a - 1
        bounds.append(b - (1 if cluster >= 1 else 0))
    bounds.append(len(phs))
    for si in range(k):
        seg = list(phs[bounds[si]:bounds[si + 1]])
        vi = nuc[si] - bounds[si]
        sylls.append((seg[:vi], seg[vi], seg[vi + 1:]))
    return sylls


def time_map(tempo, swing):
    spb = 60000.0 / tempo

    def f(b):
        if swing:
            frac = (b * 4) % 2
            if abs(frac - 1) < 1e-6:  # off-16th position inside an eighth
                b = b + 1.0 / 12
        return b * spb
    return f


def build_syllables(notes):
    """Group notes into sung syllables. Returns list of dict(text, hyph, notes=[...])."""
    syl = []
    for i, n in enumerate(notes):
        prev = notes[i - 1] if i else None
        contiguous = prev is not None and abs(prev['t'] + prev['d'] - n['t']) < 1e-6
        if n.get('lyric'):
            syl.append(dict(text=n['lyric'], hyph=n.get('hyph', False), notes=[n]))
        elif syl and contiguous:
            syl[-1]['notes'].append(n)  # tie or melisma
        else:
            base = syl[-1]['text'] if syl else 'uu'
            syl.append(dict(text=base, hyph=False, notes=[n], reattack=True))
    return syl


def make_pho(notes, voice='female', tempo=75, swing=False, total_beats=None, take=None):
    take = take or {}
    vname = take.get('espeak') or VOICES[voice][0]
    tm = time_map(tempo, swing)
    syls = build_syllables(notes)
    # words
    words, cur = [], []
    for s in syls:
        cur.append(s)
        if not s['hyph']:
            words.append(cur)
            cur = []
    if cur:
        words.append(cur)
    for w in words:
        if len(w) == 1 and w[0].get('reattack'):
            phs = phonemize(w[0]['text'], vname)
            sp = split_syllables(phs, 1)
            if sp:
                o, v, c = sp[-1]
                w[0]['ph'] = ([], v, [])
                continue
        text = ''.join(s['text'] for s in w)
        phs = phonemize(text, vname)
        sp = split_syllables(phs, len(w))
        if sp is None:
            sp = []
            for s in w:
                p1 = split_syllables(phonemize(s['text'], vname), 1)
                sp.append(p1[0] if p1 else ([], ('a:', 100), []))
        for s, p in zip(w, sp):
            s['ph'] = p
    # pitch track: list of (start_ms, end_ms, hz, legato_from_prev)
    cent = 2 ** (take.get('cents', 0) / 1200)
    vib_rate, seed = take.get('vib', 5.3), take.get('seed', 0)
    segs = []
    for i, n in enumerate(notes):
        a, b = tm(n['t']), tm(n['t'] + n['d'])
        leg = i > 0 and abs(tm(notes[i - 1]['t'] + notes[i - 1]['d']) - a) < 1
        segs.append((a, b, 440.0 * 2 ** ((n['midi'] - 69) / 12) * cent, leg))
    LN2 = math.log(2)

    def drift(t):  # slow, small natural pitch wander (± ~8 cents)
        return (math.sin(t / 1000 * 0.9 + seed) * 0.05 + math.sin(t / 1000 * 2.3 + 2 * seed) * 0.03) / 12 * LN2

    def f0(t):
        for i, (a, b, hz, leg) in enumerate(segs):
            if a - 1e-6 <= t < b or (i == len(segs) - 1 and t <= b + 1):
                val = math.log(hz)
                if leg and t - a < 55:  # legato glide from previous note
                    pv = math.log(segs[i - 1][2])
                    x = (t - a) / 55
                    val = pv + (val - pv) * (0.5 - 0.5 * math.cos(math.pi * x))
                elif not leg and t - a < 90:  # soft scoop into a fresh attack
                    x = max(0.0, (t - a) / 90)
                    val -= (0.35 / 12 * LN2) * (1 - x) ** 2
                dur = b - a
                if dur > 450 and t - a > 250:  # vibrato on long notes
                    depth = min(1.0, (t - a - 250) / 350) * 0.32 / 12 * LN2
                    val += depth * math.sin(2 * math.pi * vib_rate * (t - a) / 1000 + seed)
                return math.exp(val + drift(t))
            if t < a:
                break
        # outside notes (consonant anticipation before a note): use the next/nearest note
        best = min(segs, key=lambda s: min(abs(t - s[0]), abs(t - s[1])))
        return best[2]

    lines = []
    tcur = 0.0

    def emit(ph, start, dur):
        nonlocal tcur
        dur = max(10, dur)
        pts = []
        steps = max(2, min(40, int(dur // 30)))
        for k in range(steps + 1):
            p = 100.0 * k / steps
            pts.append(f'{p:.0f} {f0(start + dur * p / 100):.1f}')
        lines.append(f'{ph} {dur:.0f} ' + ' '.join(pts))
        tcur = start + dur

    # plan: onset consonants are sung BEFORE the beat (vowel lands on the beat), like real singers
    plan = []
    for si, sy in enumerate(syls):
        S = tm(sy['notes'][0]['t'])
        E = tm(sy['notes'][-1]['t'] + sy['notes'][-1]['d'])
        on, v, co = sy['ph']
        T = E - S
        on_d = [min(d, 80) for _, d in on]
        co_d = [min(d, 75) for _, d in co]
        if sum(co_d) > 0.25 * T:
            k = 0.25 * T / sum(co_d)
            co_d = [d * k for d in co_d]
        plan.append(dict(S=S, E=E, on=on, v=v, co=co, on_d=on_d, co_d=co_d))
    for si, p in enumerate(plan):
        prev_end = plan[si - 1]['E'] if si else 0.0
        contiguous = si > 0 and abs(prev_end - p['S']) < 1
        room = (0.4 * (plan[si - 1]['E'] - plan[si - 1]['S'])) if contiguous else (p['S'] - prev_end)
        want = sum(p['on_d'])
        ant = max(0.0, min(want, 140.0, room))
        p['ant'] = ant
        if want > ant + 0.35 * (p['E'] - p['S']):  # remaining part must fit into the note
            k = (ant + 0.35 * (p['E'] - p['S'])) / want
            p['on_d'] = [d * k for d in p['on_d']]
    for si, p in enumerate(plan):
        start = p['S'] - p['ant']
        nxt = plan[si + 1] if si + 1 < len(plan) else None
        end = p['E'] - (nxt['ant'] if nxt and abs(nxt['S'] - p['E']) < 1 else 0)
        if nxt and not abs(nxt['S'] - p['E']) < 1 and nxt['S'] - p['E'] > 60:
            end = p['E'] - min(40, 0.1 * (p['E'] - p['S']))  # tiny release before rests
        if start > tcur + 5:
            lines.append(f'_ {start - tcur:.0f}')
            tcur = start
        start = tcur if abs(start - tcur) < 5 or start < tcur else start
        t = start
        for (ph, _), d in zip(p['on'], p['on_d']):
            emit(ph, t, d); t += d
        vd = max(30, end - t - sum(p['co_d']))
        vname0 = p['v'][0]
        tail = None
        if vname0 in DIPH and vd > 300:  # hold the first vowel of a diphthong, close it at the end
            vname0, tail = DIPH[vname0][0], DIPH[vname0][1]
            vd -= 130
        nchunk = max(1, math.ceil(vd / 1200))
        for _ in range(nchunk):
            emit(vname0, t, vd / nchunk); t += vd / nchunk
        if tail:
            emit(tail, t, 130); t += 130
        for (ph, _), d in zip(p['co'], p['co_d']):
            emit(ph, t, d); t += d
        tcur = t
    if total_beats:
        endms = tm(total_beats)
        if endms > tcur:
            lines.append(f'_ {endms - tcur:.0f}')
    lines.append('_ 200')
    # split long silences (MBROLA overflows on very long segments)
    fixed = []
    for ln in lines:
        if ln.startswith('_ '):
            d = float(ln.split()[1])
            while d > 4000:
                fixed.append('_ 4000'); d -= 4000
            fixed.append(f'_ {d:.0f}')
        else:
            fixed.append(ln)
    lines = fixed
    return '\n'.join(lines) + '\n', syls


def render(notes, out_wav, voice='female', tempo=75, swing=False, total_beats=None, take=None):
    pho, syls = make_pho(notes, voice, tempo, swing, total_beats, take)
    db = (take or {}).get('db') or VOICES[voice][1]
    with tempfile.NamedTemporaryFile('w', suffix='.pho', delete=False) as f:
        f.write(pho)
        path = f.name
    raw = out_wav + '.raw.wav'
    r = subprocess.run(['mbrola', '-e', db, path, raw], capture_output=True, text=True)
    os.unlink(path)
    if r.returncode != 0:
        raise RuntimeError(r.stderr[-500:])
    return raw, syls


# ensemble: three different singers per section (different MBROLA voices, slight detune, own vibrato)
TAKES = {
    'female': [dict(espeak='mb-de7', db='/usr/share/mbrola/de7/de7', cents=0, vib=5.3, seed=0.0, delay=0, gain=1.0),
               dict(espeak='mb-de5', db='/usr/share/mbrola/de5/de5', cents=7, vib=5.7, seed=1.7, delay=14, gain=0.75),
               dict(espeak='mb-de3', db='/usr/share/mbrola/de3/de3', cents=-6, vib=4.9, seed=3.1, delay=26, gain=0.7)],
    'male': [dict(espeak='mb-de6', db='/usr/share/mbrola/de6/de6', cents=0, vib=5.2, seed=0.4, delay=0, gain=1.0),
             dict(espeak='mb-de4', db='/usr/share/mbrola/de4/de4', cents=6, vib=5.6, seed=2.2, delay=15, gain=0.75),
             dict(espeak='mb-de2', db='/usr/share/mbrola/de2/de2', cents=-7, vib=4.8, seed=4.0, delay=27, gain=0.7)],
}


def render_hum(midi, voice, out_wav, dur_ms=1700):
    """A hummed tone ('mmm') at the given pitch, for giving the starting notes."""
    db = VOICES[voice][1]
    hz = 440.0 * 2 ** ((midi - 69) / 12)
    pts = ' '.join(f'{p} {hz * (1 + 0.004 * math.sin(p / 100 * 2 * math.pi * 5.2 * dur_ms / 1000)) if p > 25 else hz:.1f}' for p in range(0, 101, 4))
    pho = f'_ 60 0 {hz:.1f}\nm {dur_ms} {pts}\n_ 120 0 {hz:.1f}\n'
    with tempfile.NamedTemporaryFile('w', suffix='.pho', delete=False) as f:
        f.write(pho)
        path = f.name
    r = subprocess.run(['mbrola', '-e', db, path, out_wav], capture_output=True, text=True)
    os.unlink(path)
    if r.returncode != 0:
        raise RuntimeError(r.stderr[-300:])
    return out_wav
