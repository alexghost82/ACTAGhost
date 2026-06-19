from acta.security import PermissionDenied, PermissionRegistry
from acta.security.crypto import Crypto


def test_crypto_round_trip(services):
    crypto: Crypto = services.memory.crypto
    token = crypto.encrypt("секретные данные")
    assert token != "секретные данные"
    assert crypto.decrypt(token) == "секретные данные"


def test_permissions_grant_and_require():
    reg = PermissionRegistry()
    assert reg.has("memory", "memory.read")
    reg.require("memory", "memory.read")  # should not raise

    try:
        reg.require("ui", "integration.network")
    except PermissionDenied:
        pass
    else:
        raise AssertionError("expected PermissionDenied")

    reg.grant("ui", "integration.network")
    assert reg.has("ui", "integration.network")


def test_audit_records(services):
    services.audit.record("tester", "ping", value=1)
    tail = services.audit.tail(5)
    assert tail[-1]["actor"] == "tester"
    assert tail[-1]["details"]["value"] == 1
