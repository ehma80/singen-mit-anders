"""Static library site: index.html + library.json + songs/<id>/{song.json, score.xml, audio_*.mp3, hum_*.mp3}.
Usage: build_library.py <site_dir> add <pdf|musicxml> <id> [asset_id]   |   build_library.py <site_dir> page"""
import sys, os, json, re, shutil
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import score as scoremod, app

TEMPOS = [70, 85, 100]


def add_song(site, src, sid, source=None, procs=4):
    import math, numpy as np, nsynth, synth
    from multiprocessing import Pool
    d = os.path.join(site, 'songs', sid)
    os.makedirs(d, exist_ok=True)
    sc = app.load_any(src, os.path.basename(src))
    for v in sc['voices']:
        v['type'] = app.voice_type(v)
    sc['swing'] = bool(sc.get('swing') or sc['title'].lower().startswith('paradise'))
    total = sc['measures'] * sc.get('beats', 4)
    with Pool(procs) as pool:
        for pct in TEMPOS:
            tempo = round(sc['tempo'] * pct / 100, 2)
            total_ms = synth.time_map(tempo, sc['swing'])(total) + 500
            for vi, v in enumerate(sc['voices']):
                jobs = [(v['notes'], total_ms, tempo, sc['swing'], tk) for tk in nsynth.TAKES[v['type']]]
                ys = pool.map(nsynth.render_take, jobs)
                n = max(len(y) for y in ys)
                app._encode(ys[0], os.path.join(d, f'audio_solo_{pct}_{vi}.mp3'))
                mix = sum(np.pad(y, (0, n - len(y))) for y in ys) / math.sqrt(len(ys))
                app._encode(mix, os.path.join(d, f'audio_chor_{pct}_{vi}.mp3'))
                print(sid, pct, vi, flush=True)
    for vt, m in sorted({(v['type'], n['midi']) for v in sc['voices'] for n in v['notes']}):
        app.render_hum_mp3(m, vt, os.path.join(d, f'hum_{vt}_{m}.mp3'))
    scoremod.to_musicxml(json.loads(json.dumps(sc)), os.path.join(d, 'score.xml'))
    json.dump(dict(score=sc, tempos=TEMPOS), open(os.path.join(d, 'song.json'), 'w'), ensure_ascii=False)
    libp = os.path.join(site, 'library.json')
    lib = json.load(open(libp)) if os.path.exists(libp) else []
    lib = [x for x in lib if x['id'] != sid]
    lib.append(dict(id=sid, title=sc['title'], voices=[v['name'] for v in sc['voices']], measures=sc['measures'], source=source))
    json.dump(lib, open(libp, 'w'), ensure_ascii=False, indent=1)


def write_page(site, upload_url='', encrypted=False):
    os.makedirs(site, exist_ok=True)
    html = open(os.path.join(app.WEB, 'index.html')).read()
    html = html.replace('<script src="verovio-toolkit-wasm.js">', '<script>window.LIBRARY_MODE=true;window.UPLOAD_URL=' + json.dumps(upload_url) + ';window.ENCRYPTED=' + json.dumps(encrypted) + ';</script>\n<script src="verovio-toolkit-wasm.js">')
    open(os.path.join(site, 'index.html'), 'w').write(html)
    shutil.copy(os.path.join(app.WEB, 'verovio-toolkit-wasm.js'), site)


if __name__ == '__main__':
    site, cmd = sys.argv[1], sys.argv[2]
    if cmd == 'add':
        add_song(site, sys.argv[3], sys.argv[4], sys.argv[5] if len(sys.argv) > 5 else None)
    write_page(site)
