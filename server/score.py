"""Common score model + loaders (vector PDF via vecomr, MusicXML via music21) + lyric clean-up."""
import re
from fractions import Fraction as F
import pyphen

_hy = pyphen.Pyphen(lang='de_DE')
FUNC = ['vom', 'ist', 'die', 'der', 'das', 'und', 'noch', 'mir', 'ein', 'den', 'dem', 'im', 'in', 'zu', 'so']


def split_glued(text):
    parts = re.sub(r"(?<=[a-zäöüß'.,])(?=[A-ZÄÖÜ])", ' ', text).split()
    out = []
    for p in parts:
        tails = []
        found = True
        while found:
            found = False
            for f in FUNC:
                head = p[:-len(f)]
                if p.lower().endswith(f) and len(head) >= 2 and re.search('[aeiouäöüy]', head.lower()):
                    tails.insert(0, p[-len(f):])
                    p = head
                    found = True
                    break
        out.append(p)
        out.extend(tails)
    return out


def syllabify(word):
    syl = _hy.inserted(word).split('-')
    res = []
    for x in syl:
        if res and (x.startswith("'") or len(x.strip(".,;:!?'")) == 0):
            res[-1] += x
        else:
            res.append(x)
    return res


COMMON_WORDS = {'sie', 'ist', 'und', 'dann', 'die', 'das', 'der', 'den', 'im', 'in', 'zu', 'so', 'da', 'wo', 'ja', 'nie', 'wie', 'mit', 'vom', 'von',
                'auf', 'aus', 'nicht', 'nichts', 'ich', 'du', 'wir', 'ihr', 'er', 'es', 'man', 'ob', 'um', 'am', 'an', 'uh', 'oh', 'ah', 'dm', 'du', 'ba', 'dah'}
WEAK_WORDS = {'der', 'des', 'den', 'dem', 'er', 'es', 'en', 'el', 'ten'}


def join_split_words(notes):
    """PDF lyrics sometimes miss hyphens ("al les ei tel"): join an unstressed schwa syllable to the one before."""
    lyr = [n for n in notes if n.get('lyric')]
    for k, (a, b) in enumerate(zip(lyr, lyr[1:])):
        bt = b['lyric']
        core = re.sub(r"[.,;:!?]", '', bt)
        if k > 0 and lyr[k - 1].get('hyph'):   # a ends a hyphenated word, so it is complete ("wünsch-te je-mand")
            continue
        if re.sub(r"[.,;:!?']", '', a['lyric']).lower() in COMMON_WORDS:
            continue
        if a.get('hyph') or not core or not core[0].islower() or core in WEAK_WORDS or len(core) > 4:
            continue
        vowels = re.findall('[aeiouäöüy]+', core)
        if vowels == ['e'] and re.fullmatch(r"[a-zäöüß]+", re.sub(r"[.,;:!?]", '', a['lyric'])) and \
                syllabify(re.sub(r"[.,;:!?]", '', a['lyric']) + core) == [re.sub(r"[.,;:!?]", '', a['lyric']), core]:
            a['hyph'] = True
    return notes


