"""HashedSecret Value — argon2id hash for User passwords and ApiKey secrets.

Used as a one-column composite on entities. Hashing and verification are
CPU-bound; callers MUST wrap them in `asyncio.to_thread(...)` to avoid
blocking the event loop.
"""

from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from pydantic import SecretStr

from hearth import Value

_hasher = PasswordHasher()


def _to_str(plaintext: str | SecretStr) -> str:
    return plaintext.get_secret_value() if isinstance(plaintext, SecretStr) else plaintext


class HashedSecret(Value):
    """An argon2id hash. Used by User.password and ApiKey.key_hash.

    Both `from_plaintext` and `verify` are CPU-bound (~50-100ms by design).
    Plugin code MUST invoke them via `asyncio.to_thread(...)` to avoid
    blocking the event loop.
    """

    hash: str

    @classmethod
    def from_plaintext(cls, plaintext: str | SecretStr) -> HashedSecret:
        return cls(hash=_hasher.hash(_to_str(plaintext)))

    def verify(self, plaintext: str | SecretStr) -> bool:
        try:
            return _hasher.verify(self.hash, _to_str(plaintext))
        except VerifyMismatchError:
            return False

    def needs_rehash(self) -> bool:
        """Return True when the stored hash's parameters are weaker than the
        current argon2 library defaults. Callers that just successfully
        verified a plaintext should check this and re-hash if True, so
        upgrades to the hashing parameters propagate as users authenticate.
        """
        return _hasher.check_needs_rehash(self.hash)
