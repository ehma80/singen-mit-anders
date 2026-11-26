"""Singen mit anders server: upload sheet music (PDF/MusicXML) -> per-voice sung audio with lyrics."""
import os, json, hashlib, subprocess, shutil, tempfile, statistics
from fastapi import FastAPI, UploadFile, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
import score as scoremod
import synth

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.environ.get('CHOR_DATA', os.path.join(BASE, '..', 'data'))
WEB = os.path.join(BASE, '..', 'web')
os.makedirs(DATA, exist_ok=True)
app = FastAPI(title='Singen mit anders')


def voice_type(v):
    name = (v.get('name') or '').lower()
    if any(k in name for k in ('sopran', 'alt', 'mezzo', 'soprano', 'alto')):
        return 'female'
    if any(k in name for k in ('tenor', 'bass', 'bari')):
        return 'male'
    mids = [n['midi'] for n in v['notes']] or [60]
    return 'female' if statistics.median(mids) >= 60 else 'male'


def audiveris_to_musicxml(pdf, outdir):
    exe = shutil.which('audiveris') or os.environ.get('AUDIVERIS')
    if not exe:
        return None
    subprocess.run([exe, '-batch', '-export', '-output', outdir, pdf], capture_output=True, timeout=900)
    for root, _, files in os.walk(outdir):
        for f in files:
            if f.endswith('.mxl') or f.endswith('.musicxml') or f.endswith('.xml'):
                return os.path.join(root, f)
    return None


def load_any(path, filename):
    fn = filename.lower()
    if fn.endswith(('.musicxml', '.xml', '.mxl')):
        return scoremod.from_musicxml(path)
    if fn.endswith('.pdf'):
        try:
            sc = scoremod.from_pdf(path)
            if sc['voices'] and sum(len(v['notes']) for v in sc['voices']) > 10:
                sc['source'] = 'vector-pdf'
                return sc
        except Exception as e:  # scanned / unsupported PDF
            print('vector reader failed:', e)
        out = tempfile.mkdtemp()
        mx = audiveris_to_musicxml(path, out)
        if mx:
            sc = scoremod.from_musicxml(mx)
            sc['source'] = 'audiveris'
            return sc
        raise HTTPException(422, 'Diese PDF konnte nicht gelesen werden. Digital erzeugte PDFs (aus Sibelius/Finale/'
                                 'MuseScore) gehen direkt; für gescannte Noten wird Audiveris benötigt. Alternativ MusicXML hochladen.')
    raise HTTPException(415, 'Bitte PDF oder MusicXML hochladen.')


import threading
_render_lock = threading.Lock()


def _prerender(sid):
    """After an upload: sing all voices (chor, 100 %) and the hum tones in the background."""
    d = os.path.join(DATA, sid)
    sc = json.load(open(os.path.join(d, 'score.json')))
    with _render_lock:
        for vi in range(len(sc['voices'])):
            out = os.path.join(d, f'v{vi}_t{sc["tempo"]}_s{int(bool(sc.get("swing")))}_chor.mp3')
            if not os.path.exists(out):
                render_mp3(sc, vi, sc['tempo'], bool(sc.get('swing')), out, 'chor')
        hd = os.path.join(DATA, 'hum'); os.makedirs(hd, exist_ok=True)
        for vt, m in sorted({(v['type'], n['midi']) for v in sc['voices'] for n in v['notes']}):
            p = os.path.join(hd, f'{vt}_{m}.mp3')
            if not os.path.exists(p):
                render_hum_mp3(m, vt, p)
        open(os.path.join(d, 'ready'), 'w').write('1')


@app.post('/api/upload')
async def upload(file: UploadFile):
    data = await file.read()
    sid = hashlib.sha1(data).hexdigest()[:12]
    d = os.path.join(DATA, sid)
    os.makedirs(d, exist_ok=True)
    src = os.path.join(d, 'source' + os.path.splitext(file.filename)[1].lower())
    with open(src, 'wb') as f:
        f.write(data)
    sc = load_any(src, file.filename)
    for v in sc['voices']:
        v['type'] = voice_type(v)
    sc['id'] = sid
    sc['swing'] = sc.get('swing', sc.get('title', '').lower().startswith('paradise'))
    json.dump(sc, open(os.path.join(d, 'score.json'), 'w'), ensure_ascii=False)
    threading.Thread(target=_prerender, args=(sid,), daemon=True).start()
    return sc


@app.get('/api/library')
def library():
    out = []
    for d in sorted(os.listdir(DATA), key=lambda d: -os.path.getmtime(os.path.join(DATA, d))):
        p = os.path.join(DATA, d, 'score.json')
        if os.path.exists(p):
            sc = json.load(open(p))
            out.append(dict(id=d, title=sc.get('title'), voices=[v['name'] for v in sc['voices']], measures=sc['measures'],
                            ready=os.path.exists(os.path.join(DATA, d, 'ready'))))
    return out


@app.get('/api/score/{sid}')
def get_score(sid: str):
    p = os.path.join(DATA, os.path.basename(sid), 'score.json')
    if not os.path.exists(p):
        raise HTTPException(404)
    return JSONResponse(json.load(open(p)))


@app.get('/api/musicxml/{sid}')
def musicxml(sid: str):
    d = os.path.join(DATA, os.path.basename(sid))
    out = os.path.join(d, 'score.musicxml')
    if not os.path.exists(out):
        scoremod.to_musicxml(json.load(open(os.path.join(d, 'score.json'))), out)
    return FileResponse(out, media_type='application/xml')


