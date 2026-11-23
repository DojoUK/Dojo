"""Every "are you sure?" question uses Dojo's confirm dialog (static/js/confirm.js), not the browser's box."""
import pathlib
import re

from django.conf import settings
from django.test import SimpleTestCase

TEMPLATES = pathlib.Path(settings.BASE_DIR) / 'templates'
LAYOUTS = ('base.html', 'portal/base.html', 'network/base.html')


class ConfirmDialogTests(SimpleTestCase):
    def test_no_template_uses_the_browsers_confirm_box(self):
        offenders = [
            f'{path.relative_to(TEMPLATES)}:{number}'
            for path in TEMPLATES.rglob('*.html')
            for number, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1)
            if re.search(r'(?<!\w)confirm\(', line)
        ]
        self.assertEqual(offenders, [], 'Ask with data-confirm="…" or window.dojoConfirm() instead.')

    def test_every_layout_loads_the_dialog(self):
        for layout in LAYOUTS:
            path = TEMPLATES / layout
            if not path.exists():           # not every branch has every layout
                continue
            with self.subTest(layout=layout):
                text = path.read_text(encoding='utf-8')
                self.assertIn("js/confirm.js", text)
                self.assertIn("css/confirm.css", text)

    def test_messages_are_plain_text_not_javascript(self):
        for path in TEMPLATES.rglob('*.html'):
            for message in re.findall(r'data-confirm="([^"]*)"', path.read_text(encoding='utf-8')):
                with self.subTest(template=str(path.relative_to(TEMPLATES)), message=message[:60]):
                    self.assertNotIn('\\', message)
                    self.assertNotIn('escapejs', message)
