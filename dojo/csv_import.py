"""
CSV imports shown as a two-step page: upload or paste a CSV, check the
preview of what each row will do, then import. Each kind of import is an
Importer subclass; ImportView handles the page, access and the preview.
"""
import csv
import io
from collections import defaultdict
from datetime import datetime
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.db import transaction
from django.http import Http404
from django.shortcuts import redirect, render
from django.views import View

from .mixins import OrgMixin

MAX_CSV_BYTES = 2 * 1024 * 1024
MAX_ROWS = 5000
PREVIEW_ROWS = 100
DATE_FORMATS = ('%Y-%m-%d', '%d/%m/%Y', '%d-%m-%Y', '%d-%b-%Y', '%d %b %Y', '%d/%m/%y', '%d %B %Y')


class RowError(ValueError):
    """A row that can't be imported; the message says why."""


def parse_date(raw, label='date'):
    raw = (raw or '').strip()
    if not raw:
        raise RowError(f'missing {label}')
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    raise RowError(f'{label} "{raw}" isn\'t a date (use e.g. 2026-03-14 or 14/03/2026)')


def parse_optional_date(raw, label='date'):
    return parse_date(raw, label) if (raw or '').strip() else None


YES = {'yes', 'y', 'true', '1', 'present', 'x', '✓'}
NO = {'no', 'n', 'false', '0', 'absent'}


def parse_yes_no(raw, default=None):
    """True or False from yes/no-style text. A blank cell gives `default`, or is an error without one."""
    value = (raw or '').strip().lower()
    if value in YES:
        return True
    if value in NO:
        return False
    if not value and default is not None:
        return default
    raise RowError(f'"{raw}" isn\'t yes or no')


def parse_money(raw, label='amount', allow_zero=False):
    text = (raw or '').strip().replace('£', '').replace(',', '')
    if not text:
        raise RowError(f'missing {label}')
    try:
        value = Decimal(text).quantize(Decimal('0.01'))
    except InvalidOperation:
        raise RowError(f'{label} "{raw}" isn\'t a number') from None
    if not value.is_finite() or value < 0 or (value == 0 and not allow_zero) or value >= Decimal('1000000'):
        raise RowError(f'{label} "{raw}" is out of range')
    return value


def name_key(name):
    return ' '.join((name or '').split()).lower()


class MemberFinder:
    """
    Finds a club's member from a CSV's name (and email, when the file has
    one). Two members with the same name are never guessed between: the row
    is an error unless an email column says which.
    """

    def __init__(self, org):
        from members.models import Member
        self.by_name = defaultdict(list)
        for member in Member.objects.filter(organisation=org).exclude(name=''):
            self.by_name[name_key(member.name)].append(member)

    def find(self, name, email=''):
        candidates = self.by_name.get(name_key(name), [])
        if not candidates:
            parts = name.split()
            if len(parts) == 2:  # "Surname Firstname"
                candidates = self.by_name.get(name_key(f'{parts[1]} {parts[0]}'), [])
        if not candidates:
            raise RowError(f'no member called "{name}"')
        if len(candidates) > 1 and email:
            candidates = [m for m in candidates if (m.email or '').strip().lower() == email.strip().lower()]
            if not candidates:
                raise RowError(f'none of the members called "{name}" has the email {email}')
        if len(candidates) > 1:
            raise RowError(f'{len(candidates)} members are called "{name}": add a member_email column to say which')
        return candidates[0]


class Importer:
    """One kind of CSV import. Subclasses set the class attributes and implement plan_row and save."""
    key = ''
    title = ''
    intro = ''
    columns = ()          # (name, required, help)
    example = ''          # a short example CSV
    capability = None     # None: org admins only; else a staff capability that also grants it
    feature = None        # an optional feature the club must have switched on
    done_url = None       # URL name to return to after importing
    do_not_call_in_templates = True  # templates list the classes themselves

    def __init__(self, org, user):
        self.org = org
        self.user = user

    def required_columns(self):
        return [name for name, required, _ in self.columns if required]

    def prepare(self):
        """Look-ups shared by every row (e.g. members by name). Runs once per preview or import."""

    def plan_row(self, row):
        """
        Check one row (a dict of lower-cased column names). Return
        (action, summary, data): action is 'create', 'update' or 'skip',
        summary a short description for the preview, data whatever save()
        needs. Raise RowError for a row that can't be imported.
        """
        raise NotImplementedError

    def save(self, planned):
        """Write the planned rows ([(action, summary, data)], skips left out). Returns a summary message."""
        raise NotImplementedError

    def plan(self, rows):
        self.prepare()
        planned, problems = [], []
        for number, row in enumerate(rows, start=2):
            try:
                action, summary, data = self.plan_row(row)
            except RowError as e:
                problems.append(f'Row {number}: {e}.')
                planned.append((number, 'error', str(e), None))
                continue
            planned.append((number, action, summary, data))
        return planned, problems


