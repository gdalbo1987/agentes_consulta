"""Criptografia simétrica dos segredos guardados em `IntegrationSetting`
(hoje um só: o client secret da Microsoft Graph).

Usa Fernet (biblioteca `cryptography`) com uma chave mestra vinda de
`SETTINGS_ENCRYPTION_KEY` no `.env` — se o banco vazar sozinho (backup, dump,
acesso indevido), os segredos continuam ilegíveis sem essa chave, que não fica
no banco. Gerar uma chave nova:

    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
"""
import os

from cryptography.fernet import Fernet, InvalidToken


class SettingsEncryptionKeyMissingError(Exception):
    pass


def _fernet() -> Fernet:
    chave = os.environ.get("SETTINGS_ENCRYPTION_KEY")
    if not chave:
        raise SettingsEncryptionKeyMissingError(
            "SETTINGS_ENCRYPTION_KEY não configurada no .env. Ela é necessária para "
            "gravar ou ler segredos das Integrações."
        )
    return Fernet(chave.encode())


def encrypt(value: str) -> str:
    """"" -> "" (campo vazio não é segredo nenhum, não precisa de token)."""
    if not value:
        return ""
    return _fernet().encrypt(value.encode()).decode()


def decrypt(value: str) -> str:
    """"" -> ""; token inválido/corrompido -> "" (nunca propaga exceção para o
    chamador — um segredo ilegível deve se comportar como "não configurado",
    não derrubar a página)."""
    if not value:
        return ""
    try:
        return _fernet().decrypt(value.encode()).decode()
    except (InvalidToken, ValueError):
        return ""
