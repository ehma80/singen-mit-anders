"""GitHub Actions: sing every piece in noten/ that is new or changed, remove deleted ones, rebuild the page.
With CHOR_PASSWORT set, uploads are expected as *.enc (encrypted in the app's Chorleitung area) and every
generated file is stored encrypted (*.bin), so the public repository reveals neither notes nor recordings.
Usage: ci_build.py <noten_dir> <site_dir> <upload_url>"""
import sys, os, re, json, hashlib, shutil, tempfile, tarfile, io
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_library as bl
import chorcrypt as cc

noten, site, upload_url = sys.argv[1], sys.argv[2], (sys.argv[3] if len(sys.argv) > 3 else '')
PW = os.environ.get('CHOR_PASSWORT', '')
os.makedirs(os.path.join(site, 'songs'), exist_ok=True)
key = salt = None
priv = None
if PW:
    key, salt = cc.site_key(site, PW)
    priv = cc.site_keypair(site, key, salt)  # public-key drop box (pubkey.pem) for *.pkenc files
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
    elif fl.endswith('.pkenc'):  # encrypted to the site's public key: a sheet-music file or a finished song bundle (.tar)
        if not priv:
            print('Übersprungen (kein CHOR_PASSWORT gesetzt):', f); continue
        name = f[:-6]
        try:
            plain = cc.pk_decrypt(open(src, 'rb').read(), priv)
        except Exception:
            print('FEHLER: nicht lesbar', f); continue
        src_plain = os.path.join(tmp, name); open(src_plain, 'wb').write(plain)
    elif fl.endswith(('.pdf', '.xml', '.musicxml', '.mxl')):
        name, src_plain = f, src
    else:
        continue
    base = name[:-4] if name.lower().endswith('.tar') else os.path.splitext(name)[0]
    sid = re.sub(r'[^a-z0-9]+', '-', base.lower()).strip('-') or 'stueck'
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
        if src_plain.lower().endswith('.tar'):  # finished song (already sung): just unpack and register
            d = os.path.join(site, 'songs', sid); os.makedirs(d)
            with tarfile.open(src_plain) as t:
                for m in t.getmembers():
                    n = m.name[2:] if m.name.startswith('./') else m.name
                    if m.isfile() and '/' not in n and not n.startswith('.'):
                        open(os.path.join(d, n), 'wb').write(t.extractfile(m).read())
            sc = json.load(open(os.path.join(d, 'song.json')))['score']
            lib = json.load(open(libp)) if os.path.exists(libp) else []
            lib = [x for x in lib if x['id'] != sid] + [dict(id=sid, title=sc['title'], voices=[v['name'] for v in sc['voices']], measures=sc['measures'], source=h)]
            json.dump(lib, open(libp, 'w'), ensure_ascii=False, indent=1)
        else:
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
