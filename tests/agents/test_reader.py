"""Reader tests moved from Kormic: real readers and evidence checks, fake model answers."""

import io
import json
import shutil
import tempfile
import zipfile
from pathlib import Path
from unittest import TestCase as SimpleTestCase
from unittest.mock import patch

from PIL import Image, ImageDraw, ImageFont

from agents_service.agents.cv.evidence import ResumeFacts, supported_resume
from agents_service.documents.reading import read_document
from tests.agents.kormic_adapter import extract

TEXT = """Test Person
other@example.com
Education
BS Nursing Dayton University 2012 2016
MS Nursing Ohio University 2018 2020
Experience
Nurse Dayton Clinic 2016 2018
Lead Nurse Ohio Clinic 2020 Present
Skills
Patient care
"""
FACTS = {
    'name': 'Test Person',
    'email': 'other@example.com',
    'skills': ['Patient care'],
    'education': [
        {
            'title': 'BS Nursing',
            'organization': 'Dayton University',
            'start_date': '2012',
            'end_date': '2016',
            'evidence': 'BS Nursing Dayton University 2012 2016',
        },
        {
            'title': 'MS Nursing',
            'organization': 'Ohio University',
            'start_date': '2018',
            'end_date': '2020',
            'evidence': 'MS Nursing Ohio University 2018 2020',
        },
    ],
    'work_experience_entries': [
        {
            'title': 'Nurse',
            'organization': 'Dayton Clinic',
            'start_date': '2016',
            'end_date': '2018',
            'evidence': 'Nurse Dayton Clinic 2016 2018',
        },
        {
            'title': 'Lead Nurse',
            'organization': 'Ohio Clinic',
            'start_date': '2020',
            'end_date': 'Present',
            'evidence': 'Lead Nurse Ohio Clinic 2020 Present',
        },
    ],
}


def docx():
    data = io.BytesIO()
    with zipfile.ZipFile(data, 'w') as z:
        z.writestr(
            zipfile.ZipInfo('[Content_Types].xml'),
            '<Types><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>',
        )
        z.writestr(
            zipfile.ZipInfo('word/document.xml'),
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
            + ''.join('<w:p><w:r><w:t>' + line + '</w:t></w:r></w:p>' for line in TEXT.splitlines())
            + '</w:body></w:document>',
        )
    return data.getvalue()


