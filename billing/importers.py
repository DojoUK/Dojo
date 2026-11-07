"""CSV imports for invoices and expenses (see dojo.csv_import)."""
from datetime import datetime, time
from datetime import timezone as dt_timezone

from decimal import Decimal

from dojo.csv_import import Importer, MemberFinder, RowError, name_key, parse_date, parse_money, parse_optional_date

from .models import BillingPolicy, Expense, Invoice, Payment

STATUSES = {'unpaid': Invoice.Status.UNPAID, 'paid': Invoice.Status.PAID, 'overdue': Invoice.Status.UNPAID}
METHODS = {value: value for value in Payment.Method.values} | {
    label.lower(): value for value, label in Payment.Method.choices
} | {
    'bank transfer': Payment.Method.BACS, 'bank': Payment.Method.BACS, 'transfer': Payment.Method.BACS,
    'card': Payment.Method.STRIPE, 'online': Payment.Method.STRIPE,
}


class InvoiceImporter(Importer):
    key = 'invoices'
    title = 'Invoices'
    intro = ('Adds past invoices, and the payment against each if it was paid. An invoice for a member and '
             'period that already exists is skipped, so importing the same file twice is safe. Imported '
             'invoices aren\'t emailed, collected automatically or reduced by overpayment credit, and an '
             'overpayment on one isn\'t carried onto later invoices.')
    columns = (
        ('member_name', True, 'Must match a member\'s name. "Surname Firstname" is tried too.'),
        ('member_email', False, 'Only needed to tell apart two members with the same name.'),
        ('amount', True, 'What the invoice is for, e.g. 45.00.'),
        ('period', True, 'e.g. "January 2026" or "Autumn Term 2026".'),
        ('due_date', True, 'e.g. 2026-01-31 or 31/01/2026.'),
        ('billing_policy', False, 'Matched by billing policy name.'),
        ('discount_amount', False, 'Already taken off the amount; recorded for reference.'),
        ('status', False, 'unpaid or paid. Paid automatically if paid_date is given and covers the amount.'),
        ('notes', False, ''),
        ('paid_amount', False, 'How much was paid. Defaults to the amount when paid_date is given.'),
        ('paid_date', False, 'When it was paid; records a payment.'),
        ('payment_method', False, 'bank transfer, cash, card or manual (the default).'),
    )
    example = ('member_name,amount,period,due_date,paid_date,payment_method\n'
               'Alice Able,30.00,January 2026,31/01/2026,28/01/2026,bank transfer\n'
               'Bob Best,30.00,January 2026,31/01/2026,,')
    capability = 'manage_billing'
    feature = 'billing'
    done_url = 'invoice_list'

    def prepare(self):
        self.members = MemberFinder(self.org)
        self.policies = {name_key(p.name): p for p in BillingPolicy.objects.filter(organisation=self.org)}
        self.existing = set(Invoice.objects.filter(organisation=self.org).values_list('member_id', 'period'))
        self.seen = set()

    def plan_row(self, row):
        member = self.members.find(row.get('member_name', ''), row.get('member_email', ''))
        amount = parse_money(row.get('amount'), allow_zero=True)
        period = ' '.join(row.get('period', '').split())[:50]
        if not period:
            raise RowError('missing period')
        due_date = parse_date(row.get('due_date'), 'due_date')
        discount = parse_money(row.get('discount_amount'), 'discount_amount', allow_zero=True) if row.get('discount_amount') else 0
        policy = None
        if row.get('billing_policy'):
            policy = self.policies.get(name_key(row['billing_policy']))
            if policy is None:
                raise RowError(f'no billing policy called "{row["billing_policy"]}"')
        status_raw = row.get('status', '').lower()
        if status_raw and status_raw not in STATUSES:
            raise RowError(f'status "{row["status"]}" should be unpaid or paid')
        paid_date = parse_optional_date(row.get('paid_date'), 'paid_date')
        paid_amount = None
        if row.get('paid_amount') and not paid_date:
            raise RowError('paid_amount needs a paid_date')
        if paid_date:
            paid_amount = parse_money(row.get('paid_amount'), 'paid_amount') if row.get('paid_amount') else amount
        method_raw = row.get('payment_method', '').lower()
        method = METHODS.get(method_raw, Payment.Method.MANUAL) if method_raw else Payment.Method.MANUAL
        if method_raw and method_raw not in METHODS:
            raise RowError(f'payment_method "{row["payment_method"]}" isn\'t one Dojo knows')

        key = (member.pk, period)
        if key in self.existing:
            return 'skip', f'{member.name} already has an invoice for {period}', None
        if key in self.seen:
            raise RowError(f'{member.name} has two invoices for {period} in the file')
        self.seen.add(key)
        status = STATUSES.get(status_raw, Invoice.Status.UNPAID)
        if paid_amount is not None and paid_amount >= amount:
            status = Invoice.Status.PAID
        summary = f'{member.name}: £{amount:.2f} for {period}' + (f', paid £{paid_amount:.2f}' if paid_amount else '')
        return 'create', summary, {
            'invoice': dict(member=member, amount=amount, period=period, due_date=due_date,
                            discount_amount=discount, billing_policy=policy, status=status,
                            notes=row.get('notes', '')),
            'payment': (paid_amount, paid_date, method) if paid_amount else None,
        }

    def save(self, planned):
        invoices = payments = 0
        for _, _, data in planned:
            invoice = Invoice(organisation=self.org, **data['invoice'])
            invoice.skip_autopay = True  # history, not a new bill to collect (see billing.signals)
            if data['payment']:
                # Any overpayment was settled back then: don't offer it again as credit (billing.credit).
                invoice.overpayment_credited = max(data['payment'][0] - invoice.amount, Decimal(0))
            invoice.save()
            invoices += 1
            if data['payment']:
                amount, paid_on, method = data['payment']
                Payment.objects.create(
                    invoice=invoice, amount=amount, method=method, notes='Imported',
                    paid_at=datetime.combine(paid_on, time(12), tzinfo=dt_timezone.utc),
                )
                payments += 1
        return (f'{invoices} invoice{"s" if invoices != 1 else ""} and '
                f'{payments} payment{"s" if payments != 1 else ""} imported.')


