import base64, json, hashlib
from nacl.secret import SecretBox
from nacl import utils

def derive_key(secret: str) -> bytes:
    return hashlib.sha256(secret.encode()).digest()

def encrypt_blob(secret_key: str, data: dict) -> str:
    key = derive_key(secret_key)
    box = SecretBox(key)
    nonce = utils.random(SecretBox.NONCE_SIZE)
    ct = box.encrypt(json.dumps(data).encode(), nonce)
    return base64.b64encode(ct).decode()

def decrypt_blob(secret_key: str, b64cipher: str) -> dict:
    key = derive_key(secret_key)
    box = SecretBox(key)
    raw = base64.b64decode(b64cipher)
    pt = box.decrypt(raw)
    return json.loads(pt.decode())
