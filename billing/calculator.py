"""
Invoice amount calculator.

Given a member and a billing context (policy + period), returns the gross amount,
total discount, net amount, and a human-readable breakdown.
"""
from decimal import Decimal


def calculate_invoice_amount(member, policy, term=None, year=None, month=None):
    """
    Returns a dict:
      gross         - amount before discounts
      discount      - total discount amount
      net           - amount to charge (gross - discount)
      breakdown     - list of dicts describing each line
      discount_lines - list of dicts describing each discount applied
    """
    from classes.models import ClassMember, Session

    breakdown = []
    discount_lines = []

    # ── 1. Calculate gross ────────────────────────────────────────────────────
    if policy.pricing_model == policy.PricingModel.FLAT:
        gross = Decimal(str(policy.amount or 0))
        breakdown.append({
            'label': 'Flat rate',
            'amount': gross,
        })

    else:
        # Per-session: find enrolled classes that use this policy
        # A class counts if: member's own policy matches OR (member has no policy AND class policy matches)
        if member.billing_policy_id and member.billing_policy_id == policy.pk:
            enrolments = ClassMember.objects.filter(member=member).select_related('assigned_class')
        else:
            enrolments = ClassMember.objects.filter(
                member=member,
                assigned_class__billing_policy=policy,
            ).select_related('assigned_class')

        rate = Decimal(str(policy.per_session_rate or 0))
        discount_pct = Decimal(str(policy.additional_class_discount or 0))

        gross = Decimal('0')
        for i, enrolment in enumerate(enrolments):
            cls = enrolment.assigned_class

            # Count sessions in the billing period
            session_qs = Session.objects.filter(assigned_class=cls, is_cancelled=False)
            if term:
                session_qs = session_qs.filter(date__gte=term.start_date, date__lte=term.end_date)
            elif year and month:
                session_qs = session_qs.filter(date__year=year, date__month=month)
            count = session_qs.count()

            if count == 0:
                # Fall back to schedule estimate if no sessions created yet
                count = _estimate_sessions(cls, term, year, month)

            if i == 0:
                session_rate = rate
                label = f"{cls.name} ({count} sessions × £{rate:.2f})"
            else:
                session_rate = rate * (1 - discount_pct / 100)
                label = f"{cls.name} ({count} sessions × £{session_rate:.2f} — {discount_pct:.0f}% multi-class discount)"

            line_total = session_rate * count
            gross += line_total
            breakdown.append({'label': label, 'amount': line_total})

    # ── 2. Apply member-specific discounts ────────────────────────────────────
    from billing.models import MemberDiscount
    for md in MemberDiscount.objects.filter(
        member=member, is_active=True, discount__policy=policy
    ).select_related('discount'):
        amount_off = md.discount.amount_off(gross)
        discount_lines.append({'label': md.discount.name, 'amount': amount_off})

    # ── 3. Apply family group discount ────────────────────────────────────────
    family_membership = member.family_memberships.select_related('family_group').first()
    if family_membership:
        fg = family_membership.family_group
        if fg.discount_percentage:
            family_off = round(gross * fg.discount_percentage / 100, 2)
            discount_lines.append({
                'label': f"Family discount ({fg.name} — {fg.discount_percentage:.0f}% off)",
                'amount': family_off,
            })

    total_discount = sum(d['amount'] for d in discount_lines)
    net = max(Decimal('0'), gross - total_discount)

    return {
        'gross': gross,
        'discount': total_discount,
        'net': net,
        'breakdown': breakdown,
        'discount_lines': discount_lines,
    }


def _estimate_sessions(cls, term, year, month):
    """Estimate session count from class schedule when actual sessions don't exist."""
    import math
    from datetime import date, timedelta

    schedule = cls.schedule or []
    if not schedule:
        return 0

    sessions_per_week = len(schedule)

    if term:
        delta = (term.end_date - term.start_date).days
        weeks = delta / 7
    elif year and month:
        import calendar
        _, days_in_month = calendar.monthrange(year, month)
        weeks = days_in_month / 7
    else:
        weeks = 4  # default fallback

    return max(1, round(sessions_per_week * weeks))
