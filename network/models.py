from django.contrib.auth.models import User
from django.db import models

from members.names import NameParts
from organisations.models import Organisation


class MemberAccount(NameParts, models.Model):
    """
    A member's own Dojo account, used across every club on this installation (the Dojo network).

    It signs in with an email and password (a Django User whose username is the email). Club
    membership records (members.Member) and applications link to it; existing memberships are
    linked by email once the address is verified (see accounts.link_memberships).
    """
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='member_account')
    name = models.CharField(max_length=255)   # first name and surname (members/names.py)
    first_name = models.CharField(max_length=150, blank=True)
    surname = models.CharField(max_length=150, blank=True)
    preferred_name = models.CharField(max_length=150, blank=True, help_text='What they like to be called, if not their first name')
    pronouns = models.CharField(max_length=30, blank=True, help_text='e.g. she/her, he/him, they/them')
    email_verified_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    # My details: kept once here and used to fill in each club's application (only the fields that
    # club collects), and to update the member's own records at their clubs. See network/details.py.
    date_of_birth = models.DateField(null=True, blank=True)
    phone = models.CharField(max_length=20, blank=True)
    address_line1 = models.CharField(max_length=255, blank=True)
    address_line2 = models.CharField(max_length=255, blank=True)
    city = models.CharField('town or city', max_length=100, blank=True)
    county = models.CharField(max_length=100, blank=True)
    postcode = models.CharField(max_length=20, blank=True)
    emergency_contact_name = models.CharField(max_length=255, blank=True)
    emergency_contact_phone = models.CharField(max_length=20, blank=True)
    emergency_contact_2_name = models.CharField('second emergency contact name', max_length=255, blank=True)
    emergency_contact_2_phone = models.CharField('second emergency contact phone', max_length=20, blank=True)
    medical_info = models.TextField(blank=True)
    guardian_name = models.CharField('parent or guardian name', max_length=255, blank=True)
    guardian_email = models.EmailField('parent or guardian email', blank=True)
    guardian_phone = models.CharField('parent or guardian phone', max_length=20, blank=True)

    @property
    def email(self):
        return self.user.email

    @property
    def is_verified(self):
        return self.email_verified_at is not None

    def __str__(self):
        return f'{self.name} <{self.email}>'


class FavouriteClub(models.Model):
    """A club a member has saved from the club finder, to come back to or join."""
    account = models.ForeignKey(MemberAccount, on_delete=models.CASCADE, related_name='favourites')
    organisation = models.ForeignKey(Organisation, on_delete=models.CASCADE, related_name='favourited_by')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['account', 'organisation'], name='one_favourite_per_club')]
        ordering = ['organisation__name']


class ClubReview(models.Model):
    """
    A member's 1–5 star review of a club, with what they want to say. One per account per club,
    and only from accounts with a membership there (network/reviews.py). The member can edit it
    any time, e.g. once the club has sorted out something they raised.
    """
    account = models.ForeignKey(MemberAccount, on_delete=models.CASCADE, related_name='reviews')
    organisation = models.ForeignKey(Organisation, on_delete=models.CASCADE, related_name='reviews')
    rating = models.PositiveSmallIntegerField()
    text = models.TextField(blank=True, max_length=2000)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    # The club's public reply, from its staff Reviews page.
    reply = models.TextField(blank=True, max_length=2000)
    replied_at = models.DateTimeField(null=True, blank=True)
    replied_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')

    class Meta:
        ordering = ['-updated_at']
        constraints = [
            models.UniqueConstraint(fields=['account', 'organisation'], name='one_review_per_club'),
            models.CheckConstraint(condition=models.Q(rating__gte=1, rating__lte=5), name='review_rating_1_to_5'),
        ]

    @property
    def edited(self):
        return (self.updated_at - self.created_at).total_seconds() > 60

    @property
    def updated_since_reply(self):
        return bool(self.replied_at) and (self.updated_at - self.replied_at).total_seconds() > 60

    @property
    def reviewer(self):
        """How the reviewer is shown: first name and last initial."""
        parts = (self.account.name or '').split()
        if not parts:
            return 'A member'
        return parts[0] if len(parts) == 1 else f'{parts[0]} {parts[-1][0]}.'

    def __str__(self):
        return f'{self.rating}★ for {self.organisation} by {self.account}'


class PriorProgression(models.Model):
    """
    A grade the member gained before (or outside) the clubs on Dojo, added by them, e.g. when they
    create their account. It's theirs: they can change or remove it, and clubs see it read only.
    Grades a club on Dojo awards are that club's MemberProgression records instead.
    """
    account = models.ForeignKey(MemberAccount, on_delete=models.CASCADE, related_name='prior_progressions')
    discipline = models.CharField(max_length=20)            # organisations/disciplines.py
    grade = models.CharField(max_length=255, help_text='e.g. Blue belt, 2nd Kyu')
    achieved_date = models.DateField(null=True, blank=True)
    awarded_by = models.CharField(max_length=255, blank=True, help_text='The club or association that awarded it')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-achieved_date', '-created_at']

    def __str__(self):
        return f'{self.grade} ({self.discipline}) for {self.account}'


class ReviewPrompt(models.Model):
    """An email asking an account to review a club it trains at (network/reviews.py), sent once."""
    account = models.ForeignKey(MemberAccount, on_delete=models.CASCADE, related_name='review_prompts')
    organisation = models.ForeignKey(Organisation, on_delete=models.CASCADE, related_name='review_prompts')
    sent_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['account', 'organisation'], name='one_review_prompt_per_club')]