class ReaderTests(SimpleTestCase):
    def test_docx_full_record_mapping_and_unsupported_field_removed(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'cv.docx'
            path.write_bytes(docx())
            facts = {**FACTS, 'phone': '5559999999', 'summary': 'Invented summary', 'gpa': 4.0}
            result = extract([path], 'cv', {'full_name': 'Test Person'}, lambda *_: facts)
        self.assertEqual(result['status'], 'ready')
        self.assertEqual(len(result['records']), 7)
        self.assertEqual(
            [r['details']['title'] for r in result['records'] if r['section'] == 'education'],
            ['BS Nursing', 'MS Nursing'],
        )
        self.assertFalse(any(r['section'] in ('phone', 'summary', 'gpa') for r in result['records']))
        self.assertTrue(all(r['sources'][0]['evidence'] for r in result['records']))

    def test_cv_and_linkedin_return_the_owner_name_with_its_sources(self):
        # Kormic compared this name with the profile; the agent only returns it.
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'cv.docx'
            path.write_bytes(docx())
            for kind in ('cv', 'linkedin'):
                facts = (
                    FACTS if kind == 'cv' else {'name': {'value': 'Test Person', 'evidence': 'Test Person'}}
                )
                with self.subTest(kind=kind):
                    with patch('tests.agents.kormic_adapter.read_document', return_value=TEXT):
                        result = extract([path], kind, {}, lambda *_, facts=facts: facts)
                    self.assertEqual(result['status'], 'ready')
                    names = [r for r in result['records'] if r['section'] == 'name']
                    self.assertEqual(names[0]['value'], 'Test Person')
                    self.assertTrue(names[0]['sources'][0]['evidence'])

    def test_fabricated_record_and_field_cannot_pass_quote_check(self):
        data = ResumeFacts.model_validate(
            {
                'education': [
                    {
                        'title': 'MS Nursing',
                        'organization': 'Fake University',
                        'evidence': 'MS Nursing Ohio University 2018 2020',
                    },
                    {'title': 'Doctorate', 'evidence': 'Doctorate from Fake University'},
                ]
            }
        ).model_dump()
        result = supported_resume(data, TEXT)
        self.assertEqual(len(result['education']), 1)
        self.assertIsNone(result['education'][0]['organization'])

    def test_linkedin_conflict_partial_and_duplicate_preserve_sources(self):
        with tempfile.TemporaryDirectory() as root:
            a = Path(root) / 'a.png'
            b = Path(root) / 'b.png'
            a.write_bytes(b'a')
            b.write_bytes(b'b')

            def provider(text, _):
                return {'name': {'value': text.splitlines()[0], 'evidence': text.splitlines()[0]}}

            with patch(
                'tests.agents.kormic_adapter.read_document',
                side_effect=[
                    'Test Person\nEngineer at ACME\nAbout\nHello',
                    'Other Person\nEngineer at ACME\nAbout\nHello',
                ],
            ):
                result = extract([a, b], 'linkedin', {'full_name': 'Test Person'}, provider)
            # Two different owner names: both are returned for the project to judge.
            self.assertEqual(
                sorted(r['value'] for r in result['records'] if r['section'] == 'name'),
                ['Other Person', 'Test Person'],
            )
            with patch(
                'tests.agents.kormic_adapter.read_document',
                side_effect=['Test Person\nEngineer at ACME\nAbout\nHello', RuntimeError('secret')],
            ):
                result = extract([a, b], 'linkedin', {'full_name': 'Test Person'}, provider)
            self.assertEqual(result['status'], 'partial')
            self.assertNotIn('secret', json.dumps(result))
            with patch(
                'tests.agents.kormic_adapter.read_document',
                return_value='Test Person\nEngineer at ACME\nAbout\nHello',
            ):
                result = extract([a, b], 'linkedin', {'full_name': 'Test Person'}, provider)
            self.assertEqual(len(result['records']), 1)
            self.assertEqual(len(result['records'][0]['sources']), 2)

    def test_linkedin_employment_dates_run_without_missing_import(self):
        from agents_service.agents.linkedin.validation import validate_data

        value = validate_data(
            {
                'experiences': [
                    {
                        'title': 'Nurse',
                        'company': 'Dayton Clinic',
                        'start_date': '2016',
                        'end_date': '2018',
                        'evidence': 'Nurse\nDayton Clinic\n2016 - 2018',
                    }
                ]
            },
            'Experience\nNurse\nDayton Clinic\n2016 - 2018',
        )
        self.assertEqual(len(value['experiences']), 1)

    def test_real_image_and_scanned_pdf_ocr(self):
        if not shutil.which('tesseract'):
            self.skipTest('Tesseract not installed')
        with tempfile.TemporaryDirectory() as root:
            image = Image.new('RGB', (1400, 600), 'white')
            font = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf', 36)
            ImageDraw.Draw(image).multiline_text(
                (40, 40),
                'Test Person\nEducation\nDayton University\nBS Nursing 2016',
                font=font,
                fill='black',
                spacing=15,
            )
            for ext in ('png', 'pdf'):
                path = Path(root) / ('scan.' + ext)
                image.save(path)
                text = read_document(path)
                self.assertIn('Dayton University', text)
                self.assertIn('Nursing', text)


class NullableResumeTests(SimpleTestCase):
    def test_missing_collections_and_technologies_do_not_discard_valid_facts(self):
        facts = {
            **FACTS,
            'projects': None,
            'links': None,
            'warnings': None,
            'education': [{**FACTS['education'][0], 'technologies': None}],
        }
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'cv.docx'
            path.write_bytes(docx())
            result = extract([path], 'cv', {'full_name': 'Test Person'}, lambda *_: facts)
        self.assertEqual(result['status'], 'ready')
        education = [r for r in result['records'] if r['section'] == 'education']
        self.assertEqual(education[0]['details']['title'], 'BS Nursing')
        self.assertEqual(education[0]['details']['technologies'], [])
        self.assertTrue(any(r['section'] == 'skills' for r in result['records']))

    def test_invalid_field_types_still_fail_with_private_safe_diagnostics(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'cv.docx'
            path.write_bytes(docx())
            result = extract([path], 'cv', {}, lambda *_: {'skills': {'secret-name': 'secret-value'}})
        self.assertEqual(result['status'], 'failed')
        failure = result['files'][0]
        self.assertIn('unsupported format', failure['error'])
        self.assertEqual(failure['diagnostic']['validation_types'], ['list_type'])
        self.assertNotIn('secret', json.dumps(result))


class ExtraProviderFieldsTests(SimpleTestCase):
    def test_unknown_fields_are_excluded_but_known_facts_survive(self):
        facts = {
            **FACTS,
            'provider_metadata': 'must not be stored',
            'education': [{**FACTS['education'][0], 'confidence': 0.9}],
        }
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'cv.docx'
            path.write_bytes(docx())
            result = extract([path], 'cv', {'full_name': 'Test Person'}, lambda *_: facts)
        self.assertEqual(result['status'], 'ready')
        self.assertNotIn('provider_metadata', json.dumps(result))
        self.assertNotIn('confidence', json.dumps(result))
        self.assertTrue(any(r['section'] == 'education' for r in result['records']))
        self.assertTrue(any(r['section'] == 'skills' for r in result['records']))

    def test_ignoring_unknown_keys_does_not_accept_invalid_known_fields(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'cv.docx'
            path.write_bytes(docx())
            result = extract(
                [path],
                'cv',
                {},
                lambda *_: {
                    'extra': 'ignored',
                    'education': [{'title': {'invalid': 'object'}, 'evidence': 'Test Person'}],
                },
            )
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['records'], [])
        self.assertIn('string_type', result['files'][0]['diagnostic']['validation_types'])


class HeadlineConsolidationTests(SimpleTestCase):
    def extract_headlines(self, headlines):
        with tempfile.TemporaryDirectory() as root:
            paths = []
            for i in range(len(headlines)):
                path = Path(root) / f'{i}.png'
                path.write_bytes(f'screenshot {i}'.encode())
                paths.append(path)

            def provider(text, schema):
                return {'headline': {'value': text, 'evidence': text}}

            with patch('tests.agents.kormic_adapter.read_document', side_effect=headlines):
                return extract(paths, 'linkedin', {}, provider)

    def test_three_formatting_variants_merge_with_all_evidence(self):
        headlines = [
            "B.E, IT '28 | AI/ML Developer Intern @ One Smarter, Inc. | Deep Learning, Generative AI & Full-Stack Development",
            "BE. IT '28 | AI/ML Developer Intern @ One Smarter, Inc. | Deep Learning, Generative AI & Full-Stack Development",
            'B.E, IT ’28 | AI/ML Developer Intern @ One Smarter, Inc. | Deep Learning, Generative AI & Full-Stack Development',
        ]
        result = self.extract_headlines(headlines)
        self.assertEqual(result['status'], 'ready')
        self.assertEqual(len(result['records']), 1)
        record = result['records'][0]
        self.assertEqual(record['value'], headlines[0])
        self.assertEqual([s['evidence'] for s in record['sources']], headlines)
        self.assertEqual([s['file_index'] for s in record['sources']], [0, 1, 2])
        self.assertEqual(len({s['sha256'] for s in record['sources']}), 3)
        reverse = self.extract_headlines(list(reversed(headlines)))
        self.assertEqual(record['id'], reverse['records'][0]['id'])

    def test_different_wording_remains_separate_with_review_warning(self):
        result = self.extract_headlines(
            [
                'AI/ML Developer Intern at Acme',
                'AI / ML Developer Intern at Acme',
                'AI/ML Developer at Acme',
            ]
        )
        self.assertEqual(len(result['records']), 2)
        self.assertEqual(len(result['records'][0]['sources']), 2)
        self.assertEqual(len(result['records'][1]['sources']), 1)
        self.assertTrue(any('Different headline wording' in w for w in result['warnings']))

    def test_meaningful_symbols_numbers_and_words_are_not_merged(self):
        from agents_service.agents.records import headline_key

        for a, b in [
            ('C Developer', 'C++ Developer'),
            ('C# Developer', 'C Developer'),
            ('Engineer 2026', 'Engineer 2028'),
            ('.NET Developer', 'NET Developer'),
            ('Python 3.1', 'Python 31'),
            ('Full-Stack Developer', 'Full Stack Developer'),
            ('Engineer at Acme', 'Engineer at Other'),
            ('AI Developer', 'Not an AI Developer'),
        ]:
            with self.subTest(a=a, b=b):
                self.assertNotEqual(headline_key(a), headline_key(b))


class DocxLineBreakTests(SimpleTestCase):
    def test_line_breaks_and_tabs_separate_text_and_tab_stops_are_ignored(self):
        import tempfile
        import zipfile
        from pathlib import Path

        from agents_service.documents.reading import read_document

        w = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
        body = (
            f'<w:document {w}><w:body>'
            '<w:p><w:pPr><w:tabs><w:tab w:val="left" w:pos="720"/></w:tabs></w:pPr>'
            '<w:r><w:t>Example University | 2018 - May 2022</w:t></w:r>'
            '<w:r><w:br/><w:t>Columbus, Ohio | GPA: 3.6</w:t></w:r></w:p>'
            '<w:p><w:r><w:t>Skills</w:t><w:tab/><w:t>Python, SQL</w:t></w:r></w:p>'
            '</w:body></w:document>'
        )
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'cv.docx'
            with zipfile.ZipFile(path, 'w') as archive:
                archive.writestr('word/document.xml', body)
            text = read_document(path)
        self.assertIn('2018 - May 2022\nColumbus, Ohio', text)
        self.assertNotIn('2022Columbus', text)
        self.assertNotIn('\t2018', text)
        self.assertIn('Skills', text)
        self.assertIn('Python, SQL', text)
        self.assertNotIn('SkillsPython', text)
