"""Neural singing for German lyrics.

A neural German speech voice (Piper/VITS, open models) is made to *sing*:
 1. lyrics -> phonemes (espeak-ng via Piper),
 2. the VITS duration predictor is bypassed: we feed our own phoneme durations so every vowel
    starts on its beat and lasts exactly as long as its note(s) (consonants come just before the beat),
 3. WORLD vocoder replaces the speech melody with the note pitches (legato glides, light vibrato).
Natural consonants and vowel colours come from the neural voice; pitch and timing from the score.
"""
import math, os, re, functools
import numpy as np
import onnxruntime as ort
import pyworld as pw
from piper import PiperVoice

VOICE_DIR = os.environ.get('CHOR_VOICES', '/opt/voices')
VOICES = {
    'female': ['de_DE-kerstin-low', 'de_DE-ramona-low', 'de_DE-eva_k-x_low'],
    'male': ['de_DE-thorsten-high', 'de_DE-karlsson-low', 'de_DE-pavoque-low'],
}
RESPELL = {
    'welcome': 'wellkamm', 'to': 'tu', 'paradise': 'päradeis', 'beats': 'biets', 'tight': 'teit',
    'dood': 'duht', 'hooh': 'huh', 'oh': 'oh', 'uh': 'uh', 'aw': 'oh', 'ah': 'ah', 'wou': 'wouh',
    "komm'": 'komm', "schenk'": 'schenk', "schnenk'": 'schenk', "hinter'm": 'hinterm',
}
VOWEL_CHARS = set('aeiouyɪʊɛɔœøʏəɐɜæɑɒʌɘɵɤ')
MARKS = set('ːˑ̯̃')
STRESS = set('ˈˌ')
SCALES = np.array([0.5, 1.0, 0.6], dtype=np.float32)  # calmer noise for steadier sustained vowels


def is_vowel(p):
    return bool(p) and p[0] in VOWEL_CHARS


class Singer:
    def __init__(self, name):
        base = os.path.join(VOICE_DIR, f'vits-piper-{name}', name)
        self.v = PiperVoice.load(base + '.onnx', config_path=base + '.onnx.json')
        so = ort.SessionOptions(); so.intra_op_num_threads = 1
        self.dur = ort.InferenceSession(base + '.dur.onnx', so)
        self.sing = ort.InferenceSession(base + '.sing.onnx', so)
        self.sr = self.v.config.sample_rate
        self.hop = 256
        self.fms = self.hop / self.sr * 1000.0

    @functools.lru_cache(maxsize=4096)
    def word_phonemes(self, word):
        w = re.sub(r"[.,;:!?\"()„“”]", '', word).strip()
        w = RESPELL.get(w.lower(), w)
        if not w:
            return ()
        ph = [p for sent in self.v.phonemize(w) for p in sent if p not in (' ', ',', '.', '!', '?', ';', ':')]
        return tuple(ph)

    def natural(self, ids):
        out = self.dur.run(None, {'input': np.array([ids], dtype=np.int64), 'input_lengths': np.array([len(ids)], dtype=np.int64),
                                  'scales': SCALES})
        return out[1][0, 0]

    def generate(self, ids, durs):
        out = self.sing.run(None, {'input': np.array([ids], dtype=np.int64), 'input_lengths': np.array([len(ids)], dtype=np.int64),
                                   'scales': SCALES, '/Ceil_output_0': np.array([[durs]], dtype=np.float32)})
        return out[0].reshape(-1).astype(np.float64)


@functools.lru_cache(maxsize=8)
def get_singer(name):
    return Singer(name)


def split_word(ph, k):
    """Return list of k (onset_idx, nucleus_idx, coda_idx) index lists into ph."""
    groups, cur = [], None
    for i, p in enumerate(ph):
        if is_vowel(p) or (p in MARKS and cur is not None and cur[-1] == i - 1):
            if cur is not None and cur[-1] == i - 1:
                cur.append(i)
            else:
                cur = [i]; groups.append(cur)
        elif p in STRESS:
            continue
        else:
            cur = None
    if not groups:
        return None
    while len(groups) < k:  # split a multi-vowel group (e.g. "Blau-er": a ʊ ɜ)
        cand = [g for g in groups if sum(1 for i in g if is_vowel(ph[i])) >= 2]
        if not cand:
            return None
        g = max(cand, key=len); gi = groups.index(g)
        last_v = max(i for i in g if is_vowel(ph[i]))
        groups[gi:gi + 1] = [[i for i in g if i < last_v], [i for i in g if i >= last_v]]
    while len(groups) > k:
        a, b = groups[-2], groups[-1]
        groups[-2:] = [list(range(a[0], b[-1] + 1))]
    res = []
    starts = [0]
    for ga, gb in zip(groups, groups[1:]):
        cons = [i for i in range(ga[-1] + 1, gb[0]) if ph[i] not in STRESS]
        starts.append(cons[-1] if cons else gb[0])  # last consonant goes to the next syllable
    starts.append(len(ph))
    for si, g in enumerate(groups):
        idx = list(range(starts[si], starts[si + 1]))
        on = [i for i in idx if i < g[0]]
        nu = [i for i in idx if g[0] <= i <= g[-1]]
        co = [i for i in idx if i > g[-1]]
        res.append((on, nu, co))
    return res


