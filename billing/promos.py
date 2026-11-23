"""
Promo codes and intro offers: a club's discount (PolicyDiscount) with a code people type on the
join form or their portal's Plans page. Each can be limited to new members, end on a date, have a
maximum number of uses, and last a set number of months once applied. Codes marked public are
shown as the club's intro offer on Find clubs and its join page.
"""
import calendar
from datetime import date

from django.db.models import Q

from .models import MemberDiscount, PolicyDiscount


def normalise(code):
    return ''.join((code or '').split()).upper()[:30]


def find(org, code):
    code = normalise(code)
    if not code:
        return None
    return (PolicyDiscount.objects.filter(policy__organisation=org, code=code, policy__is_active=True)
            .select_related('policy').first())


def _is_new(member):
    return not member.invoices.exclude(status='cancelled').exists()


def problem(discount, member=None, today=None):
    """Why the code can't be used now (by `member`, if given), or '' if it can."""
    today = today or date.today()
    if discount.valid_until and discount.valid_until < today:
        return f'The code {discount.code} has expired.'
    if discount.max_uses is not None and discount.uses >= discount.max_uses:
        return f'The code {discount.code} has been used as many times as it can be.'
    if member is not None:
        if MemberDiscount.objects.filter(member=member, discount=discount).exists():
            return f'{member.name} already has {discount.name}.'
        if discount.new_members_only and not _is_new(member):
            return f'The code {discount.code} is for new members only.'
    return ''


def _add_months(day, months):
    month = day.month - 1 + months
    year, month = day.year + month // 12, month % 12 + 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def redeem(member, discount, today=None):
    """Give the member the discount. Returns the MemberDiscount (raises ValueError with the reason if it can't)."""
    reason = problem(discount, member, today)
    if reason:
        raise ValueError(reason)
    today = today or date.today()
    expires = _add_months(today, discount.duration_months) if discount.duration_months else None
    return MemberDiscount.objects.create(member=member, discount=discount, expires_on=expires)


def offers_by_club(org_ids, today=None):
    """{org id: first intro offer} for the finder, in one query."""
    today = today or date.today()
    offers = (PolicyDiscount.objects.filter(policy__organisation_id__in=org_ids, policy__is_active=True, show_publicly=True)
              .exclude(code='').filter(Q(valid_until__isnull=True) | Q(valid_until__gte=today))
              .select_related('policy__organisation').order_by('pk'))
    found = {}
    for offer in offers:
        if offer.max_uses is not None and offer.uses >= offer.max_uses:
            continue
        if not offer.policy.organisation.has_feature('billing'):
            continue
        found.setdefault(offer.policy.organisation_id, offer)
    return found


def public_offers(org, today=None):
    """The club's intro offers: public codes that can still be used, on active plans."""
    today = today or date.today()
    if not org.has_feature('billing'):
        return []
    offers = (PolicyDiscount.objects.filter(policy__organisation=org, policy__is_active=True, show_publicly=True)
              .exclude(code='').filter(Q(valid_until__isnull=True) | Q(valid_until__gte=today))
              .select_related('policy'))
    return [o for o in offers if o.max_uses is None or o.uses < o.max_uses]
