"""
Seed the database with a fictional demo club: Mockingham Martial Arts Club.
Run with: python manage.py seed_demo
Add --flush to wipe all existing data first.
"""

import random
from datetime import date, timedelta, datetime
from django.contrib.auth.models import User
from django.core.management.base import BaseCommand
from django.utils import timezone
from django.utils.text import slugify

ADDRESS_STREETS = [
    'Elm Street', 'Oak Avenue', 'Station Road', 'Church Lane', 'Mill Road',
    'Victoria Street', 'Kings Road', 'Park View', 'Meadow Close', 'High Street',
    'Windsor Drive', 'Orchard Way', 'Bridge Street', 'Hillside Avenue', 'Chapel Street',
]


class Command(BaseCommand):
    help = 'Seed the database with fictional demo data (Mockingham Martial Arts Club)'

    def add_arguments(self, parser):
        parser.add_argument('--flush', action='store_true', help='Delete all existing data first')

    def handle(self, *args, **options):
        if options['flush']:
            self._flush()

        self.stdout.write('Seeding Mockingham Martial Arts Club...')
        org = self._create_org()
        users = self._create_users(org)
        system = self._create_progression(org)
        classes = self._create_classes(org, users)
        members = self._create_members(org, system, classes)
        self._create_family_groups(org)
        self._create_extra_custom_fields(org)
        self._create_sessions_and_attendance(classes, members)
        self._create_billing_policies(org)
        self._create_invoices(org, members)
        variants = self._create_inventory(org)
        self._sell_demo_products(variants)
        self._create_terms(org)
        self._create_waiting_list(classes)
        self._create_former_members(org)
        self._create_documents_and_waivers(org, members)
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
        from members.models import (
            Member, Guardian, MemberApplication, MemberNote, CustomField,
            FamilyGroup, FamilyGroupMember,
        )
        from classes.models import Class, Session, Attendance, ClassMember, ClassCoach, WaitingList, SessionCoach
        from billing.models import (
            Invoice, Payment, BillingPolicy, OrgTerm, PolicyDiscount, MemberDiscount, InvoiceItem,
        )
        from progression.models import ProgressionSystem, ProgressionStage, MemberProgression
        from documents.models import Document, WaiverTemplate, SignedWaiver
        from inventory.models import Product, ProductVariant, StockMovement
        for model in [
            SignedWaiver, Document, WaiverTemplate,
            MemberProgression, ProgressionStage, ProgressionSystem,
            StockMovement, InvoiceItem, ProductVariant, Product,
            Payment, Invoice, MemberDiscount, PolicyDiscount, OrgTerm, BillingPolicy,
            SessionCoach, Attendance, ClassMember, WaitingList, ClassCoach, Session, Class,
            FamilyGroupMember, FamilyGroup,
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
            slug='mockingham-martial-arts',
            defaults={
                'name': 'Mockingham Martial Arts Club',
                'email': 'secretary@mockingham-ma.example',
                'phone': '01632 960123',
                'website': 'https://www.mockingham-ma.example',
                'settings': {
                    'sidebar_color': '#1E3A5F',
                    'sidebar_color_dark': '#162d4a',
                    'accent_color': '#2563EB',
                    'accent_hover': '#1d4ed8',
                    'demo': True,
                },
            }
        )
        self.stdout.write(f'  Org: {org.name}')
        return org

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
            organisation=org, name='MAF Grading Syllabus',
            defaults={'assign_to_new_members': True}
        )
        grades = [
            ('Cadet 1', '#ffffff', True, 1),
            ('Cadet 2', '#f9f9a0', False, 2),
            ('Cadet 3', '#f9f9a0', False, 3),
            ('Cadet 4', '#f97316', False, 4),
            ('Cadet 5', '#f97316', False, 5),
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
            ('Junior Class', [{'day': 0, 'time': '17:30', 'end': '18:30'},
                              {'day': 2, 'time': '17:30', 'end': '18:30'}], 30),
            ('Senior Class', [{'day': 0, 'time': '19:00', 'end': '20:30'},
                              {'day': 3, 'time': '19:00', 'end': '20:30'}], 25),
            ('Competition Squad', [{'day': 5, 'time': '09:00', 'end': '11:00'}], 15),
            ('Beginners', [{'day': 2, 'time': '19:00', 'end': '20:00'}], 20),
        ]
        coaches_map = {
            'Junior Class': ['priya'],
            'Senior Class': ['karen', 'dean'],
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
            organisation=org, name='MAF Licence Number',
            defaults={'field_type': 'text'}
        )

        today = date.today()
        members_data = [
            # (name, dob, email, phone, monthly_fee, grade_name, class_names, guardian, medical, licence, notes)
            ('Alice Thornton',    date(2010,  3, 14), 'alice.thornton@example.com',   '07700100001', 22, '3rd Kyu (Green)',   ['Junior Class', 'Competition Squad'], ('Rachel Thornton', 'rachel.t@example.com', '07700200001', 'Mother'), None, 'MAF-2024-11432', None),
            ('Ben Osei',          date(2009,  7, 22), 'ben.osei@example.com',         '07700100002', 22, '2nd Kyu (Blue)',    ['Junior Class', 'Competition Squad'], ('Kwame Osei', 'kwame.osei@example.com', '07700200002', 'Father'), None, 'MAF-2023-88321', None),
            ('Chloe Wraight',     date(2011,  1,  5), 'chloe.w@example.com',          '07700100003', 22, '4th Kyu (Orange)', ['Junior Class'],                       ('Diane Wraight', 'diane.w@example.com', '07700200003', 'Mother'), 'Asthma — carries blue inhaler. Ensure she takes a break if breathless.', 'MAF-2024-33210', None),
            ('Daniel Park',       date(2012,  9, 18), 'daniel.park@example.com',      '07700100004', 22, 'Cadet 3',            ['Junior Class'],                       ('Ji-Young Park', 'jiyoung@example.com', '07700200004', 'Mother'), None, None, None),
            ('Emma Gallagher',    date(2011, 11, 30), 'emma.g@example.com',           '07700100005', 22, 'Cadet 4',            ['Junior Class'],                       ('Steve Gallagher', 'steve.g@example.com', '07700200005', 'Father'), None, None, None),
            ('Finn Walsh',        date(2010,  5,  9), 'finn.walsh@example.com',       '07700100006', 22, '4th Kyu (Orange)', ['Junior Class'],                       ('Siobhan Walsh', 'siobhan.w@example.com', '07700200006', 'Mother'), 'Nut allergy (anaphylactic). EpiPen kept in red bag in club office. Do not allow near nut products.', 'MAF-2024-09871', 'Very promising competition prospect — strong throwing technique.'),
            ('Grace Ndlovu',      date(2013,  2, 25), 'grace.n@example.com',          '07700100007', 22, 'Cadet 2',            ['Junior Class'],                       ('Moses Ndlovu', 'moses.n@example.com', '07700200007', 'Father'), None, None, None),
            ('Harry Stubbs',      date(2009,  8, 12), 'harry.stubbs@example.com',     '07700100008', 22, '1st Kyu (Brown)',  ['Junior Class', 'Competition Squad'], ('Janet Stubbs', 'janet.s@example.com', '07700200008', 'Mother'), None, 'MAF-2022-55612', 'Aiming for 1st Dan this year — grading scheduled for October.'),
            ('Imogen Clarke',     date(2010, 12,  3), 'imogen.c@example.com',         '07700100009', 22, '3rd Kyu (Green)',  ['Junior Class'],                       ('Tom Clarke', 'tom.c@example.com', '07700200009', 'Father'), 'Type 1 diabetes — manages independently but coaches should be aware. Glucose tablets in her bag.', 'MAF-2023-74412', None),
            ('Jack Reeves',       date(2011,  6, 17), 'jack.r@example.com',           '07700100010', 22, 'Cadet 5',            ['Junior Class'],                       ('Sandra Reeves', 'sandra.r@example.com', '07700200010', 'Mother'), None, None, None),
            ('Marcus Webb',       date(1988,  4, 11), 'marcus.webb@example.com',      '07700100011', 35, '1st Dan (Black)',  ['Senior Class', 'Competition Squad'], None, None, 'MAF-2018-00234', 'Club captain. Helps run senior sessions.'),
            ('Natasha Burns',     date(1995,  8, 29), 'natasha.burns@example.com',    '07700100012', 35, '1st Kyu (Brown)',  ['Senior Class', 'Competition Squad'], None, None, 'MAF-2021-44871', None),
            ('Oliver Jennings',   date(1990,  2,  7), 'oliver.j@example.com',         '07700100013', 35, '2nd Kyu (Blue)',   ['Senior Class'],                       None, 'Previous knee injury (right ACL, 2022). Avoid heavy groundwork load — inform coach.', 'MAF-2020-33122', None),
            ('Priyanka Shah',     date(1998,  6, 22), 'priyanka.s@example.com',       '07700100014', 35, '3rd Kyu (Green)',  ['Senior Class'],                       None, None, 'MAF-2023-82341', None),
            ('Rory McAllister',   date(1985, 10, 14), 'rory.m@example.com',           '07700100015', 35, '1st Dan (Black)',  ['Senior Class', 'Competition Squad'], None, None, 'MAF-2014-00087', None),
            ('Sophie Adeyemi',    date(1993,  3, 31), 'sophie.a@example.com',         '07700100016', 35, '2nd Kyu (Blue)',   ['Senior Class'],                       None, None, 'MAF-2022-61233', None),
            ('Tom Bridger',       date(2001,  9,  5), 'tom.bridger@example.com',      '07700100017', 35, '1st Kyu (Brown)',  ['Senior Class', 'Competition Squad'], None, None, 'MAF-2021-58874', None),
            ('Uma Patel',         date(1997,  7, 18), 'uma.patel@example.com',        '07700100018', 35, '4th Kyu (Orange)', ['Senior Class'],                       None, None, None, None),
            ('Victor Holt',       date(1975,  5,  3), 'victor.holt@example.com',      '07700100019', 35, '2nd Dan (Black)',  ['Senior Class'],                       None, 'High blood pressure — medicated. Should not over-exert. Self-manages but coaches should be aware.', 'MAF-2005-00012', 'Former county champion. Retired from competition, trains for fitness.'),
            ('Wendy Cross',       date(1989, 12, 20), 'wendy.cross@example.com',      '07700100020', 35, '1st Kyu (Brown)',  ['Senior Class'],                       None, None, 'MAF-2020-49921', None),
            ('Yasmin Ford',       date(2000,  4,  8), 'yasmin.ford@example.com',      '07700100021', 30, '5th Kyu (Yellow)', ['Beginners'],                         None, None, None, None),
            ('Zach Murray',       date(1999, 11, 25), 'zach.murray@example.com',      '07700100022', 30, '6th Kyu (White)',  ['Beginners'],                         None, None, None, None),
            ('Abby Thornton',     date(2001,  8, 14), 'abby.t@example.com',           '07700100023', 30, '5th Kyu (Yellow)', ['Beginners'],                         None, None, None, None),
            ('Callum Reid',       date(1996,  3,  9), 'callum.reid@example.com',      '07700100024', 30, '6th Kyu (White)',  ['Beginners'],                         None, None, None, None),
            ('Diana Fox',         date(2002,  6, 30), 'diana.fox@example.com',        '07700100025', 30, '5th Kyu (Yellow)', ['Beginners', 'Senior Class'],          None, None, None, None),
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

            # Address
            if not m.address_line1:
                idx = members_data.index((name, dob, email, phone, fee, grade_name, class_names, guardian_data, medical, licence, note_text))
                street_number = 1 + (idx * 7) % 180
                street_name = ADDRESS_STREETS[idx % len(ADDRESS_STREETS)]
                m.address_line1 = f'{street_number} {street_name}'
                m.address_line2 = 'Mockingham'
                m.save(update_fields=['address_line1', 'address_line2'])

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
        from classes.models import Session, Attendance, ClassMember, ClassCoach, SessionCoach

        today = date.today()
        total_sessions = 0

        for class_name, cls in classes.items():
            enrolled = list(ClassMember.objects.filter(assigned_class=cls).select_related('member'))
            coaches = list(ClassCoach.objects.filter(assigned_class=cls).select_related('user'))
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
                            'Good session — focused on throwing combinations.',
                            'Worked on groundwork transitions. Good energy from the group.',
                            'Competition prep — sparring focus.',
                            'Grading technique review. Several members looking ready.',
                            'New members settling in well. Revised breakfalls.',
                            'Fitness circuit + sparring. High intensity.',
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
                        for cc in coaches:
                            present = random.random() < 0.9
                            SessionCoach.objects.get_or_create(
                                session=session, coach=cc.user,
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

    # ── Family groups ──────────────────────────────────────────────────────────

    def _create_family_groups(self, org):
        from members.models import Member, FamilyGroup, FamilyGroupMember

        family, _ = FamilyGroup.objects.get_or_create(
            organisation=org, name='Thornton Family',
            defaults={'discount_percentage': 10},
        )
        for name in ['Alice Thornton', 'Abby Thornton']:
            member = Member.objects.filter(organisation=org, name=name).first()
            if member:
                FamilyGroupMember.objects.get_or_create(family_group=family, member=member)
        self.stdout.write(f'  Family groups: {family.name}')

    # ── Extra custom fields ────────────────────────────────────────────────────

    def _create_extra_custom_fields(self, org):
        from members.models import Member, CustomField

        contact_field, _ = CustomField.objects.get_or_create(
            organisation=org, name='Preferred Contact Method',
            defaults={'field_type': CustomField.FieldType.SELECT,
                      'options': ['Email', 'Phone', 'Text'], 'order': 1},
        )
        newsletter_field, _ = CustomField.objects.get_or_create(
            organisation=org, name='Newsletter Opt-in',
            defaults={'field_type': CustomField.FieldType.BOOLEAN, 'order': 2},
        )
        contact_choices = ['Email', 'Phone', 'Text']
        for i, member in enumerate(Member.objects.filter(organisation=org).order_by('pk')):
            values = member.custom_field_values or {}
            if str(contact_field.pk) not in values:
                values[str(contact_field.pk)] = contact_choices[i % len(contact_choices)]
            if str(newsletter_field.pk) not in values:
                values[str(newsletter_field.pk)] = (i % 3 != 0)
            member.custom_field_values = values
            member.save(update_fields=['custom_field_values'])
        self.stdout.write('  Custom fields: Preferred Contact Method, Newsletter Opt-in')

    # ── Billing policies & discounts ───────────────────────────────────────────

    def _create_billing_policies(self, org):
        from members.models import Member
        from billing.models import BillingPolicy, PolicyDiscount, MemberDiscount
        from classes.models import Class

        junior, _ = BillingPolicy.objects.get_or_create(
            organisation=org, name='Junior Membership',
            defaults={'billing_cycle': BillingPolicy.BillingCycle.MONTHLY,
                      'pricing_model': BillingPolicy.PricingModel.FLAT,
                      'amount': 22, 'description': 'Standard junior membership, one class per week.'},
        )
        senior, _ = BillingPolicy.objects.get_or_create(
            organisation=org, name='Senior Membership',
            defaults={'billing_cycle': BillingPolicy.BillingCycle.MONTHLY,
                      'pricing_model': BillingPolicy.PricingModel.FLAT,
                      'amount': 35, 'description': 'Standard senior membership, unlimited classes.'},
        )
        beginners, _ = BillingPolicy.objects.get_or_create(
            organisation=org, name='Beginners Course',
            defaults={'billing_cycle': BillingPolicy.BillingCycle.TERMLY,
                      'pricing_model': BillingPolicy.PricingModel.FLAT,
                      'amount': 90, 'description': 'Introductory course, billed per term.'},
        )
        pay_as_you_go, _ = BillingPolicy.objects.get_or_create(
            organisation=org, name='Pay As You Go',
            defaults={'billing_cycle': BillingPolicy.BillingCycle.MONTHLY,
                      'pricing_model': BillingPolicy.PricingModel.PER_SESSION,
                      'per_session_rate': 8, 'additional_class_discount': 50,
                      'description': 'Competition squad members pay per session attended.'},
        )

        PolicyDiscount.objects.get_or_create(
            policy=junior, name='Sibling Discount',
            defaults={'discount_type': PolicyDiscount.DiscountType.PERCENTAGE, 'value': 10},
        )
        PolicyDiscount.objects.get_or_create(
            policy=senior, name='Loyalty Discount',
            defaults={'discount_type': PolicyDiscount.DiscountType.PERCENTAGE, 'value': 15},
        )
        first_term_discount, _ = PolicyDiscount.objects.get_or_create(
            policy=beginners, name='First Term Discount',
            defaults={'discount_type': PolicyDiscount.DiscountType.FIXED, 'value': 10},
        )

        fee_to_policy = {22: junior, 35: senior, 30: beginners}
        for member in Member.objects.filter(organisation=org):
            if member.billing_policy_id is None and member.monthly_fee in fee_to_policy:
                member.billing_policy = fee_to_policy[member.monthly_fee]
                member.save(update_fields=['billing_policy'])

        comp_squad = Class.objects.filter(organisation=org, name='Competition Squad').first()
        if comp_squad and not comp_squad.billing_policy_id:
            comp_squad.billing_policy = pay_as_you_go
            comp_squad.save(update_fields=['billing_policy'])

        sibling_discount = PolicyDiscount.objects.filter(policy=junior, name='Sibling Discount').first()
        loyalty_discount = PolicyDiscount.objects.filter(policy=senior, name='Loyalty Discount').first()
        for name in ['Alice Thornton', 'Abby Thornton']:
            member = Member.objects.filter(organisation=org, name=name).first()
            if member and sibling_discount:
                MemberDiscount.objects.get_or_create(member=member, discount=sibling_discount)
        marcus = Member.objects.filter(organisation=org, name='Marcus Webb').first()
        if marcus and loyalty_discount:
            MemberDiscount.objects.get_or_create(member=marcus, discount=loyalty_discount)
        yasmin = Member.objects.filter(organisation=org, name='Yasmin Ford').first()
        if yasmin and first_term_discount:
            MemberDiscount.objects.get_or_create(member=yasmin, discount=first_term_discount)

        self.stdout.write('  Billing policies: Junior/Senior Membership, Beginners Course, Pay As You Go')
        return {'junior': junior, 'senior': senior, 'beginners': beginners, 'pay_as_you_go': pay_as_you_go}

    # ── Terms ──────────────────────────────────────────────────────────────────

    def _create_terms(self, org):
        from billing.models import OrgTerm

        terms = [
            ('Spring Term 2026', date(2026, 1, 5), date(2026, 3, 27)),
            ('Summer Term 2026', date(2026, 4, 13), date(2026, 7, 17)),
            ('Autumn Term 2026', date(2026, 9, 7), date(2026, 12, 18)),
        ]
        for name, start, end in terms:
            OrgTerm.objects.get_or_create(
                organisation=org, name=name,
                defaults={'start_date': start, 'end_date': end},
            )
        self.stdout.write(f'  Terms: {len(terms)} created')

    # ── Inventory ──────────────────────────────────────────────────────────────

    def _create_inventory(self, org):
        from inventory.models import Product, ProductVariant, StockMovement

        catalogue = [
            ('Adult Gi', Product.Category.GI, [
                ('3', 45, 5, 2), ('4', 45, 1, 2), ('5', 48, 6, 2),
            ]),
            ('Kids Gi', Product.Category.GI, [
                ('000', 25, 8, 2), ('0', 25, 6, 2), ('1', 28, 4, 2), ('2', 28, 3, 2),
            ]),
            ('Club Belt', Product.Category.BELT, [
                ('White', 8, 15, 3), ('Yellow', 8, 12, 3), ('Orange', 8, 10, 3),
                ('Green', 9, 8, 3), ('Blue', 9, 6, 2), ('Brown', 10, 4, 2), ('Black', 15, 3, 1),
            ]),
            ('Focus Mitts', Product.Category.PROTECTIVE, [
                ('Pair', 18, 6, 2),
            ]),
            ('Club Hoodie', Product.Category.APPAREL, [
                ('S', 25, 5, 2), ('M', 25, 6, 2), ('L', 25, 4, 2), ('XL', 25, 0, 2),
            ]),
            ('Water Bottle', Product.Category.ACCESSORY, [
                ('Standard', 6, 20, 5),
            ]),
        ]

        variants = {}
        product_count = 0
        variant_count = 0
        for product_name, category, sizes in catalogue:
            product, _ = Product.objects.get_or_create(
                organisation=org, name=product_name, defaults={'category': category},
            )
            product_count += 1
            for size, price, stock, threshold in sizes:
                variant, created = ProductVariant.objects.get_or_create(
                    product=product, size=size,
                    defaults={'price': price, 'quantity_in_stock': stock, 'low_stock_threshold': threshold},
                )
                variant_count += 1
                variants[(product_name, size)] = variant
                if created and stock > 0:
                    StockMovement.objects.create(
                        variant=variant, quantity_change=stock,
                        reason=StockMovement.Reason.RESTOCK, notes='Initial stock on seeding',
                    )
        self.stdout.write(f'  Inventory: {product_count} products, {variant_count} variants')
        return variants

    def _sell_demo_products(self, variants):
        from billing.models import Invoice, InvoiceItem
        from members.models import Member

        sales = [
            ('Marcus Webb', ('Adult Gi', '5'), 1),
            ('Diana Fox', ('Club Belt', 'Yellow'), 1),
        ]
        sold = 0
        for member_name, variant_key, qty in sales:
            variant = variants.get(variant_key)
            member = Member.objects.filter(name=member_name).first()
            if not variant or not member:
                continue
            invoice = Invoice.objects.filter(member=member).order_by('-pk').first()
            if not invoice or InvoiceItem.objects.filter(invoice=invoice, variant=variant).exists():
                continue
            InvoiceItem.objects.create(
                invoice=invoice, variant=variant, quantity=qty, unit_price=variant.price,
            )
            invoice.amount = (invoice.amount or 0) + (variant.price * qty)
            invoice.save(update_fields=['amount'])
            variant.adjust_stock(-qty, reason='sale', invoice=invoice, notes=f'Sold on invoice #{invoice.pk} (demo seed)')
            sold += 1
        self.stdout.write(f'  Product sales: {sold} demo invoice line items')

    # ── Waiting list ───────────────────────────────────────────────────────────

    def _create_waiting_list(self, classes):
        from members.models import Member
        from classes.models import WaitingList

        senior = classes.get('Senior Class')
        if not senior:
            return
        count = 0
        for name in ['Yasmin Ford', 'Callum Reid']:
            member = Member.objects.filter(name=name).first()
            if member:
                _, created = WaitingList.objects.get_or_create(assigned_class=senior, member=member)
                count += created
        self.stdout.write(f'  Waiting list: {count} entries added to {senior.name}')

    # ── Former members ─────────────────────────────────────────────────────────

    def _create_former_members(self, org):
        from members.models import Member

        now = timezone.now()
        zach = Member.objects.filter(organisation=org, name='Zach Murray').first()
        if zach and zach.is_active:
            zach.is_active = False
            zach.archived_at = now - timedelta(days=4 * 365)
            zach.save(update_fields=['is_active', 'archived_at'])
        diana = Member.objects.filter(organisation=org, name='Diana Fox').first()
        if diana and diana.is_active:
            diana.is_active = False
            diana.archived_at = now - timedelta(days=180)
            diana.retention_notes = 'Outstanding balance dispute — retain until resolved.'
            diana.save(update_fields=['is_active', 'archived_at', 'retention_notes'])
        self.stdout.write('  Former members: Zach Murray (flagged), Diana Fox (retention override)')

    # ── Documents & waivers ────────────────────────────────────────────────────

    def _create_documents_and_waivers(self, org, members):
        from django.core.files.base import ContentFile
        from documents.models import Document, WaiverTemplate, SignedWaiver

        membership_waiver, _ = WaiverTemplate.objects.get_or_create(
            organisation=org, name='Membership Waiver & Consent',
            defaults={
                'description': 'Standard liability waiver and consent to participate in training.',
                'file': ContentFile(b'%PDF-1.4\n%Demo membership waiver document.\n', name='membership_waiver.pdf'),
                'is_required': True,
            },
        )
        WaiverTemplate.objects.get_or_create(
            organisation=org, name='Photography Consent',
            defaults={
                'description': 'Consent to appear in club photos/videos for marketing use.',
                'file': ContentFile(b'%PDF-1.4\n%Demo photography consent document.\n', name='photography_consent.pdf'),
                'is_required': False,
            },
        )

        unsigned = {'Jack Reeves', 'Yasmin Ford'}
        signed_count = 0
        for member in members:
            if member.name in unsigned:
                continue
            if SignedWaiver.objects.filter(member=member, template=membership_waiver).exists():
                continue
            guardian = member.guardians.first()
            SignedWaiver.objects.create(
                member=member, template=membership_waiver,
                signed_pdf=ContentFile(b'%PDF-1.4\n%Demo signed waiver.\n', name=f'waiver_{member.pk}.pdf'),
                signer_name=guardian.name if guardian else member.name,
                signed_at=timezone.now() - timedelta(days=random.randint(30, 700)),
                offline=random.random() < 0.2,
            )
            signed_count += 1
        self.stdout.write(f'  Waivers: {signed_count} signed (2 members deliberately left unsigned)')

        finn = next((m for m in members if m.name == 'Finn Walsh'), None)
        doc_count = 0
        if finn and not Document.objects.filter(member=finn, category=Document.Category.MEDICAL).exists():
            Document.objects.create(
                member=finn, name='Allergy Action Plan', category=Document.Category.MEDICAL,
                file=ContentFile(b'%PDF-1.4\n%Demo allergy action plan.\n', name='allergy_action_plan.pdf'),
                notes='Provided by GP — keep with first aid kit.',
            )
            doc_count += 1
        marcus = next((m for m in members if m.name == 'Marcus Webb'), None)
        if marcus and not Document.objects.filter(member=marcus, category=Document.Category.MEMBERSHIP).exists():
            Document.objects.create(
                member=marcus, name='Membership Agreement', category=Document.Category.MEMBERSHIP,
                file=ContentFile(b'%PDF-1.4\n%Demo membership agreement.\n', name='membership_agreement.pdf'),
            )
            doc_count += 1
        self.stdout.write(f'  Documents: {doc_count} created')

    # ── Applications ───────────────────────────────────────────────────────────

    def _create_applications(self, org):
        from members.models import MemberApplication

        apps = [
            ('Liam Fletcher',  date(2012,  8, 5),  'liam.fletcher@example.com',  '07700300001',
             'Paul Fletcher', 'paul.f@example.com', '07700400001', 'Father', 'pending', ''),
            ('Mia Winters',    date(1999,  3, 19), 'mia.winters@example.com',    '07700300002',
             '', '', '', '', 'pending', ''),
            ('Noah Davies',    date(2011, 11,  2), 'noah.davies@example.com',    '07700300003',
             'Claire Davies', 'claire.d@example.com', '07700400003', 'Mother', 'approved', ''),
            ('Olivia Grant',   date(2003,  6, 28), 'olivia.grant@example.com',   '07700300004',
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
                    'city': 'Mockingham', 'postcode': 'MK1 1AA',
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
                'email': f'{username}@mockingham-ma.example',
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
                                   coaching='MAF-L2-7823', coaching_exp=date(2026, 9, 30))
        users['dean'] = make_user('dean.okafor', 'Dean', 'Okafor', 'coach',
                                  dbs='DBS-2023-88241', dbs_exp=date(2026, 6, 1),
                                  coaching='MAF-L1-3312', coaching_exp=date(2025, 12, 31))
        users['priya'] = make_user('priya.nair', 'Priya', 'Nair', 'coach',
                                   dbs='DBS-2024-11092', dbs_exp=date(2027, 11, 20),
                                   coaching='MAF-L1-5541', coaching_exp=date(2027, 5, 10))
        self.stdout.write(f'  Users: {", ".join(users)} (password: demo1234)')
        return users
