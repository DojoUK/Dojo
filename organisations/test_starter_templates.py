from io import StringIO

from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from organisations import starter
from organisations.models import Organisation
from progression.models import ProgressionStage, ProgressionSystem, SyllabusItem, SyllabusSection


class BjjTemplateTests(TestCase):
    def setUp(self):
        self.org = Organisation.objects.create(name='Club', slug='club', features={'grades': False})

    def stages(self, system_name):
        system = ProgressionSystem.objects.get(organisation=self.org, name=system_name)
        return list(system.stages.values_list('name', flat=True))

    def test_bjj_is_listed(self):
        self.assertIn('bjj', [t['key'] for t in starter.available()])

    def test_applies_kids_and_adult_belts_with_stripes_and_turns_grades_on(self):
        created = starter.apply_template(self.org, 'bjj')
        self.assertEqual(created, {'systems': 2, 'stages': 65 + 31, 'sections': 0, 'items': 0})
        self.org.refresh_from_db()
        self.assertTrue(self.org.has_feature('grades'))
        kids, adults = self.stages('BJJ Kids Belts'), self.stages('BJJ Adult Belts')
        self.assertEqual(kids[:6], ['White Belt', 'White Belt - 1 Stripe', 'White Belt - 2 Stripes',
                                    'White Belt - 3 Stripes', 'White Belt - 4 Stripes', 'Grey-White Belt'])
        for belt in ('Grey', 'Grey-Black', 'Yellow-White', 'Yellow', 'Yellow-Black', 'Orange-White', 'Orange',
                     'Orange-Black', 'Green-White', 'Green', 'Green-Black'):
            self.assertIn(f'{belt} Belt - 4 Stripes', kids)
        for belt in ('White', 'Blue', 'Purple', 'Brown'):
            self.assertIn(f'{belt} Belt - 4 Stripes', adults)
        self.assertEqual(adults[20:], [
            'Black Belt', 'Black Belt - 1st Degree', 'Black Belt - 2nd Degree', 'Black Belt - 3rd Degree',
            'Black Belt - 4th Degree', 'Black Belt - 5th Degree', 'Black Belt - 6th Degree',
            'Red and Black Belt (7th Degree)', 'Red and White Belt (8th Degree)',
            'Red Belt (9th Degree)', 'Red Belt (10th Degree)'])

    def test_order_colours_and_default_stage(self):
        starter.apply_template(self.org, 'bjj')
        system = ProgressionSystem.objects.get(name='BJJ Adult Belts')
        ordered = list(system.stages.all())
        self.assertEqual(ordered[0].name, 'White Belt')
        self.assertEqual([s.name for s in system.stages.filter(is_default=True)], ['White Belt'])
        self.assertEqual(ProgressionStage.objects.get(system=system, name='Blue Belt - 2 Stripes').colour, '#2563EB')
        self.assertTrue(all(len(s.colour) == 7 for s in ProgressionStage.objects.filter(system__organisation=self.org)))

    def test_applying_twice_adds_nothing_and_keeps_edits(self):
        starter.apply_template(self.org, 'bjj')
        ProgressionStage.objects.filter(name='Blue Belt').update(colour='#000000')
        again = starter.apply_template(self.org, 'bjj')
        self.assertEqual(again, {'systems': 0, 'stages': 0, 'sections': 0, 'items': 0})
        self.assertEqual(ProgressionStage.objects.get(name='Blue Belt').colour, '#000000')

    def test_unknown_template_is_refused(self):
        with self.assertRaises(ValueError):
            starter.apply_template(self.org, '../secrets')

    def test_management_command(self):
        out = StringIO()
        call_command('apply_club_template', 'bjj', '--org', 'club', stdout=out)
        self.assertIn('2 progression system', out.getvalue())
        self.assertTrue(ProgressionSystem.objects.filter(organisation=self.org).count() == 2)