def _mix_takes(raws, takes, out_wav):
    import numpy as np, soundfile as sf
    sigs, sr = [], None
    for r, tk in zip(raws, takes):
        x, sr = sf.read(r)
        x = x / (np.sqrt(np.mean(x ** 2)) + 1e-9) * 0.06 * tk.get('gain', 1.0)
        x = np.concatenate([np.zeros(int(sr * tk.get('delay', 0) / 1000)), x])
        sigs.append(x)
    n = max(len(x) for x in sigs)
    mix = sum(np.pad(x, (0, n - len(x))) for x in sigs) / len(sigs) * 1.6
    sf.write(out_wav, mix, sr)


def _encode(y, out, sr=22050, reverb=True):
    """Peak-normalise, gate silences to true zero (no clicks/noise in rests), light room, MP3."""
    import numpy as np, soundfile as sf
    y = np.asarray(y, dtype=np.float64)
    y = y / (np.abs(y).max() + 1e-9) * 0.89
    # gate: 20 ms frames below -55 dB -> 0, with 10 ms ramps
    fr = int(0.02 * sr)
    n = len(y) // fr
    if n:
        rms = np.sqrt(np.mean(y[:n * fr].reshape(n, fr) ** 2, axis=1))
        g = (rms > 10 ** (-55 / 20)).astype(float)
        g = np.maximum(g, np.maximum(np.r_[g[1:], 0], np.r_[0, g[:-1]]))  # keep one frame around sound
        env = np.repeat(g, fr)
        k = int(0.01 * sr); ker = np.ones(k) / k
        env = np.convolve(env, ker, mode='same')
        y[:n * fr] *= env
    tmp = out + '.tmp.wav'
    sf.write(tmp, y, sr)
    fx = 'aecho=0.8:0.5:45|95|160:0.18|0.11|0.06' if reverb else 'anull'
    subprocess.run(['ffmpeg', '-y', '-loglevel', 'error', '-i', tmp, '-af', fx + ',alimiter=limit=0.95',
                    '-ac', '1', '-ar', str(sr), '-b:a', '40k', out], check=True)
    os.unlink(tmp)


def render_mp3(sc, vi, tempo, swing, out, style='chor', pool=None):
    v = sc['voices'][vi]
    total = sc['measures'] * sc.get('beats', 4)
    vt = v.get('type', 'female')
    if style in ('chor', 'solo'):
        import nsynth
        y = nsynth.render_section(v['notes'], total, vt, tempo, swing, style, pool)
        _encode(y, out)
        return
    # 'robot': old diphone voice (MBROLA)
    import soundfile as sf
    raw, _ = synth.render(v['notes'], out, vt, tempo, swing, total)
    y, sr = sf.read(raw); os.unlink(raw)
    _encode(y, out, sr)


def render_hum_mp3(midi, vt, out):
    import nsynth
    _encode(nsynth.render_hum(midi, vt), out)


@app.get('/api/hum/{vt}/{midi}.mp3')
def hum(vt: str, midi: int):
    if vt not in ('female', 'male') or not 30 <= midi <= 90:
        raise HTTPException(400)
    d = os.path.join(DATA, 'hum'); os.makedirs(d, exist_ok=True)
    out = os.path.join(d, f'{vt}_{midi}.mp3')
    if not os.path.exists(out):
        render_hum_mp3(midi, vt, out)
    return FileResponse(out, media_type='audio/mpeg')


@app.get('/api/audio/{sid}/{vi}.mp3')
def audio(sid: str, vi: int, tempo: int = 0, swing: int = -1, style: str = 'chor'):
    d = os.path.join(DATA, os.path.basename(sid))
    sc = json.load(open(os.path.join(d, 'score.json')))
    tempo = tempo or sc['tempo']
    sw = bool(sc.get('swing')) if swing < 0 else bool(swing)
    style = style if style in ('solo', 'robot') else 'chor'
    out = os.path.join(d, f'v{vi}_t{tempo}_s{int(sw)}_{style}.mp3')
    if not os.path.exists(out):
        render_mp3(sc, vi, tempo, sw, out, style)
    return FileResponse(out, media_type='audio/mpeg')


@app.on_event('startup')
def seed_library():
    """First start: put the example pieces from ../seed into the library."""
    seed = os.path.join(BASE, '..', 'seed')
    if not os.path.isdir(seed) or any(os.path.exists(os.path.join(DATA, d, 'score.json')) for d in os.listdir(DATA)):
        return
    for f in sorted(os.listdir(seed)):
        src = os.path.join(seed, f)
        data = open(src, 'rb').read()
        sid = hashlib.sha1(data).hexdigest()[:12]
        d = os.path.join(DATA, sid); os.makedirs(d, exist_ok=True)
        dst = os.path.join(d, 'source' + os.path.splitext(f)[1].lower()); shutil.copy(src, dst)
        try:
            sc = load_any(dst, f)
        except Exception as e:
            print('seed failed', f, e); continue
        for v in sc['voices']:
            v['type'] = voice_type(v)
        sc['id'] = sid
        sc['swing'] = sc.get('title', '').lower().startswith('paradise')
        json.dump(sc, open(os.path.join(d, 'score.json'), 'w'), ensure_ascii=False)
        threading.Thread(target=_prerender, args=(sid,), daemon=True).start()


app.mount('/', StaticFiles(directory=WEB, html=True), name='web')