class ExpenseImporter(Importer):
    key = 'expenses'
    title = 'Expenses'
    intro = ('Adds club expenses. A row with the same date, description and amount as an expense already in '
             'Dojo is skipped, so importing the same file twice is safe.')
    columns = (
        ('description', True, 'e.g. "Hall rent".'),
        ('amount', True, 'e.g. 350.00.'),
        ('expense_date', True, 'e.g. 2026-03-01 or 01/03/2026.'),
        ('category', False, 'rent, utilities, equipment, insurance, salaries, licensing, marketing, '
                            'maintenance or other (or the full category name).'),
        ('notes', False, ''),
    )
    example = 'description,amount,expense_date,category\nHall rent,350.00,01/03/2026,rent\nNew mats,1200,14/03/2026,equipment'
    capability = 'manage_billing'
    feature = 'billing'
    done_url = 'expense_list'

    CATEGORIES = {value: value for value in Expense.Category.values} | {
        label.lower(): value for value, label in Expense.Category.choices
    }

    def prepare(self):
        self.existing = set(
            Expense.objects.filter(organisation=self.org).values_list('expense_date', 'description', 'amount')
        )

    def plan_row(self, row):
        description = ' '.join(row.get('description', '').split())[:255]
        if not description:
            raise RowError('missing description')
        amount = parse_money(row.get('amount'))
        day = parse_date(row.get('expense_date'), 'expense_date')
        category_raw = row.get('category', '').lower()
        if category_raw and category_raw not in self.CATEGORIES:
            raise RowError(f'category "{row["category"]}" isn\'t one of the expense categories')
        category = self.CATEGORIES.get(category_raw, Expense.Category.OTHER)
        key = (day, description, amount)
        if key in self.existing:
            return 'skip', f'{description} on {day:%d %b %Y} is already in Dojo', None
        self.existing.add(key)
        return 'create', f'{description}: £{amount:.2f} on {day:%d %b %Y}', dict(
            description=description, amount=amount, expense_date=day, category=category, notes=row.get('notes', ''),
        )

    def save(self, planned):
        for _, _, fields in planned:
            Expense.objects.create(organisation=self.org, created_by=self.user, **fields)
        return f'{len(planned)} expense{"s" if len(planned) != 1 else ""} imported.'
