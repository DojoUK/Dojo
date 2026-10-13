"""
Every email Dojo sends, with the fields and blocks its wording can use and the
default wording. Clubs can replace any of them in Settings → Email → Customise
emails (see mailer.compose for how wording becomes an email).

Wording is plain text with a little formatting:
  **bold**          [link text](https://...)      [button: Pay now]({pay_link})
  blank line = new paragraph; a block like {pay_options} on its own line inserts it.
"""
from dataclasses import dataclass, field

COMMON_FIELDS = (
    ('greeting', '"Hi Ann" or "Dear guardian of Ann Lee"'),
    ('first_name', "The member's first name"),
    ('member_name', "The member's full name"),
    ('club_name', 'Your club\'s name'),
    ('club_email', 'Your club\'s contact email'),
    ('club_website', 'Your club\'s website'),
)


@dataclass(frozen=True)
class EmailType:
    key: str
    label: str
    when: str
    default_subject: str
    default_body: str
    fields: tuple = ()
    blocks: tuple = ()
    example: dict = field(default_factory=dict)

    @property
    def all_fields(self):
        return COMMON_FIELDS + self.fields


EXAMPLE_COMMON = {
    'greeting': 'Hi Ann', 'first_name': 'Ann', 'member_name': 'Ann Lee',
}
EXAMPLE_INVOICE = {
    'period': 'Autumn Term 2026', 'amount': '£45.00', 'due_date': '30 Sep 2026', 'reference': 'INV1234',
    'pay_link': 'https://example.com/pay/…', 'portal_link': 'https://example.com/p/…',
}

INVOICE_FIELDS = (
    ('period', 'What the invoice is for, e.g. "Autumn Term 2026"'),
    ('amount', 'Amount still to pay, e.g. "£45.00"'),
    ('due_date', 'Due date, e.g. "30 Sep 2026"'),
    ('reference', 'Reference to quote on a bank transfer, e.g. INV1234'),
    ('pay_link', 'Link to the invoice\'s payment page'),
    ('portal_link', 'Link to the member portal'),
)
INVOICE_BLOCKS = (
    ('invoice_summary', 'Box with the member, period, due date and amount'),
    ('pay_options', 'Pay button, bank details and other ways to pay (or "nothing to do" for automatic payments)'),
)

EMAIL_TYPES = (
    EmailType(
        'welcome', 'Welcome', 'When a member is added and sent their portal link.',
        'Welcome to {club_name}',
        """{greeting},

Welcome to **{club_name}**! We're glad to have you with us.

You can view membership details, check invoices and pay online using your personal member portal:

{portal_button}

If you have any questions, please don't hesitate to get in touch.

See you on the mat,
**{club_name}**""",
        fields=(('portal_link', 'Link to the member portal'),),
        blocks=(('portal_button', 'A "Go to member portal" button with the link underneath'),),
        example={'portal_link': 'https://example.com/p/…'},
    ),
    EmailType(
        'portal_link_refreshed', 'New portal link', 'When a member\'s portal link is replaced (routinely, or by an admin).',
        'Your {club_name} member portal link has been refreshed',
        """{greeting},

As a routine security measure, we periodically refresh member portal links. Your old link no longer works, so please use this new one to see your membership details, invoices and more.

{portal_button}

If you didn't expect this email, or have any questions, please get in touch.

Thanks,
**{club_name}**""",
        fields=(('portal_link', 'Link to the member portal'),),
        blocks=(('portal_button', 'A "Go to member portal" button with the link underneath'),),
        example={'portal_link': 'https://example.com/p/…'},
    ),
    EmailType(
        'invoice', 'Invoice', 'When an invoice is sent to a member.',
        'Invoice from {club_name}: {period}',
        """{greeting},

Here's your invoice from {club_name} for **{period}**.

{invoice_summary}

{pay_options}

Thanks,
**{club_name}**""",
        fields=INVOICE_FIELDS, blocks=INVOICE_BLOCKS, example=EXAMPLE_INVOICE,
    ),
    EmailType(
        'invoice_reminder', 'Payment reminder', 'When an overdue invoice is chased, by an admin or automatically.',
        'Payment reminder: {period} ({club_name})',
        """{greeting},

This is a friendly reminder that the following invoice from **{club_name}** is now overdue.

{invoice_summary}

{pay_options}

If you've already paid, please ignore this message: it may have crossed with your payment. Any questions, just reply.

Thanks,
**{club_name}**""",
        fields=INVOICE_FIELDS, blocks=INVOICE_BLOCKS, example=EXAMPLE_INVOICE,
    ),
    EmailType(
        'announcement', 'Announcement', 'Announcements sent to the whole club or a class. The message itself is written each time.',
        '{title}',
        """{greeting},

{message}

**{club_name}**""",
        fields=(('title', 'The announcement\'s title'),),
        blocks=(('message', 'The announcement\'s message, as written'),),
        example={'title': 'Christmas training times', 'message': 'Training finishes on 18 December and starts again on 6 January. Have a great break!'},
    ),
    EmailType(
        'session_cancelled', 'Session cancelled', 'When a class session is cancelled.',
        '{class_name} on {session_date} is cancelled',
        """{greeting},

We wanted to let you know that the following session has been **cancelled**:

{session_box}

We'll be in touch if anything changes. Sorry for any inconvenience.

**{club_name}**""",
        fields=(('class_name', 'The class'), ('session_date', 'The session\'s date, e.g. "Tuesday 3 November 2026"')),
        blocks=(('session_box', 'Highlighted box with the class and date'),),
        example={'class_name': 'Juniors', 'session_date': 'Tuesday 3 November 2026'},
    ),
    EmailType(
        'waiting_list_place', 'Waiting list place', 'When a member is moved off a class waiting list.',
        'A place has opened up: {class_name} ({club_name})',
        """{greeting},

Great news! A place has opened up in **{class_name}** at {club_name}, and {first_name} has been moved off the waiting list and into the class.

No action is needed: you're all set.

Thanks,
**{club_name}**""",
        fields=(('class_name', 'The class'),),
        example={'class_name': 'Juniors'},
    ),
)

BY_KEY = {email_type.key: email_type for email_type in EMAIL_TYPES}
