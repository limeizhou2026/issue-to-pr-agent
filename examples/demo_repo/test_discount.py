from discount import apply_discount


def test_twenty_percent_discount_on_100():
    assert apply_discount(100, 20) == 80


def test_zero_percent_discount_is_a_no_op():
    assert apply_discount(50, 0) == 50
