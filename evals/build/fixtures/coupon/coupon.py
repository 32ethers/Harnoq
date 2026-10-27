"""Coupon settlement for a single order.

Applies a customer's coupons to an order total and reports what the customer
actually pays, plus which coupons were consumed.
"""
from dataclasses import dataclass, field


class CouponError(Exception):
    """Base class for every rejection this module reports."""


class CouponExpired(CouponError):
    """The coupon's validity window has already closed."""


class ThresholdNotMet(CouponError):
    """The order total is below the coupon's minimum spend."""


class DuplicateCoupon(CouponError):
    """The same coupon code was presented twice on one order."""


class CouponRejected(CouponError):
    """The coupon cannot be applied to this order."""


@dataclass
class Coupon:
    """One coupon a customer is trying to use.

    Attributes:
        code: the coupon's unique code.
        amount: how much it takes off, in cents.
        min_spend: the order total required before it may be used, in cents.
        valid_until_day: last day the coupon may be used, as a day number.
    """

    code: str
    amount: int
    min_spend: int
    valid_until_day: int


@dataclass
class Settlement:
    """What the customer pays after coupons are applied.

    Attributes:
        final_amount: the amount payable, in cents.
        applied: codes of the coupons that were consumed, in order.
    """

    final_amount: int
    applied: list[str] = field(default_factory=list)


#: How many coupons one order may stack.
MAX_STACKED = 2


def settle(order_total: int, coupons: list[Coupon], today: int) -> Settlement:
    """Apply coupons to an order total.

    Args:
        order_total: the order total before coupons, in cents.
        coupons: the coupons the customer presented, in the order given.
        today: the current day number, compared against each coupon's window.

    Returns:
        Settlement: the payable amount and the codes consumed.

    Raises:
        CouponError: if any presented coupon cannot be applied.
    """
    seen: set[str] = set()
    applied: list[str] = []
    remaining = order_total

    for coupon in coupons:
        if coupon.code in seen:
            raise DuplicateCoupon(f"coupon {coupon.code} presented twice")
        seen.add(coupon.code)

        if len(applied) > MAX_STACKED:
            raise CouponRejected(
                f"at most {MAX_STACKED} coupons may be stacked on one order"
            )
        if today > coupon.valid_until_day:
            raise CouponExpired(f"coupon {coupon.code} expired on day {coupon.valid_until_day}")
        if order_total < coupon.min_spend:
            raise CouponRejected(
                f"order total {order_total} is below the {coupon.min_spend} minimum "
                f"for coupon {coupon.code}"
            )

        remaining = remaining - coupon.amount
        applied.append(coupon.code)

    return Settlement(final_amount=remaining, applied=applied)