def pitch_track(notes, tm, vib_rate=5.3, seed=0.0, cents=0.0):
    segs = []
    for i, n in enumerate(notes):
        a, b = tm(n['t']), tm(n['t'] + n['d'])
        leg = i > 0 and abs(tm(notes[i - 1]['t'] + notes[i - 1]['d']) - a) < 1
        segs.append((a, b, math.log(440.0 * 2 ** ((n['midi'] - 69 + cents / 100) / 12)), leg))
    starts = np.array([s[0] for s in segs])
    L2 = math.log(2)

    def f0(t):
        i = int(np.searchsorted(starts, t, side='right')) - 1
        if i < 0:
            return math.exp(segs[0][2])
        a, b, lv, leg = segs[i]
        if t >= b and i + 1 < len(segs):  # between notes (consonant before next note)
            return math.exp(segs[i + 1][2])
        val = lv
        if leg and t - a < 60:
            x = (t - a) / 60
            val = segs[i - 1][2] + (lv - segs[i - 1][2]) * (0.5 - 0.5 * math.cos(math.pi * x))
        elif not leg and t - a < 80:
            val -= (0.3 / 12 * L2) * (1 - max(0, (t - a) / 80)) ** 2
        if b - a > 450 and t - a > 250:
            val += min(1.0, (t - a - 250) / 350) * 0.3 / 12 * L2 * math.sin(2 * math.pi * vib_rate * (t - a) / 1000 + seed)
        val += (math.sin(t / 1000 * 0.9 + seed) * 0.04 + math.sin(t / 1000 * 2.3 + 2 * seed) * 0.025) / 12 * L2
        return math.exp(val)
    return f0


def render_voice(notes, total_ms, tm, singer_name, take=None):
    """Render one voice to a float array (sr of the singer)."""
    import synth  # reuse syllable grouping
    take = take or {}
    sg = get_singer(singer_name)
    sr, fms = sg.sr, sg.fms
    out = np.zeros(int((total_ms / 1000 + 1) * sr))
    syls = synth.build_syllables(notes)
    for s in syls:
        s['S'] = tm(s['notes'][0]['t'])
        s['E'] = tm(s['notes'][-1]['t'] + s['notes'][-1]['d'])
    # phrases: split at rests
    phrases, cur = [], []
    for s in syls:
        if cur and s['S'] - cur[-1]['E'] > 1:
            phrases.append(cur); cur = []
        cur.append(s)
    if cur:
        phrases.append(cur)
    f0fun = pitch_track(notes, tm, take.get('vib', 5.3), take.get('seed', 0.0), take.get('cents', 0.0))
    for ph in phrases:
        seg = render_phrase(sg, ph, f0fun)
        if seg is None:
            continue
        y, t0 = seg
        i0 = int(round(t0 / 1000 * sr))
        if i0 < 0:
            y = y[-i0:]; i0 = 0
        n = min(len(y), len(out) - i0)
        out[i0:i0 + n] += y[:n]
    return out, sr