class JudoBjaTemplateTests(TestCase):
    def setUp(self):
        self.org = Organisation.objects.create(name='Judo Club', slug='judo-club')
        starter.apply_template(self.org, 'judo-bja')

    def stages(self, name):
        return list(ProgressionSystem.objects.get(organisation=self.org, name=name).stages.values_list('name', flat=True))

    def test_creates_the_four_bja_schemes(self):
        self.assertEqual(
            list(ProgressionSystem.objects.filter(organisation=self.org).values_list('name', flat=True)),
            ['BJA Sho Awards', 'BJA Mon Grades', 'BJA Kyu Grades', 'BJA Dan Grades'])
        self.assertTrue(self.org.has_feature('grades'))

    def test_sho_awards(self):
        sho = self.stages('BJA Sho Awards')
        self.assertEqual((len(sho), sho[0], sho[-1]), (9, '1st Sho', '9th Sho'))

    def test_mon_grades_follow_the_bja_belts_and_tags(self):
        mon = self.stages('BJA Mon Grades')
        self.assertEqual(len(mon), 19)
        self.assertEqual(mon[0], 'Novice (White Belt)')
        self.assertEqual(mon[1], '1st Mon - Red Belt + 1 Yellow Tag')
        self.assertEqual(mon[3], '3rd Mon - Red Belt + 3 Yellow Tags')
        self.assertEqual(mon[4], '4th Mon - Yellow Belt + 1 Red Tag')
        self.assertEqual(mon[10], '10th Mon - Green Belt + 1 Red Tag')
        self.assertEqual(mon[-1], '18th Mon - Brown Belt + 3 Red Tags')

    def test_kyu_and_dan_grades(self):
        self.assertEqual(self.stages('BJA Kyu Grades'), [
            'Novice (White Belt)', '6th Kyu - Red Belt', '5th Kyu - Yellow Belt', '4th Kyu - Orange Belt',
            '3rd Kyu - Green Belt', '2nd Kyu - Blue Belt', '1st Kyu - Brown Belt'])
        dan = self.stages('BJA Dan Grades')
        self.assertEqual((len(dan), dan[0], dan[4], dan[5], dan[8]),
                         (10, '1st Dan - Black Belt', '5th Dan - Black Belt', '6th Dan - Red and White Belt',
                          '9th Dan - Red Belt'))

    def test_only_mon_and_kyu_start_at_their_first_grade(self):
        defaults = {s.name for s in ProgressionStage.objects.filter(system__organisation=self.org, is_default=True)}
        self.assertEqual(defaults, {'Novice (White Belt)'})
        self.assertEqual(ProgressionStage.objects.filter(system__organisation=self.org, is_default=True).count(), 2)


class JudoBjaSyllabusTests(TestCase):
    def setUp(self):
        self.org = Organisation.objects.create(name='Judo Club', slug='judo-club')
        self.created = starter.apply_template(self.org, 'judo-bja')

    def test_creates_18_mon_and_6_kyu_checklists_and_turns_syllabus_on(self):
        self.assertEqual(self.created['sections'], 24)
        self.assertEqual(SyllabusSection.objects.filter(name__startswith='BJA Mon: ').count(), 18)
        self.assertEqual(SyllabusSection.objects.filter(name__startswith='BJA Kyu: ').count(), 6)
        self.assertEqual(SyllabusItem.objects.filter(section__organisation=self.org).count(), self.created['items'])
        self.assertTrue(self.org.has_feature('syllabus'))

    def test_each_grade_points_at_the_syllabus_for_the_next_grade(self):
        stage = lambda name: ProgressionStage.objects.get(system__organisation=self.org, name=name)
        novice = ProgressionStage.objects.get(system__name='BJA Mon Grades', name='Novice (White Belt)')
        self.assertEqual(novice.syllabus_section.name, 'BJA Mon: Novice to 1st Mon')
        novice_kyu = ProgressionStage.objects.get(system__name='BJA Kyu Grades', name='Novice (White Belt)')
        self.assertEqual(novice_kyu.syllabus_section.name, 'BJA Kyu: Novice to 6th Kyu')
        self.assertEqual(stage('1st Mon - Red Belt + 1 Yellow Tag').syllabus_section.name, 'BJA Mon: 1st to 2nd Mon')
        self.assertEqual(stage('17th Mon - Brown Belt + 2 Red Tags').syllabus_section.name, 'BJA Mon: 17th to 18th Mon')
        self.assertIsNone(stage('18th Mon - Brown Belt + 3 Red Tags').syllabus_section)
        self.assertEqual(stage('6th Kyu - Red Belt').syllabus_section.name, 'BJA Kyu: 6th to 5th Kyu')
        self.assertEqual(stage('2nd Kyu - Blue Belt').syllabus_section.name, 'BJA Kyu: 2nd to 1st Kyu')
        self.assertIsNone(stage('1st Kyu - Brown Belt').syllabus_section)
        self.assertEqual(ProgressionStage.objects.filter(system__organisation=self.org, syllabus_section__isnull=False).count(), 18 + 6)

    def test_checklist_content_and_headers(self):
        section = SyllabusSection.objects.get(name='BJA Kyu: 6th to 5th Kyu')
        self.assertIn('britishjudo.org.uk', section.content)
        self.assertEqual([s.name for s in section.subsections.all()],
                         ['Fundamental skills', 'Performance skills', 'Personal choice', 'Supplementary knowledge'])
        names = list(section.items.values_list('name', flat=True))
        self.assertIn('Tachi-waza: Tai-otoshi', names)
        self.assertIn('Kumi-kata: Demonstrate the right and left standard grips', names)
        terminology = section.items.get(name='Explain the Japanese terminology for this grade')
        self.assertIn('Tai-otoshi (Body drop throw)', terminology.description)

    def test_applying_again_adds_nothing(self):
        again = starter.apply_template(self.org, 'judo-bja')
        self.assertEqual(again, {'systems': 0, 'stages': 0, 'sections': 0, 'items': 0})


