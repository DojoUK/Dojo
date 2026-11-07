"""CSV imports for classes, registers and coach attendance (see dojo.csv_import)."""
import re

from collections import defaultdict

from dojo.csv_import import Importer, MemberFinder, RowError, name_key, parse_date, parse_yes_no

from .models import Attendance, Class, Session, SessionCoach

DAYS = {
    'mon': 0, 'monday': 0, 'tue': 1, 'tues': 1, 'tuesday': 1, 'wed': 2, 'weds': 2, 'wednesday': 2,
    'thu': 3, 'thur': 3, 'thurs': 3, 'thursday': 3, 'fri': 4, 'friday': 4,
    'sat': 5, 'saturday': 5, 'sun': 6, 'sunday': 6,
}
SCHEDULE_ENTRY = re.compile(r'^([A-Za-z]+)\s+(\d{1,2})[:.](\d{2})\s*(?:-\s*(\d{1,2})[:.](\d{2}))?$')


def parse_schedule(raw):
    """'Tue 18:00-18:45, Thu 19:00' → [{'day': 1, 'time': '18:00', 'end': '18:45'}, {'day': 3, 'time': '19:00'}]."""
    schedule = []
    for chunk in re.split(r'[;,]', raw or ''):
        chunk = chunk.strip()
        if not chunk:
            continue
        m = SCHEDULE_ENTRY.match(chunk)
        day = DAYS.get(m.group(1).lower()) if m else None
        if day is None:
            raise RowError(f'schedule "{chunk}" should look like "Tue 18:00-18:45"')
        start_h, start_m, end_h, end_m = m.group(2, 3, 4, 5)
        if int(start_h) > 23 or int(start_m) > 59 or (end_h and (int(end_h) > 23 or int(end_m) > 59)):
            raise RowError(f'schedule "{chunk}" has an impossible time')
        entry = {'day': day, 'time': f'{int(start_h):02d}:{start_m}'}
        if end_h:
            entry['end'] = f'{int(end_h):02d}:{end_m}'
        schedule.append(entry)
    return schedule


class ClassImporter(Importer):
    key = 'classes'
    title = 'Classes'
    intro = ('Adds classes, or updates the description, schedule and capacity of classes that already '
             'exist with the same name. For a class that already exists, a missing column or blank cell '
             'leaves that detail as it is.')
    columns = (
        ('name', True, 'The class name, e.g. "Juniors".'),
        ('description', False, ''),
        ('schedule', False, 'Days and times, e.g. "Tue 18:00-18:45, Thu 19:00-20:00".'),
        ('max_capacity', False, 'Most members allowed. Blank: no limit for a new class.'),
    )
    LABELS = {'description': 'description', 'schedule': 'schedule', 'max_capacity': 'capacity'}
    example = 'name,schedule,max_capacity\nJuniors,"Tue 18:00-18:45, Thu 18:00-18:45",30\nSeniors,Tue 19:00-20:30,'
    done_url = 'class_list'

    def prepare(self):
        self.existing = {name_key(c.name): c for c in Class.objects.filter(organisation=self.org)}
        self.seen = set()

    def plan_row(self, row):
        name = ' '.join(row.get('name', '').split())[:255]
        if not name:
            raise RowError('missing name')
        if name_key(name) in self.seen:
            raise RowError(f'"{name}" is in the file twice')
        self.seen.add(name_key(name))
        capacity_raw = row.get('max_capacity', '')
        if capacity_raw and not capacity_raw.isdigit():
            raise RowError(f'max_capacity "{capacity_raw}" isn\'t a whole number')
        fields = {
            'description': row.get('description', ''),
            'schedule': parse_schedule(row.get('schedule', '')),
            'max_capacity': int(capacity_raw) if capacity_raw else None,
        }
        existing = self.existing.get(name_key(name))
        if existing:
            # Only what the file actually gives: a missing column or blank cell isn't "clear it".
            changes = {
                field: value for field, value in fields.items()
                if row.get(field) and value != getattr(existing, field)
            }
            if not changes:
                return 'skip', f'{existing.name}: nothing to change', None
            return 'update', f'Update {existing.name} ({", ".join(self.LABELS[f] for f in changes)})', (existing, changes)
        return 'create', f'Add {name}', (name, fields)

    def save(self, planned):
        created = updated = 0
        for action, _, data in planned:
            if action == 'update':
                cls, fields = data
                for field, value in fields.items():
                    setattr(cls, field, value)
                cls.save(update_fields=list(fields))
                updated += 1
            else:
                name, fields = data
                Class.objects.create(organisation=self.org, name=name, **fields)
                created += 1
        return f'{created} class{"es" if created != 1 else ""} added, {updated} updated.'


