from decimal import Decimal, InvalidOperation

from django import template

register = template.Library()


@register.filter
def pounds(value):
    """£ with two decimals and the minus sign in front: -£340.00, £1,250.50."""
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return ''
    sign = '-' if amount < 0 else ''
    return f'{sign}£{abs(amount):,.2f}'
