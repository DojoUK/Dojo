"""
Invoice amount calculator.

Given a member and a billing context (policy + period), returns the gross amount,
total discount, net amount, and a human-readable breakdown.
"""
from datetime import date
from decimal import Decimal


def family_plan_cover(member, policy):
    """
    For a family plan shared by two or more linked members: (lead, covered), where `covered` is every
    active family member billed on the plan and `lead` the one who gets the single family invoice.
    None when the policy isn't a family plan or only one family member is on it (billed as normal).
    """
    if not policy.is_family_plan:
        return None
    from members.family import family_members

    from .schedule import members_for_policy
    on_plan = {m.pk for m in members_for_policy(member.organisation, policy)}
    covered = [m for m in family_members(member) if m.pk in on_plan]
    if len(covered) < 2:
        return None
    return min(covered, key=lambda m: m.pk), covered


def _enrolments(member, policy):
    """The classes billed on `policy` for a member: all of theirs if it's their own plan, else the class's."""
    from classes.models import ClassMember
    # A class counts if: member's own policy matches OR (member has no policy AND class policy matches)
    if member.billing_policy_id and member.billing_policy_id == policy.pk:
        return ClassMember.objects.filter(member=member).select_related('assigned_class')
    return ClassMember.objects.filter(member=member, assigned_class__billing_policy=policy).select_related('assigned_class')


def calculate_invoice_amount(member, policy, term=None, year=None, month=None):
    """
    Returns a dict:
      gross         - amount before discounts
      discount      - total discount amount
      net           - amount to charge (gross - discount)
      breakdown     - list of dicts describing each line
      discount_lines - list of dicts describing each discount applied
      covers        - names of the family members a family-plan invoice covers (else [])

    A family plan (BillingPolicy.is_family_plan) bills a linked family once: the "lead" member's
    invoice carries the flat rate (once) or every child's sessions, and the other children get nothing.
    """
    from classes.models import Session

    breakdown = []
    discount_lines = []

    cover = family_plan_cover(member, policy)
    if cover is not None and cover[0].pk != member.pk:
        return {
            'gross': Decimal('0'), 'discount': Decimal('0'), 'net': Decimal('0'), 'covers': [],
            'breakdown': [{'label': f'Covered by the family invoice for {cover[0].name}', 'amount': Decimal('0')}],
            'discount_lines': [],
        }
    people = cover[1] if cover else [member]
    names = [m.name for m in people]

    # ── 1. Calculate gross ────────────────────────────────────────────────────
    if policy.pricing_model == policy.PricingModel.FLAT:
        gross = Decimal(str(policy.amount or 0))
        breakdown.append({
            'label': f"Family flat rate ({', '.join(names)})" if cover else 'Flat rate',
            'amount': gross,
        })

    else:
        # Per-session: find the classes that use this policy, for each person the plan covers.
        enrolments = [(person, enrolment) for person in people for enrolment in _enrolments(person, policy)]

        rate = Decimal(str(policy.per_session_rate or 0))
        discount_pct = Decimal(str(policy.additional_class_discount or 0))

        gross = Decimal('0')
        seen_per_person = {}
        for person, enrolment in enrolments:
            i = seen_per_person.get(person.pk, 0)
            seen_per_person[person.pk] = i + 1
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
            breakdown.append({'label': f'{person.name}: {label}' if cover else label, 'amount': line_total})

    # ── 2. Apply member-specific discounts ────────────────────────────────────
    from billing.models import MemberDiscount
    from django.db.models import Q
    # Discounts that last a set time stop once the period being billed starts after they end.
    if term:
        period_start = term.start_date
    elif year and month:
        period_start = date(year, month, 1)
    else:
        period_start = date.today()
    for md in MemberDiscount.objects.filter(
        member__in=people, is_active=True, discount__policy=policy
    ).filter(Q(expires_on__isnull=True) | Q(expires_on__gte=period_start)).select_related('discount'):
        amount_off = md.discount.amount_off(gross)
        discount_lines.append({'label': md.discount.name, 'amount': amount_off})

    # ── 2b. Auto-apply discounts: everyone billed on the plan gets them, once per invoice, unless
    # they already have it as a member discount above, or staff have exempted them (an inactive
    # MemberDiscount). One that lasts a set time runs from when the member joined the club.
    from billing.models import PolicyDiscount
    from billing.promos import _add_months
    has_row = set(MemberDiscount.objects.filter(member=member, discount__policy=policy).values_list('discount_id', flat=True))
    for discount in PolicyDiscount.objects.filter(policy=policy, auto_apply=True).exclude(pk__in=has_row):
        if discount.duration_months and member.joined_date \
                and _add_months(member.joined_date, discount.duration_months) < period_start:
            continue
        discount_lines.append({'label': discount.name, 'amount': discount.amount_off(gross)})

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
        'covers': names if cover else [],
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
