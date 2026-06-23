"""
Seed the database with a fictional demo club: Ironbridge Judo Club.
Run with: python manage.py seed_demo
Add --flush to wipe all existing data first.
"""

import random
from datetime import date, timedelta, datetime
from django.contrib.auth.models import User
from django.core.management.base import BaseCommand
from django.utils import timezone
from django.utils.text import slugify


class Command(BaseCommand):
    help = 'Seed the database with fictional demo data (Ironbridge Judo Club)'

    def add_arguments(self, parser):
        parser.add_argument('--flush', action='store_true', help='Delete all existing data first')

    def handle(self, *args, **options):
        if options['flush']:
            self._flush()

        self.stdout.write('Seeding Ironbridge Judo Club...')
        org = self._create_org()
        users = self._create_users(org)
        system = self._create_progression(org)
        classes = self._create_classes(org, users)
        members = self._create_members(org, system, classes)
        self._create_sessions_and_attendance(classes, members)
        self._create_invoices(org, members)
        self._create_applications(org)
        self._create_announcements(org, users)
        self._update_admin_user()
        self.stdout.write(self.style.SUCCESS('\nDone! Log in at http://localhost:8000'))
        self.stdout.write('  Username: admin   Password: admin')
        self.stdout.write(f'  Org: {org.name} ({org.slug})')

    # ── Flush ──────────────────────────────────────────────────────────────────

    def _flush(self):
        self.stdout.write('Flushing existing data...')
        from organisations.models import Organisation, OrganisationMember, Announcement
        from members.models import Member, Guardian, MemberApplication, MemberNote, CustomField
        from classes.models import Class, Session, Attendance, ClassMember, ClassCoach, WaitingList
        from billing.models import Invoice, Payment
        from progression.models import ProgressionSystem, ProgressionStage, MemberProgression
        from documents.models import Document, WaiverTemplate, SignedWaiver
        for model in [
            SignedWaiver, Document, WaiverTemplate,
            MemberProgression, ProgressionStage, ProgressionSystem,
            Payment, Invoice,
            Attendance, ClassMember, ClassCoach, WaitingList, Session, Class,
            MemberNote, MemberApplication, Guardian, Member, CustomField,
            Announcement, OrganisationMember, Organisation,
        ]:
            count = model.objects.all().count()
            model.objects.all().delete()
            if count:
                self.stdout.write(f'  Deleted {count} {model.__name__}')
        User.objects.filter(is_superuser=False).delete()

    # ── Organisation ───────────────────────────────────────────────────────────

    def _create_org(self):
        from organisations.models import Organisation
        org, _ = Organisation.objects.get_or_create(
            slug='ironbridge-judo-club',
            defaults={
                'name': 'Ironbridge Judo Club',
                'email': 'secretary@ironbridgejudo.co.uk',
                'phone': '01952 884412',
                'website': 'https://www.ironbridgejudo.co.uk',
                'settings': {
                    'sidebar_color': '#1E3A5F',
                    'sidebar_color_dark': '#162d4a',
                    'accent_color': '#2563EB',
                    'accent_hover': '#1d4ed8',
                },
            }
        )
        self.stdout.write(f'  Org: {org.name}')
        return org

    # ── Users ──────────────────────────────────────────────────────────────────

    def _create_users(self, org):
        from organisations.models import OrganisationMember
        users = {}

        def make_user(username, first, last, role, password='demo1234'):
            u, _ = User.objects.get_or_create(username=username, defaults={
                'first_name': first, 'last_name': last,
                'email': f'{username}@ironbridgejudo.co.uk',
            })
            u.set_password(password)
            u.save()
            OrganisationMember.objects.get_or_create(user=u, organisation=org, defaults={'role': role})
            return u

        users['karen'] = make_user('karen.shaw', 'Karen', 'Shaw', 'org_admin',
                                   dbs='DBS-2024-00451', dbs_exp=date(2027, 3, 15),
                                   coaching='BJA-L2-7823', coaching_exp=date(2026, 9, 30))
        users['dean'] = make_user('dean.okafor', 'Dean', 'Okafor', 'coach',
                                  dbs='DBS-2023-88241', dbs_exp=date(2026, 6, 1),
                                  coaching='BJA-L1-3312', coaching_exp=date(2025, 12, 31))
        users['priya'] = make_user('priya.nair', 'Priya', 'Nair', 'coach',
                                   dbs='DBS-2024-11092', dbs_exp=date(2027, 11, 20),
                                   coaching='BJA-L1-5541', coaching_exp=date(2027, 5, 10))
        self.stdout.write(f'  Users: {", ".join(users)}')
        return users

    def _update_org_member_credentials(self, org, username, dbs, dbs_exp, coaching, coaching_exp):
        from organisations.models import OrganisationMember
        try:
            om = OrganisationMember.objects.get(organisation=org, user__username=username)
            om.dbs_number = dbs
            om.dbs_expiry = dbs_exp
            om.coaching_licence = coaching
            om.coaching_licence_expiry = coaching_exp
            om.save()
        except OrganisationMember.DoesNotExist:
            pass

    # ── Progression ────────────────────────────────────────────────────────────

    def _create_progression(self, org):
        from progression.models import ProgressionSystem, ProgressionStage
        system, _ = ProgressionSystem.objects.get_or_create(
            organisation=org, name='BJA Judo Grades',
            defaults={'assign_to_new_members': True}
        )
        grades = [
            ('Mon 1', '#ffffff', True, 1),
            ('Mon 2', '#f9f9a0', False, 2),
            ('Mon 3', '#f9f9a0', False, 3),
            ('Mon 4', '#f97316', False, 4),
            ('Mon 5', '#f97316', False, 5),
            ('6th Kyu (White)', '#ffffff', False, 6),
            ('5th Kyu (Yellow)', '#facc15', False, 7),
            ('4th Kyu (Orange)', '#f97316', False, 8),
            ('3rd Kyu (Green)', '#22c55e', False, 9),
            ('2nd Kyu (Blue)', '#3b82f6', False, 10),
            ('1st Kyu (Brown)', '#a16207', False, 11),
            ('1st Dan (Black)', '#1a1a1a', False, 12),
            ('2nd Dan (Black)', '#1a1a1a', False, 13),
        ]
        for name, colour, is_default, order in grades:
            ProgressionStage.objects.get_or_create(
                system=system, name=name,
                defaults={'colour': colour, 'is_default': is_default, 'order': order}
            )
        self.stdout.write(f'  Progression: {system.name} ({len(grades)} grades)')
        return system

    # ── Classes ────────────────────────────────────────────────────────────────

    def _create_classes(self, org, users):
        from classes.models import Class, ClassCoach
        data = [
            ('Junior Judo', [{'day': 0, 'time': '17:30', 'end': '18:30'},
                              {'day': 2, 'time': '17:30', 'end': '18:30'}], 30),
            ('Senior Judo', [{'day': 0, 'time': '19:00', 'end': '20:30'},
                              {'day': 3, 'time': '19:00', 'end': '20:30'}], 25),
            ('Competition Squad', [{'day': 5, 'time': '09:00', 'end': '11:00'}], 15),
            ('Beginners', [{'day': 2, 'time': '19:00', 'end': '20:00'}], 20),
        ]
        coaches_map = {
            'Junior Judo': ['priya'],
            'Senior Judo': ['karen', 'dean'],
            'Competition Squad': ['karen', 'dean'],
            'Beginners': ['priya'],
        }
        classes = {}
        for name, schedule, cap in data:
            cls, _ = Class.objects.get_or_create(
                organisation=org, name=name,
                defaults={'schedule': schedule, 'max_capacity': cap, 'description': ''}
            )
            for coach_key in coaches_map.get(name, []):
                u = users.get(coach_key)
                if u:
                    ClassCoach.objects.get_or_create(assigned_class=cls, user=u)
            classes[name] = cls
        self.stdout.write(f'  Classes: {", ".join(classes)}')
        return classes

    # ── Members ────────────────────────────────────────────────────────────────

    def _create_members(self, org, system, classes):
        from members.models import Member, Guardian, MemberNote, CustomField
        from classes.models import ClassMember
        from progression.models import ProgressionStage, MemberProgression

        # Custom field
        cf, _ = CustomField.objects.get_or_create(
            organisation=org, name='BJA Licence Number',
            defaults={'field_type': 'text'}
        )

        today = date.today()
        members_data = [
            # (name, dob, email, phone, monthly_fee, grade_name, class_names, guardian, medical, licence, notes)
            ('Alice Thornton',    date(2010,  3, 14), 'alice.thornton@email.co.uk',   '07700100001', 22, '3rd Kyu (Green)',   ['Junior Judo', 'Competition Squad'], ('Rachel Thornton', 'rachel.t@email.co.uk', '07700200001', 'Mother'), None, 'BJA-2024-11432', None),
            ('Ben Osei',          date(2009,  7, 22), 'ben.osei@email.co.uk',         '07700100002', 22, '2nd Kyu (Blue)',    ['Junior Judo', 'Competition Squad'], ('Kwame Osei', 'kwame.osei@email.co.uk', '07700200002', 'Father'), None, 'BJA-2023-88321', None),
            ('Chloe Wraight',     date(2011,  1,  5), 'chloe.w@email.co.uk',          '07700100003', 22, '4th Kyu (Orange)', ['Junior Judo'],                       ('Diane Wraight', 'diane.w@email.co.uk', '07700200003', 'Mother'), 'Asthma — carries blue inhaler. Ensure she takes a break if breathless.', 'BJA-2024-33210', None),
            ('Daniel Park',       date(2012,  9, 18), 'daniel.park@email.co.uk',      '07700100004', 22, 'Mon 3',            ['Junior Judo'],                       ('Ji-Young Park', 'jiyoung@email.co.uk', '07700200004', 'Mother'), None, None, None),
            ('Emma Gallagher',    date(2011, 11, 30), 'emma.g@email.co.uk',           '07700100005', 22, 'Mon 4',            ['Junior Judo'],                       ('Steve Gallagher', 'steve.g@email.co.uk', '07700200005', 'Father'), None, None, None),
            ('Finn Walsh',        date(2010,  5,  9), 'finn.walsh@email.co.uk',       '07700100006', 22, '4th Kyu (Orange)', ['Junior Judo'],                       ('Siobhan Walsh', 'siobhan.w@email.co.uk', '07700200006', 'Mother'), 'Nut allergy (anaphylactic). EpiPen kept in red bag in club office. Do not allow near nut products.', 'BJA-2024-09871', 'Very promising competition prospect — good uchi-mata.'),
            ('Grace Ndlovu',      date(2013,  2, 25), 'grace.n@email.co.uk',          '07700100007', 22, 'Mon 2',            ['Junior Judo'],                       ('Moses Ndlovu', 'moses.n@email.co.uk', '07700200007', 'Father'), None, None, None),
            ('Harry Stubbs',      date(2009,  8, 12), 'harry.stubbs@email.co.uk',     '07700100008', 22, '1st Kyu (Brown)',  ['Junior Judo', 'Competition Squad'], ('Janet Stubbs', 'janet.s@email.co.uk', '07700200008', 'Mother'), None, 'BJA-2022-55612', 'Aiming for 1st Dan this year — grading scheduled for October.'),
            ('Imogen Clarke',     date(2010, 12,  3), 'imogen.c@email.co.uk',         '07700100009', 22, '3rd Kyu (Green)',  ['Junior Judo'],                       ('Tom Clarke', 'tom.c@email.co.uk', '07700200009', 'Father'), 'Type 1 diabetes — manages independently but coaches should be aware. Glucose tablets in her bag.', 'BJA-2023-74412', None),
            ('Jack Reeves',       date(2011,  6, 17), 'jack.r@email.co.uk',           '07700100010', 22, 'Mon 5',            ['Junior Judo'],                       ('Sandra Reeves', 'sandra.r@email.co.uk', '07700200010', 'Mother'), None, None, None),
            ('Marcus Webb',       date(1988,  4, 11), 'marcus.webb@email.co.uk',      '07700100011', 35, '1st Dan (Black)',  ['Senior Judo', 'Competition Squad'], None, None, 'BJA-2018-00234', 'Club captain. Helps run senior sessions.'),
            ('Natasha Burns',     date(1995,  8, 29), 'natasha.burns@email.co.uk',    '07700100012', 35, '1st Kyu (Brown)',  ['Senior Judo', 'Competition Squad'], None, None, 'BJA-2021-44871', None),
            ('Oliver Jennings',   date(1990,  2,  7), 'oliver.j@email.co.uk',         '07700100013', 35, '2nd Kyu (Blue)',   ['Senior Judo'],                       None, 'Previous knee injury (right ACL, 2022). Avoid heavy newaza load — inform coach.', 'BJA-2020-33122', None),
            ('Priyanka Shah',     date(1998,  6, 22), 'priyanka.s@email.co.uk',       '07700100014', 35, '3rd Kyu (Green)',  ['Senior Judo'],                       None, None, 'BJA-2023-82341', None),
            ('Rory McAllister',   date(1985, 10, 14), 'rory.m@email.co.uk',           '07700100015', 35, '1st Dan (Black)',  ['Senior Judo', 'Competition Squad'], None, None, 'BJA-2014-00087', None),
            ('Sophie Adeyemi',    date(1993,  3, 31), 'sophie.a@email.co.uk',         '07700100016', 35, '2nd Kyu (Blue)',   ['Senior Judo'],                       None, None, 'BJA-2022-61233', None),
            ('Tom Bridger',       date(2001,  9,  5), 'tom.bridger@email.co.uk',      '07700100017', 35, '1st Kyu (Brown)',  ['Senior Judo', 'Competition Squad'], None, None, 'BJA-2021-58874', None),
            ('Uma Patel',         date(1997,  7, 18), 'uma.patel@email.co.uk',        '07700100018', 35, '4th Kyu (Orange)', ['Senior Judo'],                       None, None, None, None),
            ('Victor Holt',       date(1975,  5,  3), 'victor.holt@email.co.uk',      '07700100019', 35, '2nd Dan (Black)',  ['Senior Judo'],                       None, 'High blood pressure — medicated. Should not over-exert. Self-manages but coaches should be aware.', 'BJA-2005-00012', 'Former county champion. Retired from competition, trains for fitness.'),
            ('Wendy Cross',       date(1989, 12, 20), 'wendy.cross@email.co.uk',      '07700100020', 35, '1st Kyu (Brown)',  ['Senior Judo'],                       None, None, 'BJA-2020-49921', None),
            ('Yasmin Ford',       date(2000,  4,  8), 'yasmin.ford@email.co.uk',      '07700100021', 30, '5th Kyu (Yellow)', ['Beginners'],                         None, None, None, None),
            ('Zach Murray',       date(1999, 11, 25), 'zach.murray@email.co.uk',      '07700100022', 30, '6th Kyu (White)',  ['Beginners'],                         None, None, None, None),
            ('Abby Thornton',     date(2001,  8, 14), 'abby.t@email.co.uk',           '07700100023', 30, '5th Kyu (Yellow)', ['Beginners'],                         None, None, None, None),
            ('Callum Reid',       date(1996,  3,  9), 'callum.reid@email.co.uk',      '07700100024', 30, '6th Kyu (White)',  ['Beginners'],                         None, None, None, None),
            ('Diana Fox',         date(2002,  6, 30), 'diana.fox@email.co.uk',        '07700100025', 30, '5th Kyu (Yellow)', ['Beginners', 'Senior Judo'],          None, None, None, None),
        ]

        stage_map = {s.name: s for s in system.stages.all()}
        members = []
        for (name, dob, email, phone, fee, grade_name, class_names,
             guardian_data, medical, licence, note_text) in members_data:
            m, _ = Member.objects.get_or_create(
                organisation=org, name=name,
                defaults={
                    'date_of_birth': dob,
                    'email': email,
                    'phone': phone,
                    'monthly_fee': fee,
                    'joined_date': today - timedelta(days=random.randint(90, 900)),
                    'is_active': True,
                    'medical_info': medical or '',
                    'licence_number': licence or '',
                    'licence_expiry': (today + timedelta(days=random.randint(30, 400))) if licence else None,
                    'custom_field_values': {str(cf.pk): licence} if licence else {},
                }
            )
            if guardian_data:
                g_name, g_email, g_phone, g_rel = guardian_data
                Guardian.objects.get_or_create(
                    member=m, name=g_name,
                    defaults={'email': g_email, 'phone': g_phone, 'relationship': g_rel}
                )
            # Emergency contacts
            if not m.emergency_contact_name and guardian_data:
                g_name, g_email, g_phone, g_rel = guardian_data
                m.emergency_contact_name = g_name
                m.emergency_contact_phone = g_phone
                m.save(update_fields=['emergency_contact_name', 'emergency_contact_phone'])
            elif not m.emergency_contact_name:
                m.emergency_contact_name = f'Next of kin ({name})'
                m.emergency_contact_phone = f'0770020{str(members_data.index((name, dob, email, phone, fee, grade_name, class_names, guardian_data, medical, licence, note_text))).zfill(4)}'
                m.save(update_fields=['emergency_contact_name', 'emergency_contact_phone'])

            # Progression
            stage = stage_map.get(grade_name)
            if stage and not MemberProgression.objects.filter(member=m, stage=stage).exists():
                MemberProgression.objects.create(
                    member=m, stage=stage,
                    achieved_date=today - timedelta(days=random.randint(30, 730)),
                )
            # Enrol in classes
            for class_name in class_names:
                cls = classes.get(class_name)
                if cls:
                    ClassMember.objects.get_or_create(assigned_class=cls, member=m)
            # Notes
            if note_text:
                MemberNote.objects.get_or_create(
                    member=m, body=note_text,
                    defaults={'author': User.objects.filter(is_superuser=True).first()}
                )
            members.append(m)

        self.stdout.write(f'  Members: {len(members)} created')
        return members

    # ── Sessions & Attendance ──────────────────────────────────────────────────

    def _create_sessions_and_attendance(self, classes, members):
        from classes.models import Session, Attendance, ClassMember

        today = date.today()
        total_sessions = 0

        for class_name, cls in classes.items():
            enrolled = list(ClassMember.objects.filter(assigned_class=cls).select_related('member'))
            schedule = cls.schedule or []

            # Generate 10 weeks of past sessions + 3 weeks upcoming
            for week_offset in range(-10, 4):
                for slot in schedule:
                    day_offset = (slot['day'] - today.weekday()) % 7
                    session_date = today + timedelta(weeks=week_offset, days=day_offset)
                    if session_date > today + timedelta(days=21):
                        continue

                    is_past = session_date < today
                    is_cancelled = is_past and random.random() < 0.05  # 5% cancellation rate
                    notes = ''
                    if is_past and not is_cancelled and random.random() < 0.3:
                        notes = random.choice([
                            'Good session — focused on uchi-mata combinations.',
                            'Worked on newaza transitions. Good energy from the group.',
                            'Competition prep — randori focus.',
                            'Grading technique review. Several members looking ready.',
                            'New members settling in well. Revised breakfalls.',
                            'Fitness circuit + randori. High intensity.',
                        ])

                    session, _ = Session.objects.get_or_create(
                        assigned_class=cls, date=session_date,
                        defaults={'is_cancelled': is_cancelled, 'notes': notes}
                    )
                    total_sessions += 1

                    # Attendance for past non-cancelled sessions
                    if is_past and not is_cancelled:
                        for cm in enrolled:
                            present = random.random() < 0.78
                            Attendance.objects.get_or_create(
                                session=session, member=cm.member,
                                defaults={'present': present}
                            )

        self.stdout.write(f'  Sessions: {total_sessions} generated')

    # ── Invoices ───────────────────────────────────────────────────────────────

    def _create_invoices(self, org, members):
        from billing.models import Invoice, Payment

        today = date.today()
        count = 0

        for member in members:
            if not member.monthly_fee:
                continue

            # 3 months of invoices
            for months_ago in [3, 2, 1]:
                inv_date = today.replace(day=1) - timedelta(days=30 * months_ago)
                period = inv_date.strftime('%B %Y')
                due = inv_date.replace(day=28)

                if months_ago == 3:
                    status = 'paid'
                elif months_ago == 2:
                    status = 'paid' if random.random() < 0.85 else 'unpaid'
                else:
                    r = random.random()
                    status = 'paid' if r < 0.6 else ('unpaid' if r < 0.9 else 'overdue')

                inv, created = Invoice.objects.get_or_create(
                    organisation=org, member=member, period=period,
                    defaults={
                        'amount': member.monthly_fee,
                        'due_date': due,
                        'status': status,
                    }
                )
                if created and status == 'paid':
                    Payment.objects.create(
                        invoice=inv,
                        method=random.choice(['bacs', 'cash', 'manual']),
                        amount=member.monthly_fee,
                        paid_at=timezone.make_aware(
                            datetime.combine(due - timedelta(days=random.randint(0, 14)),
                                             datetime.min.time())
                        ),
                    )
                count += 1

        self.stdout.write(f'  Invoices: {count} created')

    # ── Applications ───────────────────────────────────────────────────────────

    def _create_applications(self, org):
        from members.models import MemberApplication

        apps = [
            ('Liam Fletcher',  date(2012,  8, 5),  'liam.fletcher@email.co.uk',  '07700300001',
             'Paul Fletcher', 'paul.f@email.co.uk', '07700400001', 'Father', 'pending', ''),
            ('Mia Winters',    date(1999,  3, 19), 'mia.winters@email.co.uk',    '07700300002',
             '', '', '', '', 'pending', ''),
            ('Noah Davies',    date(2011, 11,  2), 'noah.davies@email.co.uk',    '07700300003',
             'Claire Davies', 'claire.d@email.co.uk', '07700400003', 'Mother', 'approved', ''),
            ('Olivia Grant',   date(2003,  6, 28), 'olivia.grant@email.co.uk',   '07700300004',
             '', '', '', '', 'rejected', 'Applicant lives outside our catchment area.'),
        ]
        for (name, dob, email, phone, g_name, g_email, g_phone, g_rel, status, notes) in apps:
            MemberApplication.objects.get_or_create(
                organisation=org, name=name,
                defaults={
                    'date_of_birth': dob, 'email': email, 'phone': phone,
                    'guardian_name': g_name, 'guardian_email': g_email,
                    'guardian_phone': g_phone,
                    'notes': notes, 'status': status,
                    'address_line1': f'{random.randint(1,120)} Demo Street',
                    'city': 'Telford', 'postcode': 'TF1 1AA',
                }
            )
        self.stdout.write(f'  Applications: {len(apps)} created')

    # ── Announcements ──────────────────────────────────────────────────────────

    def _create_announcements(self, org, users):
        from organisations.models import Announcement
        admin = User.objects.filter(is_superuser=True).first()
        data = [
            ('Club closed — Bank Holiday',
             'Just a reminder that the club will be closed on the upcoming bank holiday Monday. Normal sessions resume the following week.',
             15),
            ('Summer grading — save the date',
             'We are planning our summer grading for Saturday 12th July. More details to follow, but please keep the date free. We have several members who are looking ready to grade.',
             23),
            ('New beginners course starting',
             'Our next beginners course starts on Thursday 6th February at 7pm. Please spread the word to anyone who has been thinking about giving judo a try. There are still spaces available.',
             23),
        ]
        for subject, body, count in data:
            Announcement.objects.get_or_create(
                organisation=org, subject=subject,
                defaults={
                    'body': body,
                    'sent_by': admin,
                    'recipient_count': count,
                    'recipient_label': 'All active members',
                    'sent_at': timezone.now() - timedelta(days=random.randint(5, 90)),
                }
            )
        self.stdout.write(f'  Announcements: {len(data)} created')

    # ── Admin user ─────────────────────────────────────────────────────────────

    def _update_admin_user(self):
        admin, created = User.objects.get_or_create(username='admin')
        admin.set_password('admin')
        admin.is_superuser = True
        admin.is_staff = True
        admin.first_name = 'Admin'
        admin.save()

    # ── Helper to set coach credentials ───────────────────────────────────────

    def _create_users(self, org):
        from organisations.models import OrganisationMember
        users = {}

        def make_user(username, first, last, role, password='demo1234',
                      dbs='', dbs_exp=None, coaching='', coaching_exp=None):
            u, _ = User.objects.get_or_create(username=username, defaults={
                'first_name': first, 'last_name': last,
                'email': f'{username}@ironbridgejudo.co.uk',
            })
            u.set_password(password)
            u.save()
            om, _ = OrganisationMember.objects.get_or_create(
                user=u, organisation=org, defaults={'role': role}
            )
            if dbs:
                om.dbs_number = dbs
                om.dbs_expiry = dbs_exp
                om.coaching_licence = coaching
                om.coaching_licence_expiry = coaching_exp
                om.save()
            return u

        users['karen'] = make_user('karen.shaw', 'Karen', 'Shaw', 'org_admin',
                                   dbs='DBS-2024-00451', dbs_exp=date(2027, 3, 15),
                                   coaching='BJA-L2-7823', coaching_exp=date(2026, 9, 30))
        users['dean'] = make_user('dean.okafor', 'Dean', 'Okafor', 'coach',
                                  dbs='DBS-2023-88241', dbs_exp=date(2026, 6, 1),
                                  coaching='BJA-L1-3312', coaching_exp=date(2025, 12, 31))
        users['priya'] = make_user('priya.nair', 'Priya', 'Nair', 'coach',
                                   dbs='DBS-2024-11092', dbs_exp=date(2027, 11, 20),
                                   coaching='BJA-L1-5541', coaching_exp=date(2027, 5, 10))
        self.stdout.write(f'  Users: {", ".join(users)} (password: demo1234)')
        return users