def read_rows(text, importer):
    """(rows, error): the CSV's rows as dicts with lower-cased, underscored headers."""
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        return None, 'That doesn\'t look like a CSV with a header row.'
    reader.fieldnames = [(h or '').strip().lower().replace(' ', '_') for h in reader.fieldnames]
    missing = [c for c in importer.required_columns() if c not in reader.fieldnames]
    if missing:
        return None, f'The CSV needs these columns: {", ".join(missing)}.'
    rows = []
    try:
        for row in reader:
            if not any((v or '').strip() for v in row.values() if isinstance(v, str)):
                continue  # blank line
            rows.append({k: (v or '').strip() if isinstance(v, str) else '' for k, v in row.items() if k})
            if len(rows) > MAX_ROWS:
                return None, f'That\'s more than {MAX_ROWS} rows. Split it into smaller files.'
    except csv.Error as e:  # e.g. a cell far too long, or a stray quote that never closes
        return None, f'Line {reader.line_num} of the CSV couldn\'t be read ({e}). Check it in a spreadsheet and try again.'
    if not rows:
        return None, 'The CSV has a header row but no data.'
    return rows, None


def can_use(view, importer_class):
    if importer_class.feature and not view.org.has_feature(importer_class.feature):
        return False
    if view.is_org_admin:
        return True
    return bool(importer_class.capability) and view.can(importer_class.capability)


# Every import, in the order the Import data page lists them.
IMPORTERS = (
    'classes.importers.ClassImporter',
    'classes.importers.RegisterImporter',
    'classes.importers.CoachAttendanceImporter',
    'billing.importers.InvoiceImporter',
    'billing.importers.ExpenseImporter',
)


def importer_classes():
    from django.utils.module_loading import import_string
    return {cls.key: cls for cls in map(import_string, IMPORTERS)}


class ImportView(OrgMixin, View):
    """The import page for one kind of import (`kind` in the URL), or the list of them."""

    def importer_class(self, kind):
        importer_class = importer_classes().get(kind)
        if importer_class is None:
            raise Http404
        if not can_use(self, importer_class):
            from django.core.exceptions import PermissionDenied
            raise PermissionDenied
        return importer_class

    def render_page(self, request, importer, **context):
        return render(request, 'org/import.html', {
            'org': self.org, 'org_membership': self.org_membership, 'importer': importer, **context,
        })

    def get(self, request, org_slug, kind=None):
        if kind is None:
            available = [i for i in importer_classes().values() if can_use(self, i)]
            if not available and not self.is_org_admin:
                from django.core.exceptions import PermissionDenied
                raise PermissionDenied
            return render(request, 'org/import_list.html', {
                'org': self.org, 'org_membership': self.org_membership, 'importers': available,
            })
        return self.render_page(request, self.importer_class(kind)(self.org, request.user))

    def post(self, request, org_slug, kind=None):
        if kind is None:
            raise Http404
        importer = self.importer_class(kind)(self.org, request.user)
        upload = request.FILES.get('csv_file')
        if upload is not None:
            if upload.size > MAX_CSV_BYTES:
                messages.error(request, 'That file is too big. The limit is 2 MB.')
                return self.render_page(request, importer)
            raw = upload.read()
        else:
            raw = request.POST.get('raw_csv', '').encode('utf-8')[:MAX_CSV_BYTES + 1]
            if len(raw) > MAX_CSV_BYTES:
                messages.error(request, 'That\'s too much text. The limit is 2 MB.')
                return self.render_page(request, importer)
        try:
            text = raw.decode('utf-8-sig').strip()
        except UnicodeDecodeError:
            messages.error(request, 'Save the CSV as UTF-8 and try again.')
            return self.render_page(request, importer)
        if not text:
            messages.error(request, 'Choose a CSV file or paste the CSV in.')
            return self.render_page(request, importer)

        rows, error = read_rows(text, importer)
        if error:
            messages.error(request, error)
            return self.render_page(request, importer, raw_csv=text)
        planned, problems = importer.plan(rows)

        if request.POST.get('action') != 'import':
            counts = {a: sum(1 for p in planned if p[1] == a) for a in ('create', 'update', 'skip', 'error')}
            return self.render_page(
                request, importer, raw_csv=text, planned=planned[:PREVIEW_ROWS], counts=counts,
                more_rows=max(0, len(planned) - PREVIEW_ROWS), problems=problems,
            )

        to_save = [(action, summary, data) for _, action, summary, data in planned if action in ('create', 'update')]
        with transaction.atomic():
            summary = importer.save(to_save)
        messages.success(request, summary)
        for problem in problems[:20]:
            messages.warning(request, problem)
        if len(problems) > 20:
            messages.warning(request, f'…and {len(problems) - 20} more rows were skipped.')
        return redirect(importer.done_url, org_slug=self.org.slug) if importer.done_url else redirect(
            'org_import', org_slug=self.org.slug,
        )
