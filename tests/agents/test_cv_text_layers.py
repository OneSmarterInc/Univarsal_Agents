"""PDF text layers and source checks that must hold for any CV layout."""

import tempfile
from pathlib import Path
from unittest import TestCase as SimpleTestCase

from agents_service.agents.cv.owner import supported_owner
from agents_service.documents.matching import evidence_supported
from agents_service.documents.reading import best_pdf_text, split_words


def simple_pdf(lines):
    """A one-page PDF with one text line per entry."""
    text = ' '.join(f'({line}) Tj 0 -20 Td' for line in lines)
    stream = f'BT /F1 12 Tf 50 750 Td {text} ET'.encode()
    objects = [
        b'<< /Type /Catalog /Pages 2 0 R >>',
        b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
        b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>',
        b'<< /Length %d >>\nstream\n' % len(stream) + stream + b'\nendstream',
        b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
    ]
    out, offsets = bytearray(b'%PDF-1.4\n'), []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += b'%d 0 obj\n' % number + body + b'\nendobj\n'
    xref = len(out)
    out += b'xref\n0 %d\n0000000000 65535 f \n' % (len(objects) + 1)
    out += b''.join(b'%010d 00000 n \n' % o for o in offsets)
    out += b'trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n' % (
        len(objects) + 1,
        xref,
    )
    return bytes(out)


class TextLayerTests(SimpleTestCase):
    def pdf(self, lines):
        folder = tempfile.mkdtemp()
        path = Path(folder) / 'cv.pdf'
        path.write_bytes(simple_pdf(lines))
        return path

    def test_split_words_are_counted(self):
        self.assertEqual(split_words('D eveloped D ermAI Monitor, Classi f ication'), 3)
        self.assertEqual(split_words('Python, Java and SQL for a data team'), 0)

    def test_pdfium_text_is_used_when_pypdf_split_words_and_joined_lines(self):
        path = self.pdf(['JANE DOE', 'B.E. Information Technology', 'Developed a classification tool'])
        broken = 'JANE DOEB.E. Information Technology\nD eveloped a classi f ication tool'
        chosen = best_pdf_text(path, [broken])[0]
        self.assertIn('JANE DOE\n', chosen)
        self.assertIn('Developed a classification tool', chosen)

    def test_pypdf_text_is_kept_when_pdfium_lost_text(self):
        path = self.pdf(['Jane Doe'])
        fuller = 'Jane Doe\nData Analyst at Acme with five years of reporting work'
        self.assertEqual(best_pdf_text(path, [fuller])[0], fuller)

    def test_quote_with_split_words_is_still_supported(self):
        source = 'D eveloped D ermAI Monitor: model training and deployment'
        self.assertTrue(evidence_supported('Developed DermAI Monitor: model training', source))
        # Short quotes still need an exact match.
        self.assertFalse(evidence_supported('JavaScript', 'Java Script'))
        self.assertFalse(evidence_supported('Built a payments platform', source))

    def test_owner_name_can_share_its_header_line(self):
        for header in (
            'Jane Doe | Data Analyst',
            'Jane Doe, MBA',
            'Jane Doe – Software Engineer',
            'Jane Doe • Pune',
        ):
            source = header + '\njane@example.com\nEXPERIENCE\nAnalyst at Acme'
            self.assertTrue(supported_owner('Jane Doe', header + '\njane@example.com', source), header)
        source = 'Jane Doe\nEXPERIENCE\nWorked with John Smith | Acme'
        self.assertFalse(supported_owner('John Smith', 'Worked with John Smith | Acme', source))


class DashAndEducationTests(SimpleTestCase):
    def test_dropped_dashes_come_back_from_the_other_layer(self):
        from agents_service.documents.reading import restore_dashes

        kept = 'HSC 2023–2024 | SSC 2021–2022 — School of Scholars\nROC-AUC, Sem 1–4'
        lost = 'HSC 2023 2024 | SSC 2021 2022 School of Scholars\nROC AUC, Sem 1 4'
        self.assertEqual(restore_dashes(lost, kept), kept)
        # Nothing but dashes is taken from the other layer.
        self.assertEqual(restore_dashes('Jane Doe 2020', 'Jane X Doe 2020'), 'Jane Doe 2020')

    def test_several_qualifications_on_one_line(self):
        from agents_service.agents.cv.source_education import layout_education

        records = layout_education(
            'EDUCATION\nB.E. in Information Technology\nExample College of Engineering\n\n'
            'HSC 2023–2024 | SSC 2021–2022 — School of Scholars\nSKILLS\nPython'
        )
        self.assertEqual(
            [(r['title'], r['organization'], r['start_date'], r['end_date']) for r in records],
            [
                ('B.E. in Information Technology', 'Example College of Engineering', None, None),
                ('HSC', None, '2023', '2024'),
                ('SSC', 'School of Scholars', '2021', '2022'),
            ],
        )


class SectionFailureTests(SimpleTestCase):
    def test_one_unreadable_section_keeps_the_rest_of_the_cv(self):
        from unittest.mock import patch

        from agents_service.agents.cv.local_reader import extract_local_resume
        from agents_service.ai.providers import Completion
        from tests.agents.kormic_adapter import configuration

        source = 'Jane Doe\njane@example.com\nEDUCATION\nRandom text only'
        with (
            patch(
                'agents_service.agents.cv.local_reader.extract_owner',
                return_value=[{'value': 'Jane Doe', 'evidence': 'Jane Doe'}],
            ),
            patch(
                'agents_service.agents.cv.local_reader.plan',
                return_value=[(('email', 'phone', 'location'), source), (('education',), source)],
            ),
            patch('agents_service.agents.cv.local_reader.provider') as factory,
        ):
            factory.return_value.__enter__.return_value.generate.side_effect = [
                Completion(data={'email': 'jane@example.com', 'phone': None, 'location': None}),
                Completion(
                    data={
                        'education': [
                            {
                                'title': {'first': 1, 'last': 2},
                                'organization': {'first': 3, 'last': 9},
                                'start_date': None,
                                'end_date': None,
                            }
                        ]
                    }
                ),
            ]
            result = extract_local_resume(source, configuration('extraction'))
        self.assertEqual(result['email'], 'jane@example.com')
        self.assertEqual(result['education'], [])
        self.assertTrue(any('education details could not be read' in w for w in result['warnings']))

    def test_whole_file_problems_still_stop(self):
        from unittest.mock import patch

        from agents_service.agents.cv.local_reader import extract_local_resume
        from agents_service.ai.providers import AIError
        from tests.agents.kormic_adapter import configuration

        with (
            patch('agents_service.agents.cv.local_reader.extract_owner', return_value=[]),
            patch(
                'agents_service.agents.cv.local_reader.plan',
                return_value=[(('summary',), 'Jane Doe')],
            ),
            patch('agents_service.agents.cv.local_reader.provider') as factory,
        ):
            factory.return_value.__enter__.return_value.generate.side_effect = AIError('timeout')
            with self.assertRaises(AIError):
                extract_local_resume('Jane Doe', configuration('extraction'))