class SessionRowsMixin:
    """Shared by the register imports: find the class, and the session on that date (made if missing)."""

    def prepare(self):
        self.classes = {name_key(c.name): c for c in Class.objects.filter(organisation=self.org)}
        self.sessions = {
            (s.assigned_class_id, s.date): s
            for s in Session.objects.filter(assigned_class__organisation=self.org)
        }

    def class_and_date(self, row):
        cls = self.classes.get(name_key(row.get('class_name', '')))
        if cls is None:
            raise RowError(f'no class called "{row.get("class_name", "")}" (import classes first)')
        return cls, parse_date(row.get('date'))

    def session_for(self, cls, day):
        """The session for a class on a date, created if it doesn't exist: extra if it's off the schedule."""
        session = self.sessions.get((cls.pk, day))
        if session is None:
            on_schedule = any(entry.get('day') == day.weekday() for entry in cls.schedule or [])
            session, _ = Session.objects.get_or_create(
                assigned_class=cls, date=day, defaults={'is_extra': not on_schedule},
            )
            self.sessions[(cls.pk, day)] = session
        return session


class RegisterImporter(SessionRowsMixin, Importer):
    key = 'registers'
    title = 'Registers'
    intro = ('Marks members present or absent at past sessions. A session that doesn\'t exist yet is added '
             '(as an extra session if it\'s not on the class\'s schedule). Members must already exist; '
             'importing the same row again just updates the mark.')
    columns = (
        ('class_name', True, 'Must match a class name.'),
        ('date', True, 'e.g. 2026-03-14 or 14/03/2026.'),
        ('member_name', True, 'Must match a member\'s name. "Surname Firstname" is tried too.'),
        ('member_email', False, 'Only needed to tell apart two members with the same name.'),
        ('present', False, 'Yes or no; blank means absent.'),
    )
    example = 'class_name,date,member_name,present\nJuniors,14/03/2026,Alice Able,yes\nJuniors,14/03/2026,Bob Best,no'
    done_url = 'attendance_analytics'

    def prepare(self):
        super().prepare()
        self.members = MemberFinder(self.org)

    def plan_row(self, row):
        cls, day = self.class_and_date(row)
        member = self.members.find(row.get('member_name', ''), row.get('member_email', ''))
        present = parse_yes_no(row.get('present'), default=False)
        mark = 'present' if present else 'absent'
        return 'create', f'{member.name}: {mark} at {cls.name} on {day:%d %b %Y}', (cls, day, member, present)

    def save(self, planned):
        count = 0
        for _, _, (cls, day, member, present) in planned:
            session = self.session_for(cls, day)
            Attendance.objects.update_or_create(session=session, member=member, defaults={'present': present})
            count += 1
        return f'{count} register mark{"s" if count != 1 else ""} imported.'


class CoachAttendanceImporter(SessionRowsMixin, Importer):
    key = 'coach-attendance'
    title = 'Coach attendance'
    intro = ('Marks coaches present or absent at past sessions, adding sessions that don\'t exist yet. '
             'Coaches must already be staff in Dojo; they\'re matched by full name or username.')
    columns = (
        ('class_name', True, 'Must match a class name.'),
        ('date', True, 'e.g. 2026-03-14 or 14/03/2026.'),
        ('coach_name', True, 'A staff member\'s full name or username.'),
        ('present', False, 'Yes or no; blank means absent.'),
    )
    example = 'class_name,date,coach_name,present\nJuniors,14/03/2026,Cora Coach,yes'
    done_url = 'class_list'

    def prepare(self):
        super().prepare()
        from organisations.models import OrganisationMember
        self.staff = defaultdict(dict)
        for om in OrganisationMember.objects.filter(organisation=self.org).select_related('user'):
            for key in (om.user.get_full_name(), om.user.username):
                if key:
                    self.staff[name_key(key)][om.user.pk] = om.user

    def plan_row(self, row):
        cls, day = self.class_and_date(row)
        name = row.get('coach_name', '')
        matches = list(self.staff.get(name_key(name), {}).values())
        if not matches:
            raise RowError(f'no staff member called "{name}"')
        if len(matches) > 1:
            raise RowError(f'{len(matches)} staff members are called "{name}": use their username instead')
        coach = matches[0]
        present = parse_yes_no(row.get('present'), default=False)
        name = coach.get_full_name() or coach.username
        mark = 'present' if present else 'absent'
        return 'create', f'{name}: {mark} at {cls.name} on {day:%d %b %Y}', (cls, day, coach, present)

    def save(self, planned):
        count = 0
        for _, _, (cls, day, coach, present) in planned:
            session = self.session_for(cls, day)
            SessionCoach.objects.update_or_create(session=session, coach=coach, defaults={'present': present})
            count += 1
        return f'{count} coach mark{"s" if count != 1 else ""} imported.'
