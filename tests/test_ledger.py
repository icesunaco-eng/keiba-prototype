import sqlite3

import pytest

from keiba.ledger import Ledger, LedgerConflict


def _add(led, payload):
    return led.add_prediction("2025-01-04|東京|1", "1h", "rule", "v0", payload, "in", "model", "code")


def test_same_key_same_payload_is_noop(tmp_path):
    led = Ledger(tmp_path / "l.sqlite")
    assert _add(led, {"a": 1}) is True
    assert _add(led, {"a": 1}) is False
    led.commit()
    assert led.verify() == {"predictions": 1, "hash_mismatch": 0}


def test_same_key_different_payload_is_rejected(tmp_path):
    led = Ledger(tmp_path / "l.sqlite")
    _add(led, {"a": 1})
    with pytest.raises(LedgerConflict):
        _add(led, {"a": 2})


def test_update_and_delete_are_blocked(tmp_path):
    led = Ledger(tmp_path / "l.sqlite")
    _add(led, {"a": 1})
    led.commit()
    with pytest.raises(sqlite3.DatabaseError):
        led.conn.execute("UPDATE predictions SET payload='{}'")
    with pytest.raises(sqlite3.DatabaseError):
        led.conn.execute("DELETE FROM predictions")


def test_read_only_cannot_write(tmp_path):
    led = Ledger(tmp_path / "l.sqlite")
    _add(led, {"a": 1})
    led.commit()
    led.close()
    ro = Ledger(tmp_path / "l.sqlite", read_only=True)
    with pytest.raises(sqlite3.OperationalError):
        ro.add_prediction("2025-01-04|東京|2", "1h", "rule", "v0", {"b": 1}, "in", "model", "code")
    assert next(ro.predictions())["a"] == 1


def test_settlement_conflict(tmp_path):
    led = Ledger(tmp_path / "l.sqlite")
    led.add_settlement("k", "1h", "rule", "v0", "w", {"returned": 0})
    with pytest.raises(LedgerConflict):
        led.add_settlement("k", "1h", "rule", "v0", "w", {"returned": 100})
