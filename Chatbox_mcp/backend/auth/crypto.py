import os
import logging
from cryptography.fernet import Fernet

logger = logging.getLogger(__name__)

# Load FERNET_KEY from environment or generate a temporary one
_fernet_key = os.environ.get("FERNET_KEY")
if not _fernet_key:
    # Generate a temporary key for the process lifetime
    _fernet_key = Fernet.generate_key().decode()
    logger.warning(
        "FERNET_KEY environment variable is not set. Generated a temporary key "
        f"for this process session: {_fernet_key}. Note that restarting the server "
        "will cause decryption errors for any credentials encrypted with this temporary key!"
    )
else:
    # Ensure it's a valid base64 key
    try:
        _key_bytes = _fernet_key.strip().encode()
        Fernet(_key_bytes)
        _fernet_key = _fernet_key.strip()
    except Exception as e:
        logger.error(f"Invalid FERNET_KEY provided in environment: {e}. Generating a temporary one.")
        _fernet_key = Fernet.generate_key().decode()

_cipher_suite = Fernet(_fernet_key.encode())

def encrypt(plaintext: str) -> str:
    """Encrypt a plaintext string using Fernet and return ciphertext as URL-safe base64 string."""
    if not plaintext:
        return plaintext
    if not isinstance(plaintext, str):
        plaintext = str(plaintext)
    ciphertext_bytes = _cipher_suite.encrypt(plaintext.encode("utf-8"))
    return ciphertext_bytes.decode("utf-8")

def decrypt(ciphertext: str) -> str:
    """
    Decrypt a ciphertext string.
    If it is not a valid encrypted token (e.g., legacy plaintext value),
    return it as-is.
    """
    if not ciphertext:
        return ciphertext
    if not isinstance(ciphertext, str):
        ciphertext = str(ciphertext)
    
    if not is_encrypted(ciphertext):
        return ciphertext
        
    try:
        decrypted_bytes = _cipher_suite.decrypt(ciphertext.encode("utf-8"))
        return decrypted_bytes.decode("utf-8")
    except Exception as e:
        logger.error(f"Decryption failed: {e}")
        return ciphertext

def is_encrypted(value: str) -> bool:
    """
    Check if a string looks like a Fernet encrypted token.
    Fernet tokens start with 'gAAAAA'.
    """
    if not value or not isinstance(value, str):
        return False
    return value.startswith("gAAAAA")
