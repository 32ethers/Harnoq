"""Tests for coupon settlement."""
import pytest

from coupon import (
    Coupon,
    CouponExpired,
    CouponRejected,
    DuplicateCoupon,
    settle,
)


def _coupon(code, amount=1000, min_spend=0, valid_until_day=100):
    return Coupon(
        code=code, amount=amount, min_spend=min_spend, valid_until_day=valid_until_day
    )


def test_single_coupon_reduces_the_total():
    result = settle(5000, [_coupon("A", amount=1200)], today=1)
    assert result.final_amount == 3800
    assert result.applied == ["A"]


def test_two_coupons_stack():
    result = settle(5000, [_coupon("A", amount=1000), _coupon("B", amount=500)], today=1)
    assert result.final_amount == 3500
    assert result.applied == ["A", "B"]


def test_a_third_coupon_is_rejected():
    with pytest.raises(CouponRejected):
        settle(
            5000,
            [_coupon("A"), _coupon("B"), _coupon("C")],
            today=1,
        )


def test_expired_coupon_is_rejected():
    with pytest.raises(CouponExpired):
        settle(5000, [_coupon("A", valid_until_day=3)], today=9)


def test_below_minimum_spend_is_rejected():
    with pytest.raises(CouponRejected):
        settle(1000, [_coupon("A", min_spend=3000)], today=1)


def test_the_same_code_twice_is_rejected():
    with pytest.raises(DuplicateCoupon):
        settle(5000, [_coupon("A"), _coupon("A")], today=1)


def test_the_payable_amount_never_goes_negative():
    result = settle(800, [_coupon("A", amount=1000)], today=1)
    assert result.final_amount == 0


def test_no_coupons_leaves_the_total_alone():
    result = settle(5000, [], today=1)
    assert result.final_amount == 5000
    assert result.applied == []
