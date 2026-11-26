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


# ---- public-key drop box: lets someone who does NOT know the choir password add files ----
# The site holds a public key (pubkey.pem, public) and the matching private key encrypted with the site key
# (privkey.bin). Files encrypted to the public key ("*.pkenc") can only be opened by the build (which has the password).
PKMAGIC = b'CHORPK1'


def site_keypair(site, key, salt):
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives import serialization
    pub_p, priv_p = os.path.join(site, 'pubkey.pem'), os.path.join(site, 'privkey.bin')
    if os.path.exists(priv_p):
        pem = AESGCM(key).decrypt(*(lambda b: (b[21:33], b[33:], None))(open(priv_p, 'rb').read()))
        return serialization.load_pem_private_key(pem, password=None)
    priv = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    pem = priv.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    open(priv_p, 'wb').write(encrypt(pem, key, salt))
    open(pub_p, 'wb').write(priv.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo))
    return priv


def _oaep():
    from cryptography.hazmat.primitives.asymmetric import padding
    from cryptography.hazmat.primitives import hashes
    return padding.OAEP(mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None)


def pk_encrypt(data, pub_pem):
    from cryptography.hazmat.primitives import serialization
    pub = serialization.load_pem_public_key(pub_pem)
    k, iv = os.urandom(32), os.urandom(12)
    wk = pub.encrypt(k, _oaep())
    return PKMAGIC + len(wk).to_bytes(2, 'big') + wk + iv + AESGCM(k).encrypt(iv, data, None)


def pk_decrypt(blob, priv):
    assert blob[:7] == PKMAGIC, 'keine Datei für den öffentlichen Schlüssel'
    n = int.from_bytes(blob[7:9], 'big')
    wk, rest = blob[9:9 + n], blob[9 + n:]
    k = priv.decrypt(wk, _oaep())
    return AESGCM(k).decrypt(rest[:12], rest[12:], None)