def render_phrase(sg, syls, f0fun):
    fms = sg.fms
    # words
    words, cur = [], []
    for s in syls:
        cur.append(s)
        if not s.get('hyph'):
            words.append(cur); cur = []
    if cur:
        words.append(cur)
    phs = []          # flat phoneme list
    role = []         # (syllable index, 'on'|'nu'|'co'|'sp')
    sidx = 0
    for wi, w in enumerate(words):
        text = ''.join(s['text'] for s in w)
        p = list(sg.word_phonemes(text))
        sp = split_word(p, len(w)) if p else None
        if sp is None:  # fallback: phonemize syllables separately
            p, sp = [], []
            for s in w:
                q = list(sg.word_phonemes(s['text'])) or ['a']
                o = len(p)
                one = split_word(q, 1) or [([], list(range(len(q))), [])]
                p += q
                sp.append(tuple([o + i for i in part] for part in one[0]))
        base = len(phs)
        r = [None] * len(p)
        for k, (on, nu, co) in enumerate(sp):
            for i in on: r[i] = (sidx + k, 'on')
            for i in nu: r[i] = (sidx + k, 'nu')
            for i in co: r[i] = (sidx + k, 'co')
        for i in range(len(p)):
            if r[i] is None:  # stress marks etc. belong to the syllable that follows
                nxt = next((r[k] for k in range(i + 1, len(p)) if r[k] and r[k][1] != 'mk'), None)
                prv = next((r[k] for k in range(i - 1, -1, -1) if r[k]), None)
                r[i] = ((nxt or prv or (sidx, 'on'))[0], 'mk')
        phs += p; role += r
        sidx += len(w)
        if wi < len(words) - 1:
            phs.append(' '); role.append((sidx - 1, 'sp'))
    pid = sg.v.config.phoneme_id_map
    ids = [pid['^'][0], pid['_'][0]]
    keep = []
    for p, r in zip(phs, role):
        if p not in pid:
            continue
        ids += [pid[p][0], pid['_'][0]]
        keep.append((p, r))
    ids.append(pid['$'][0])
    nat = sg.natural(ids) * fms  # ms per id
    # per syllable planning (ms)
    n_syl = len(syls)
    on_ms = [0.0] * n_syl; co_ms = [0.0] * n_syl
    for j, (p, (si, rl)) in enumerate(keep):
        d = nat[2 + 2 * j] + nat[3 + 2 * j]
        if rl == 'on': on_ms[si] += min(d, 110)
        if rl in ('co', 'sp'): co_ms[si] += min(d, 100)
    ant = []
    for i, s in enumerate(syls):
        T = s['E'] - s['S']
        room = 0.4 * (syls[i - 1]['E'] - syls[i - 1]['S']) if i else 150.0
        a = min(on_ms[i], 140.0, room)
        ant.append(a)
        co_ms[i] = min(co_ms[i], 0.3 * T)
    # target boundaries for every id, in absolute ms
    t_start = syls[0]['S'] - ant[0] - 2 * fms
    bounds = []
    # leading ^ and _
    durs_ms = [fms, fms]
    for i, s in enumerate(syls):
        start = s['S'] - ant[i]
        end = s['E'] - (ant[i + 1] if i + 1 < n_syl else 0)
        items = [(j, p, rl) for j, (p, (si, rl)) in enumerate(keep) if si == i]
        on_items = [x for x in items if x[2] in ('on', 'mk') and x[0] < min([y[0] for y in items if y[2] == 'nu'] or [10 ** 9])]
        nu_items = [x for x in items if x[2] == 'nu']
        co_items = [x for x in items if x not in on_items and x not in nu_items]
        on_nat = [nat[2 + 2 * j] + nat[3 + 2 * j] for j, _, _ in on_items]
        co_nat = [nat[2 + 2 * j] + nat[3 + 2 * j] for j, _, _ in co_items]
        k_on = (ant[i] + 0.35 * (s['E'] - s['S'])) / sum(on_nat) if sum(on_nat) > ant[i] + 0.35 * (s['E'] - s['S']) else 1.0
        on_d = [d * min(1.0, k_on) for d in on_nat]
        k_co = co_ms[i] / sum(co_nat) if co_nat and sum(co_nat) > co_ms[i] else 1.0
        co_d = [d * k_co for d in co_nat]
        vow = max(2 * fms, end - start - sum(on_d) - sum(co_d))
        nu_nat = [nat[2 + 2 * j] + nat[3 + 2 * j] for j, _, _ in nu_items] or [1]
        nu_d = [vow * d / sum(nu_nat) for d in nu_nat]
        if len(nu_items) >= 2 and vow > 300:  # diphthong: hold the first vowel, glide at the end
            tail = min(140.0, 0.3 * vow)
            nu_d = [vow - tail * (len(nu_items) - 1)] + [tail] * (len(nu_items) - 1)
        s['vowel_span'] = (start + sum(on_d), start + sum(on_d) + vow)
        seq = list(zip(on_items, on_d)) + list(zip(nu_items, nu_d)) + list(zip(co_items, co_d))
        seq.sort(key=lambda x: x[0][0])
        for (j, p, rl), d in seq:
            pad = min(nat[3 + 2 * j], fms, d * 0.3)
            durs_ms += [d - pad, pad]
    durs_ms.append(2 * fms)
    # cumulative rounding to frames
    durs = []
    acc_ms, acc_fr = 0.0, 0
    for d in durs_ms:
        acc_ms += max(0.0, d)
        fr = int(round(acc_ms / fms))
        durs.append(max(0, fr - acc_fr)); acc_fr = fr
    if sum(durs) < 4:
        return None
    y = sg.generate(ids, durs)
    # WORLD: replace melody with the notes
    sr = sg.sr
    fp = 5.0
    f0, tt = pw.dio(y, sr, f0_floor=60, f0_ceil=700, frame_period=fp)
    f0 = pw.stonemask(y, f0, tt, sr)
    sp = pw.cheaptrick(y, f0, tt, sr)
    ap = pw.d4c(y, f0, tt, sr)
    newf0 = np.zeros_like(f0)
    spans = [s['vowel_span'] for s in syls]
    for k, t in enumerate(tt):
        ta = t_start + t * 1000
        in_vowel = any(a <= ta <= b for a, b in spans)
        if f0[k] > 0 or in_vowel:
            newf0[k] = f0fun(ta)
            if in_vowel:
                ap[k] = ap[k] ** 2 if f0[k] > 0 else np.minimum(ap[k], 0.3) ** 2  # clearer, less hissy sustained vowels
    yy = pw.synthesize(newf0, sp, ap, sr, fp)
    # soft edges (no clicks)
    r = int(0.01 * sr)
    if len(yy) > 2 * r:
        yy[:r] *= np.linspace(0, 1, r); yy[-r:] *= np.linspace(1, 0, r)
    return yy, t_start