class KarateAndTaekwondoTemplateTests(TestCase):
    def setUp(self):
        self.org = Organisation.objects.create(name='Dojang', slug='dojang')

    def stages(self, system):
        return list(ProgressionSystem.objects.get(organisation=self.org, name=system).stages.values_list('name', flat=True))

    def test_both_are_offered(self):
        keys = [t['key'] for t in starter.available()]
        self.assertIn('karate-gkr', keys)
        self.assertIn('taekwondo-bt', keys)

    def test_karate_gkr_belt_journey(self):
        created = starter.apply_template(self.org, 'karate-gkr')
        self.assertEqual((created['systems'], created['stages']), (1, 15))
        grades = self.stages('GKR Karate Grades')
        self.assertEqual(grades[:4], ['White Belt (10th Kyu)', 'Yellow Tip (9th Kyu)', 'Yellow Belt (8th Kyu)', 'Orange Belt (7th Kyu)'])
        self.assertEqual(grades[7:10], ['Brown Belt (3rd Kyu)', 'Brown Belt (2nd Kyu)', 'Brown Belt (1st Kyu)'])
        self.assertEqual(grades[10], 'Black Belt - Shodan Ho')
        self.assertEqual(grades[-1], 'Black Belt - 4th Dan and above')
        system = ProgressionSystem.objects.get(name='GKR Karate Grades')
        self.assertTrue(system.assign_to_new_members)
        self.assertEqual([s.name for s in system.stages.filter(is_default=True)], ['White Belt (10th Kyu)'])
        self.org.refresh_from_db()
        self.assertTrue(self.org.has_feature('grades'))

    def test_taekwondo_kup_dan_and_poom(self):
        created = starter.apply_template(self.org, 'taekwondo-bt')
        self.assertEqual((created['systems'], created['stages']), (2, 19 + 4))
        grades = self.stages('Taekwondo Grades')
        self.assertEqual(grades[0], '10th Kup - White Belt')
        self.assertEqual(grades[1], '9th Kup - White Belt, Yellow Tag')
        self.assertEqual(grades[9], '1st Kup - Red Belt, Black Tag')
        self.assertEqual(grades[10], '1st Dan - Black Belt')
        self.assertEqual(grades[-1], '9th Dan - Black Belt (Grand Master)')
        self.assertEqual(len(grades), 19)
        self.assertEqual(self.stages('Taekwondo Poom (14 and under)')[0], '1st Poom - Black and Red Belt')
        self.assertFalse(ProgressionStage.objects.filter(system__name__startswith='Taekwondo Poom', is_default=True).exists())
        self.assertEqual([s.name for s in ProgressionStage.objects.filter(system__name='Taekwondo Grades', is_default=True)],
                         ['10th Kup - White Belt'])


class SetupWizardTemplateTests(TestCase):
    def post(self, **extra):
        return self.client.post(reverse('setup'), {
            'org_name': 'Jits Club', 'username': 'boss', 'password': 'We@vers3!x', 'password_confirm': 'We@vers3!x',
            **extra,
        })

    def test_choosing_bjj_prefills_the_belts(self):
        self.post(club_template='bjj')
        org = Organisation.objects.get()
        self.assertEqual(ProgressionSystem.objects.filter(organisation=org).count(), 2)
        self.assertTrue(org.has_feature('grades'))

    def test_start_empty_adds_nothing(self):
        self.post(club_template='')
        self.assertFalse(ProgressionSystem.objects.exists())

    def test_setup_page_offers_the_template(self):
        self.assertContains(self.client.get(reverse('setup')), 'Brazilian Jiu-Jitsu')
