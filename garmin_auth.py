"""
garmin_auth.py — Almacenamiento cifrado de credenciales de Garmin Connect.

Separado de app.py a propósito: es el único código del proyecto que maneja
secretos (usuario/contraseña de Garmin), así que conviene tenerlo aislado
para poder auditarlo y testearlo sin depender del resto de la aplicación
Flask (rate limiter, sesión, etc.).

Requiere el paquete `cryptography`. A diferencia de la versión anterior,
NO hay fallback a un cifrado más débil (XOR con clave fija) si no está
instalado: eso era prácticamente guardar la contraseña en texto plano.
Si falta la dependencia, las funciones fallan explícitamente.
"""

import base64
import hashlib
import logging
import sqlite3

try:
    from cryptography.fernet import Fernet, InvalidToken
    _CRYPTO_OK = True
except ImportError:
    _CRYPTO_OK = False


class CredencialesNoDisponibles(RuntimeError):
    """
    Se lanza cuando no se pueden leer o escribir las credenciales:
    - falta el paquete `cryptography`, o
    - el valor guardado no se puede descifrar con la SECRET_KEY actual
      (típicamente porque la SECRET_KEY cambió desde que se guardaron).
    En ambos casos hay que volver a cargar usuario/contraseña.
    """


def _get_fernet(secret_key: str) -> "Fernet":
    if not _CRYPTO_OK:
        raise CredencialesNoDisponibles(
            "El paquete 'cryptography' no está instalado (pip install cryptography). "
            "No se usa un cifrado más débil como fallback."
        )
    key = hashlib.sha256(secret_key.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def _encrypt(text: str, secret_key: str) -> str:
    if not text:
        return text
    return _get_fernet(secret_key).encrypt(text.encode()).decode()


def _decrypt(text: str, secret_key: str) -> str:
    if not text:
        return text
    f = _get_fernet(secret_key)
    try:
        return f.decrypt(text.encode()).decode()
    except InvalidToken:
        logging.error("GARMIN CREDS | No se pudo descifrar el valor guardado (¿cambió SECRET_KEY?)")
        raise CredencialesNoDisponibles(
            "No se pudieron descifrar las credenciales de Garmin guardadas. "
            "Probablemente cambió SECRET_KEY — hay que volver a cargar "
            "usuario y contraseña en Configuración."
        )


def get_credenciales_garmin(db_path: str, secret_key: str):
    """
    Devuelve (usuario, password) en texto plano.
    Devuelve ("", "") si todavía no se cargó nada.
    Propaga CredencialesNoDisponibles si hay un valor guardado pero no se
    puede descifrar (antes esto se tragaba en silencio y devolvía vacío).
    """
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        u = con.execute("SELECT valor FROM garmin_config WHERE clave='garmin_user'").fetchone()
        p = con.execute("SELECT valor FROM garmin_config WHERE clave='garmin_pass'").fetchone()
    finally:
        con.close()
    usuario = _decrypt(u["valor"], secret_key) if u and u["valor"] else ""
    passwd  = _decrypt(p["valor"], secret_key) if p and p["valor"] else ""
    return usuario, passwd


def set_credenciales_garmin(db_path: str, secret_key: str, usuario: str, passwd: str):
    con = sqlite3.connect(db_path)
    try:
        con.execute(
            "INSERT OR REPLACE INTO garmin_config (clave,valor,modificado) VALUES (?,?,datetime('now'))",
            ("garmin_user", _encrypt(usuario, secret_key)),
        )
        con.execute(
            "INSERT OR REPLACE INTO garmin_config (clave,valor,modificado) VALUES (?,?,datetime('now'))",
            ("garmin_pass", _encrypt(passwd, secret_key)),
        )
        con.commit()
    finally:
        con.close()


def credenciales_configuradas(db_path: str, secret_key: str) -> bool:
    try:
        usuario, passwd = get_credenciales_garmin(db_path, secret_key)
    except CredencialesNoDisponibles:
        return False
    return bool(usuario and passwd)
