from calc import divide, multiply


def test_divide_by_zero_returns_none():
    """唯一需要修的行为。"""
    assert divide(1, 0) is None


def test_divide_normal():
    assert divide(6, 3) == 2


def test_multiply_untouched():
    assert multiply(2, 3) == 6
