import pytest
from pydantic import ValidationError

from models import Customer, read_band, summarize_customer


def test_customer_payload():
    assert summarize_customer(" Mira ", 7) == {"name": "Mira", "id": 7}


def test_customer_accepts_field_name():
    assert Customer(name="Mira", external_id=7).external_id == 7


def test_rejects_empty_customer_name():
    with pytest.raises(ValidationError, match="name cannot be empty"):
        summarize_customer(" ", 7)


def test_reads_band():
    assert read_band({"minimum": "2", "maximum": 3}).minimum == 2


def test_rejects_inverted_band():
    with pytest.raises(ValidationError, match="minimum cannot exceed maximum"):
        read_band({"minimum": 10, "maximum": 3})


def test_missing_bound_uses_field_error():
    with pytest.raises(ValidationError, match="maximum"):
        read_band({"minimum": 1})