def fix_lyrics(notes):
    """Distribute glued lyric tokens over following lyric-less notes."""
    join_split_words(notes)
    n = len(notes)
    for i, nt in enumerate(notes):
        ly = nt.get('lyric')
        if not ly:
            continue
        # free slots after i: notes without lyric, not tied-from, until next lyric or rest gap
        slots = []
        j = i + 1
        while j < n and not notes[j].get('lyric'):
            if notes[j - 1].get('tie'):
                j += 1
                continue
            slots.append(j)
            j += 1
        text = ly
        words = split_glued(text)
        prev_h = i > 0 and any(notes[k].get('lyric') and notes[k].get('hyph') for k in [max(range(i), key=lambda k: notes[k]['t'] if notes[k].get('lyric') else -1)] if i > 0)
        if len(words) == 1 and not slots:
            continue
        if len(words) == 1:
            # long word without hyphen on several notes: maybe needs syllables (e.g. 'Himmel')
            syl = syllabify(text)
            if len(syl) > 1 and len(syl) - 1 <= len(slots) and not nt.get('hyph') and not prev_h and len(text) >= 5:
                words = syl
                pieces = [(w, k < len(syl) - 1) for k, w in enumerate(syl)]
            else:
                continue
        else:
            pieces = [(w, False) for w in words]
            # expand by syllables if more slots available
            k = 0
            while len(pieces) - 1 < len(slots) and k < len(pieces):
                w, h = pieces[k]
                syl = syllabify(w)
                if len(syl) > 1 and len(pieces) - 1 + len(syl) - 1 <= len(slots):
                    pieces[k:k + 1] = [(s, True) for s in syl[:-1]] + [(syl[-1], h)]
                    k += len(syl)
                else:
                    k += 1
        if len(pieces) - 1 > len(slots):
            continue
        nt['lyric'] = pieces[0][0]
        nt['hyph'] = pieces[0][1]
        for (w, h), j in zip(pieces[1:], slots):
            notes[j]['lyric'] = w
            notes[j]['hyph'] = h
        if len(pieces) > 1:
            notes[slots[len(pieces) - 2]]['hyph'] = ly and nt.get('_orig_hyph', False) or pieces[-1][1]
    return notes


def from_pdf(path):
    import vecomr
    raw = vecomr.to_score(path)
    meta = raw['meta']
    names_default = ['Sopran', 'Alt', 'Bariton', 'Bass', 'Tenor']
    nv = len(raw['voices'])
    # key per voice from parse: re-run build with keys from staves
    vecomr.KEY.clear()
    keys = vecomr.LAST_KEYS
    for i in range(nv):
        vecomr.KEY[i] = keys.get(i, 0)
    meta2, voices, report, nm = vecomr.build(path, names=(vecomr.LAST_NAMES or names_default)[:nv] + names_default[nv:])
    out = dict(title=meta.get('title') or 'Stück', tempo=meta.get('tempo') or 90, beats=4, measures=nm, key=keys.get(0, 0),
               report=report, voices=[])
    for vi, v in enumerate(voices):
        notes = []
        for n in v:
            ly = n['lyric']
            notes.append(dict(t=float(n['t']), d=float(n['d']), midi=n['midi'], m=n['m'],
                              lyric=ly['text'] if ly else None, hyph=bool(ly and ly['hyph']), tie=n['tie'],
                              _orig_hyph=bool(ly and ly['hyph'])))
        # ties: only keep if same pitch and next has no lyric
        for a, b in zip(notes, notes[1:]):
            if a['tie'] and not (b['midi'] == a['midi'] and not b['lyric'] and abs(a['t'] + a['d'] - b['t']) < 1e-6):
                a['tie'] = False
        if notes:
            notes[-1]['tie'] = False
        fix_lyrics(notes)
        for x in notes:
            x.pop('_orig_hyph', None)
        name = (vecomr.LAST_NAMES[vi] if vecomr.LAST_NAMES and vi < len(vecomr.LAST_NAMES) else names_default[vi])
        out['voices'].append(dict(name=name, notes=notes))
    return out