TAKES = {
    'female': [dict(singer='de_DE-kerstin-low', cents=0, vib=5.3, seed=0.0, delay=0, gain=1.0),
               dict(singer='de_DE-ramona-low', cents=6, vib=5.7, seed=1.7, delay=14, gain=0.8),
               dict(singer='de_DE-eva_k-x_low', cents=-6, vib=4.9, seed=3.1, delay=25, gain=0.75)],
    'male': [dict(singer='de_DE-thorsten-high', cents=0, vib=5.2, seed=0.4, delay=0, gain=1.0),
             dict(singer='de_DE-karlsson-low', cents=6, vib=5.6, seed=2.2, delay=15, gain=0.8),
             dict(singer='de_DE-pavoque-low', cents=-7, vib=4.8, seed=4.0, delay=26, gain=0.75)],
}
OUT_SR = 22050


def _resample(y, sr):
    from scipy.signal import resample_poly
    if sr == OUT_SR:
        return y
    g = math.gcd(OUT_SR, sr)
    return resample_poly(y, OUT_SR // g, sr // g)


def render_take(args):
    notes, total_ms, tempo, swing, take = args
    import synth
    tm = synth.time_map(tempo, swing)
    y, sr = render_voice(notes, total_ms, tm, take['singer'], take)
    y = _resample(y, sr)
    y = y / (np.sqrt(np.mean(y[np.abs(y) > 1e-4] ** 2)) + 1e-9) * 0.08 * take.get('gain', 1.0)
    return np.concatenate([np.zeros(int(OUT_SR * take.get('delay', 0) / 1000)), y])


def render_section(notes, total_beats, voice_type, tempo, swing, style='chor', pool=None):
    """Returns float array at OUT_SR: one neural singer (solo) or three (chor)."""
    import synth
    total_ms = synth.time_map(tempo, swing)(total_beats) + 500
    takes = TAKES[voice_type] if style == 'chor' else TAKES[voice_type][:1]
    jobs = [(notes, total_ms, tempo, swing, tk) for tk in takes]
    ys = list(pool.map(render_take, jobs)) if pool else [render_take(j) for j in jobs]
    n = max(len(y) for y in ys)
    mix = sum(np.pad(y, (0, n - len(y))) for y in ys) / math.sqrt(len(ys))
    return mix


def render_hum(midi, voice_type, dur_ms=4200):
    """Neural hummed 'mmm' at a pitch (for giving the starting notes)."""
    sg = get_singer(TAKES[voice_type][0]['singer'])
    pid = sg.v.config.phoneme_id_map
    ids = [pid['^'][0], pid['_'][0], pid['m'][0], pid['_'][0], pid['$'][0]]
    fr = int(dur_ms / sg.fms)
    y = sg.generate(ids, [2, 1, fr, 1, 2])
    sr = sg.sr
    f0, tt = pw.dio(y, sr, f0_floor=60, f0_ceil=700, frame_period=5.0)
    sp = pw.cheaptrick(y, f0, tt, sr)
    ap = pw.d4c(y, f0, tt, sr) ** 2
    hz = 440.0 * 2 ** ((midi - 69) / 12)
    newf0 = np.array([hz * (1 + 0.0035 * math.sin(2 * math.pi * 5.2 * t) * min(1, max(0, t - 0.5))) if 0.03 < t < len(y) / sr - 0.03 else 0
                      for t in tt])
    yy = pw.synthesize(newf0, sp, np.minimum(ap, 0.5), sr, 5.0)
    yy = _resample(yy, sr)
    r = int(0.15 * OUT_SR)
    yy[:r] *= np.linspace(0, 1, r) ** 2
    yy[-2 * r:] *= np.linspace(1, 0, 2 * r) ** 2
    return yy / (np.abs(yy).max() + 1e-9) * 0.7
