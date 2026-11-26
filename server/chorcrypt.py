"""Password protection for the public site: AES-256-GCM, key from PBKDF2-SHA256 (same scheme as the browser).
File format: b'CHOR1' + salt(16) + iv(12) + ciphertext   (salt per site for site files, per file for uploads)."""
import os, json, base64, hashlib
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

ITER = 150000
MAGIC = b'CHOR1'


def derive(password, salt):
    return hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, ITER, 32)


def encrypt(data, key, salt):
    iv = os.urandom(12)
    return MAGIC + salt + iv + AESGCM(key).encrypt(iv, data, None)


def decrypt_with_password(blob, password):
    assert blob[:5] == MAGIC, 'keine verschlüsselte Datei von „Singen mit anders“'
    salt, iv, ct = blob[5:21], blob[21:33], blob[33:]
    return AESGCM(derive(password, salt)).decrypt(iv, ct, None)


def site_key(site, password):
    """Load or create the site's salt; verify the password against the stored check value."""
    p = os.path.join(site, 'crypto.json')
    if os.path.exists(p):
        c = json.load(open(p))
        salt = base64.b64decode(c['salt'])
        key = derive(password, salt)
        blob = base64.b64decode(c['check'])
        AESGCM(key).decrypt(blob[21:33], blob[33:], None)  # raises if the password changed
        return key, salt
    salt = os.urandom(16)
    key = derive(password, salt)
    json.dump(dict(salt=base64.b64encode(salt).decode(), iter=ITER,
                   check=base64.b64encode(encrypt(b'chorprobe-ok', key, salt)).decode()), open(p, 'w'))
    return key, salt


def encrypt_tree(path, key, salt, skip=('.bin',)):
    for root, _, files in os.walk(path):
        for f in files:
            if f.endswith(skip):
                continue
            fp = os.path.join(root, f)
            open(fp + '.bin', 'wb').write(encrypt(open(fp, 'rb').read(), key, salt))
            os.unlink(fp)
