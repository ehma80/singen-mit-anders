"""GitHub Actions: sing every piece in noten/ that is new or changed, remove deleted ones, rebuild the page.
With CHOR_PASSWORT set, uploads are expected as *.enc (encrypted in the app's Chorleitung area) and every
generated file is stored encrypted (*.bin), so the public repository reveals neither notes nor recordings.
Usage: ci_build.py <noten_dir> <site_dir> <upload_url>"""
import sys, os, re, json, hashlib, shutil, tempfile
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_library as bl
import chorcrypt as cc

noten, site, upload_url = sys.argv[1], sys.argv[2], (sys.argv[3] if len(sys.argv) > 3 else '')
PW = os.environ.get('CHOR_PASSWORT', '')
os.makedirs(os.path.join(site, 'songs'), exist_ok=True)
key = salt = None
if PW:
    key, salt = cc.site_key(site, PW)
libp = os.path.join(site, 'library.json')
if PW and os.path.exists(libp + '.bin'):
    open(libp, 'wb').write(cc.decrypt_with_password(open(libp + '.bin', 'rb').read(), PW))
lib = json.load(open(libp)) if os.path.exists(libp) else []
have = {x['id']: x for x in lib}
wanted = {}
tmp = tempfile.mkdtemp()
for f in sorted(os.listdir(noten)):
    fl = f.lower()
    src = os.path.join(noten, f)
    if fl.endswith('.enc'):
        if not PW:
            print('Übersprungen (kein CHOR_PASSWORT gesetzt):', f); continue
        name = f[:-4]
        try:
            plain = cc.decrypt_with_password(open(src, 'rb').read(), PW)
        except Exception:
            print('FEHLER: falsches Passwort für', f); continue
        src_plain = os.path.join(tmp, name); open(src_plain, 'wb').write(plain)
    elif fl.endswith(('.pdf', '.xml', '.musicxml', '.mxl')):
        name, src_plain = f, src
    else:
        continue
    sid = re.sub(r'[^a-z0-9]+', '-', os.path.splitext(name)[0].lower()).strip('-') or 'stueck'
    h = hashlib.sha1(open(src, 'rb').read()).hexdigest()[:12]
    wanted[sid] = (name, h, src_plain)
failed = []
for sid, (name, h, src_plain) in wanted.items():
    done = os.path.exists(os.path.join(site, 'songs', sid, 'song.json' + ('.bin' if PW else '')))
    if sid in have and have[sid].get('source') == h and done:
        print('unverändert:', name); continue
    print('singe ein:', name, flush=True)
    shutil.rmtree(os.path.join(site, 'songs', sid), ignore_errors=True)
    try:
        bl.add_song(site, src_plain, sid, source=h)
        if PW:
            cc.encrypt_tree(os.path.join(site, 'songs', sid), key, salt)
    except Exception as e:
        print('FEHLER bei', name, ':', e); failed.append(name)
lib = json.load(open(libp)) if os.path.exists(libp) else []
for x in list(lib):
    if x['id'] not in wanted:
        print('entfernt:', x['id']); shutil.rmtree(os.path.join(site, 'songs', x['id']), ignore_errors=True); lib.remove(x)
json.dump(lib, open(libp, 'w'), ensure_ascii=False, indent=1)
if PW:
    open(libp + '.bin', 'wb').write(cc.encrypt(open(libp, 'rb').read(), key, salt))
    os.unlink(libp)
if not os.path.exists(os.path.join(site, 'count', '1.mp3')):  # spoken count-in words (not secret, stay unencrypted)
    import make_counts
    make_counts.make(site)
bl.write_page(site, upload_url, encrypted=bool(PW))
open(os.path.join(site, '.nojekyll'), 'w').close()
if failed:
    print('Nicht lesbar:', ', '.join(failed))