def from_musicxml(path):
    import music21
    sc = music21.converter.parse(path)
    tempo = 90
    mm = sc.recurse().getElementsByClass('MetronomeMark')
    for m in mm:
        tempo = int(m.getQuarterBPM() or 90)
        break
    ts = sc.recurse().getElementsByClass('TimeSignature')
    beats = float(ts[0].barDuration.quarterLength) if ts else 4.0
    out = dict(title=(sc.metadata.title if sc.metadata and sc.metadata.title else 'Stück'), tempo=tempo, beats=beats,
               report=[], voices=[])
    nmeas = 0
    for part in sc.parts:
        voices_in_part = part.voicesToParts() if part.hasVoices() else None
        subparts = list(voices_in_part.parts) if voices_in_part is not None else [part]
        for spi, sp in enumerate(subparts):
            notes = []
            flat = sp.flatten().notes
            for n in flat:
                if n.isChord:
                    p = max(n.pitches, key=lambda p: p.midi)
                else:
                    p = n.pitch
                ly = n.lyrics[0] if n.lyrics else None
                tie = bool(n.tie and n.tie.type in ('start', 'continue'))
                mnum = n.measureNumber or 0
                nmeas = max(nmeas, mnum)
                notes.append(dict(t=float(n.getOffsetInHierarchy(sc)), d=float(n.quarterLength), midi=int(round(p.ps)),
                                  m=mnum, lyric=(ly.text if ly else None),
                                  hyph=bool(ly and ly.syllabic in ('begin', 'middle')), tie=tie))
            name = part.partName or f'Stimme {len(out["voices"]) + 1}'
            if len(subparts) > 1:
                name += f' {spi + 1}'
            out['voices'].append(dict(name=name, notes=notes))
    out['measures'] = nmeas
    return out


def to_musicxml(sc, path, key_fifths=None, tempo_for_xml=60):
    """Write MusicXML (with lyrics) from the score model. Tempo is set to 60 so 1 beat = 1000 ms in renderers."""
    import music21 as m21
    from fractions import Fraction
    s = m21.stream.Score()
    s.insert(0, m21.metadata.Metadata(title=sc.get('title', '')))
    beats = sc.get('beats', 4)
    total = sc['measures'] * beats
    kf = sc.get('key', -3) if key_fifths is None else key_fifths
    for vi, v in enumerate(sc['voices']):
        p = m21.stream.Part(id=f'P{vi+1}')
        p.partName = v['name']
        lo = min((n['midi'] for n in v['notes']), default=60)
        p.insert(0, m21.clef.TrebleClef() if v.get('type') == 'female' or sum(n['midi'] for n in v['notes']) / max(1, len(v['notes'])) >= 57 else m21.clef.BassClef())
        p.insert(0, m21.key.KeySignature(kf))
        p.insert(0, m21.meter.TimeSignature(f'{int(beats)}/4'))
        t = Fraction(0)
        for n in v['notes']:
            nt = Fraction(n['t']).limit_denominator(48)
            nd = Fraction(n['d']).limit_denominator(48)
            if nt > t:
                p.insert(float(t), m21.note.Rest(quarterLength=nt - t))
            names = ['C', 'D-', 'D', 'E-', 'E', 'F', 'G-', 'G', 'A-', 'A', 'B-', 'B'] if kf < 0 else \
                    ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']
            note = m21.note.Note(names[n['midi'] % 12] + str(n['midi'] // 12 - 1), quarterLength=nd)
            if n.get('lyric'):
                ly = m21.note.Lyric(text=n['lyric'])
                note.lyrics.append(ly)
            if n.get('tie'):
                note.tie = m21.tie.Tie('start')
            p.insert(float(nt), note)
            t = nt + nd
        if t < total:
            p.insert(float(t), m21.note.Rest(quarterLength=Fraction(total) - t))
        s.insert(0, p)
    # syllabic from hyph flags
    for vi, v in enumerate(sc['voices']):
        prev_h = False
        for n in v['notes']:
            if n.get('lyric'):
                n['_syl'] = ('middle' if n.get('hyph') else 'end') if prev_h else ('begin' if n.get('hyph') else 'single')
                prev_h = n.get('hyph')
    for part, v in zip(s.parts, sc['voices']):
        lyr = iter([n for n in v['notes'] if n.get('lyric')])
        for note in part.flatten().notes:
            if note.lyrics:
                n = next(lyr)
                note.lyrics[0].syllabic = n.pop('_syl')
    # fix tie stops
    for part in s.parts:
        prev = None
        for note in part.flatten().notes:
            if prev is not None and prev.tie is not None and prev.tie.type == 'start' and note.pitch.midi == prev.pitch.midi:
                note.tie = m21.tie.Tie('stop' if note.tie is None else 'continue')
            prev = note
    s.makeNotation(inPlace=True)
    s.write('musicxml', fp=path)
    return path
