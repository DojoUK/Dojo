"""
The ready-made pieces email wording can drop in with {block_name}: each has an
HTML version (for the email) and a text version (for its plain-text copy).
"""
from django.template.loader import render_to_string


def _accent(org):
    from organisations.branding import email_branding
    return email_branding(org)['brand_accent']


def portal_button(org, portal_link):
    return {
        'html': render_to_string('emails/blocks/portal_button.html', {'portal_link': portal_link, 'accent': _accent(org)}),
        'text': f'Go to member portal: {portal_link}',
    }


def reset_button(org, reset_link):
    return {
        'html': render_to_string('emails/blocks/link_button.html', {'url': reset_link, 'text': 'Choose a new password',
                                                                    'accent': _accent(org)}),
        'text': f'Choose a new password: {reset_link}',
    }


def session_box(class_name, session_date):
    return {
        'html': render_to_string('emails/blocks/session_box.html', {'class_name': class_name, 'session_date': session_date}),
        'text': f'{class_name}, {session_date}',
    }


def message(text):
    return {'html': render_to_string('emails/blocks/message.html', {'message': text}), 'text': text}


def invoice_values(invoice):
    from billing.emails import portal_url_for
    from billing.payments import pay_url
    return {
        'period': invoice.period,
        # What's left to pay; for a paid invoice, its total (the summary then says Paid).
        'amount': f'£{invoice.balance_due if invoice.balance_due else invoice.amount:.2f}',
        'due_date': invoice.due_date.strftime('%-d %b %Y'),
        'reference': invoice.payment_reference,
        'pay_link': pay_url(invoice),
        'portal_link': portal_url_for(invoice.member) or '',
    }


def invoice_blocks(invoice, values, overdue=False, autopay_failed='', autopay_label=''):
    from billing.payments import options_for
    from organisations.branding import email_branding
    summary = {'member_name': invoice.member.name, 'overdue': overdue and bool(invoice.balance_due),
               'paid': not invoice.balance_due, 'notes': invoice.notes, **values}
    options = {**email_branding(invoice.organisation), **options_for(invoice), 'invoice': invoice,
               'autopay_failed': autopay_failed, 'autopay_label': autopay_label,
               'portal_url': values.get('portal_link'), 'button_colour': '#dc2626' if overdue else None}
    if autopay_failed:
        pay_text = f"We couldn't take this payment automatically: {autopay_failed}\nHow to pay: {values['pay_link']}"
    elif autopay_label and invoice.balance_due:
        pay_text = f'Nothing to do: this will be collected automatically from your {autopay_label}.'
    elif not invoice.balance_due:
        pay_text = 'Paid, thank you.'
    else:
        pay_text = f"How to pay: {values['pay_link']}"
        if options['bank_transfer']:
            bank = options['bank_transfer']
            pay_text += (f"\nBank transfer: {bank['account_name']}, sort code {bank['sort_code']}, "
                         f"account {bank['account_number']}, reference {values['reference']}")
    return {
        'invoice_summary': {
            'html': render_to_string('emails/blocks/invoice_summary.html', summary),
            'text': (f"Member: {invoice.member.name}\nPeriod: {values['period']}\n"
                     f"Due: {values['due_date']}{' (overdue)' if overdue else ''}\n"
                     f"Reference: {values['reference']}\n"
                     f"{'Paid' if not invoice.balance_due else 'Amount due'}: {values['amount']}"),
        },
        'pay_options': {'html': render_to_string('emails/blocks/pay_options.html', options), 'text': pay_text},
    }


class _ExampleInvoice:
    """Just enough of an invoice for the editor's preview of the pay options."""
    balance_due = 45
    autopay_status = ''


def example(org, key):
    """(values, blocks) with made-up data, for previewing an email in the editor."""
    from organisations.branding import email_branding

    from .catalogue import BY_KEY, EXAMPLE_COMMON
    email_type = BY_KEY[key]
    values = {**(EXAMPLE_COMMON if email_type.to_members else {}), **email_type.example}
    blocks = {}
    names = {name for name, _ in email_type.blocks}
    if 'portal_button' in names:
        blocks['portal_button'] = portal_button(org, values['portal_link'])
    if 'reset_button' in names:
        blocks['reset_button'] = reset_button(org, values['reset_link'])
    if 'session_box' in names:
        blocks['session_box'] = session_box(values['class_name'], values['session_date'])
    if 'message' in names:
        blocks['message'] = message(values['message'])
    if 'invoice_summary' in names:
        summary = {'member_name': values['member_name'], 'overdue': key == 'invoice_reminder', **values}
        blocks['invoice_summary'] = {'html': render_to_string('emails/blocks/invoice_summary.html', summary), 'text': ''}
        options = {**email_branding(org), 'invoice': _ExampleInvoice(), 'pay_url': values['pay_link'],
                   'payment_reference': values['reference'], 'card_payments': True,
                   'bank_transfer': {'account_name': org.name, 'sort_code': '12-34-56', 'account_number': '12345678'},
                   'cash_payments': False, 'payment_instructions': '', 'portal_url': values['portal_link'],
                   'button_colour': '#dc2626' if key == 'invoice_reminder' else None}
        blocks['pay_options'] = {'html': render_to_string('emails/blocks/pay_options.html', options), 'text': ''}
    return values, blocks
