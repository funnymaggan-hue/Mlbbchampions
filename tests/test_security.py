import time

import pytest

from app.security import InitDataError, validate_init_data
from tests.helpers import TEST_TOKEN, make_init_data


def test_valid():
    user = validate_init_data(make_init_data(777), TEST_TOKEN, 3600)
    assert user["id"] == 777


def test_wrong_token_rejected():
    data = make_init_data(777, token="999:OTHER")
    with pytest.raises(InitDataError):
        validate_init_data(data, TEST_TOKEN, 3600)


def test_expired_rejected():
    data = make_init_data(777, auth_date=int(time.time()) - 7200)
    with pytest.raises(InitDataError):
        validate_init_data(data, TEST_TOKEN, 3600)


def test_tampered_user_rejected():
    data = make_init_data(777).replace("777", "1001")  # admin ID ga almashtirish
    with pytest.raises(InitDataError):
        validate_init_data(data, TEST_TOKEN, 3600)


def test_missing_hash_and_empty():
    with pytest.raises(InitDataError):
        validate_init_data("auth_date=1&user=%7B%7D", TEST_TOKEN, 3600)
    with pytest.raises(InitDataError):
        validate_init_data("", TEST_TOKEN, 3600)
