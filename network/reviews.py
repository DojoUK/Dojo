"""
Club reviews: members rate a club 1–5 stars and say why. Shown on the club's finder page, as an
average in the finder (which ranks by it), and to the club's staff under Reviews.
"""
from django.db.models import Avg, Count

from .models import ClubReview

MAX_TEXT = 2000


def can_review(account, org):
    """Accounts with a membership at the club, current or past (an anonymised record isn't linked any more)."""
    return bool(account) and account.memberships.filter(organisation=org).exists()


def summary(org):
    """{'average': 4.3 or None, 'count': n, 'stars': [5 star counts..1]} for a club."""
    reviews = ClubReview.objects.filter(organisation=org)
    totals = reviews.aggregate(average=Avg('rating'), count=Count('pk'))
    by_star = dict(reviews.values_list('rating').annotate(n=Count('pk')))
    count = totals['count']
    return {
        'average': round(totals['average'], 1) if count else None,
        'count': count,
        'stars': [{'stars': s, 'count': by_star.get(s, 0),
                   'percent': round(100 * by_star.get(s, 0) / count) if count else 0} for s in (5, 4, 3, 2, 1)],
    }


def ratings(org_ids):
    """{org id: (average, count)} for the clubs that have reviews."""
    rows = (ClubReview.objects.filter(organisation_id__in=org_ids).values('organisation_id')
            .annotate(average=Avg('rating'), count=Count('pk')))
    return {row['organisation_id']: (round(row['average'], 1), row['count']) for row in rows}


def rank(average):
    """What the finder sorts by: the average to the nearest half star, so nearby clubs of much the
    same rating still come nearest first. Unrated clubs sort after every rated one."""
    return -round(average * 2) / 2 if average is not None else 1


def save(account, org, rating, text):
    """Create or update the account's review of the club. Returns (review, errors)."""
    errors = []
    try:
        rating = int(rating)
    except (TypeError, ValueError):
        rating = 0
    if not 1 <= rating <= 5:
        errors.append('Choose from 1 to 5 stars.')
    text = (text or '').strip()
    if len(text) > MAX_TEXT:
        errors.append(f'Keep your review to {MAX_TEXT} characters.')
    if not can_review(account, org):
        errors.append(f'Only members of {org.name} can review it.')
    if errors:
        return None, errors
    review, _ = ClubReview.objects.update_or_create(account=account, organisation=org,
                                                   defaults={'rating': rating, 'text': text})
    return review, []


# ── Asking for reviews ────────────────────────────────────────────────────────

PROMPT_AFTER_SESSIONS = 4


def prompts_on(org):
    """Whether the club asks members for reviews (on unless it switches it off on its Reviews page)."""
    return (org.settings or {}).get('review_prompts', True) is not False


def send_prompts():
    """
    Email each Dojo account a one-off request to review a club, once someone on the account has
    trained there PROMPT_AFTER_SESSIONS times and the account hasn't reviewed it. Only for clubs
    listed in Find clubs that ask for reviews, and verified accounts. Returns how many were sent.
    """
    from django.conf import settings
    from django.db.models import Count, Q
    from django.urls import reverse

    from mailer import blocks, compose
    from members.models import Member
    from organisations.models import Organisation

    from .models import ClubReview, ReviewPrompt

    sent = 0
    for org in Organisation.objects.filter(listed_in_finder=True):
        if not prompts_on(org):
            continue
        reviewed = set(ClubReview.objects.filter(organisation=org).values_list('account_id', flat=True))
        prompted = set(ReviewPrompt.objects.filter(organisation=org).values_list('account_id', flat=True))
        totals = (Member.objects.filter(organisation=org, is_active=True, account__isnull=False,
                                        account__email_verified_at__isnull=False)
                  .values('account_id').annotate(sessions=Count('attendance', filter=Q(attendance__present=True))))
        due = [row['account_id'] for row in totals if row['sessions'] >= PROMPT_AFTER_SESSIONS
               and row['account_id'] not in reviewed and row['account_id'] not in prompted]
        from .models import MemberAccount
        for account in MemberAccount.objects.filter(pk__in=set(due)).select_related('user'):
            link = settings.SITE_URL.rstrip('/') + reverse('network_club', kwargs={'slug': org.slug}) + '#reviews'
            first = account.preferred_name or account.first_name or account.name.split(' ')[0]
            try:
                compose.send(org, 'review_request', [account.email],
                             {'greeting': f'Hi {first}', 'first_name': first, 'member_name': account.name,
                              'review_link': link},
                             {'review_button': blocks.link_button(org, 'Review ' + org.name, link)})
            except Exception:
                continue
            ReviewPrompt.objects.get_or_create(account=account, organisation=org)
            sent += 1
    return sent
