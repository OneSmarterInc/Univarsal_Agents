from unittest import TestCase as SimpleTestCase

from pydantic import ValidationError

from agents_service.agents.cv.evidence import supported_resume
from agents_service.agents.cv.schemas import ResumeFacts
from agents_service.agents.cv.source_education import (
    date_candidates,
    materialize,
    numbered,
    selection_schema,
    tokens,
)
from agents_service.ai.providers import AIError


def selection(source, title, organization=None, start_date=None, end_date=None):
    items = tokens(source)

    def span(value):
        if value is None:
            return None
        offset = source.index(value)
        return {
            'first': next(i + 1 for i, m in enumerate(items) if m.start() == offset),
            'last': next(i + 1 for i, m in enumerate(items) if m.end() == offset + len(value)),
        }

    dates = date_candidates(source)

    def date_id(value):
        return (
            next((f'D{i + 1}' for i, m in enumerate(dates) if m.group() == value), 'D999') if value else None
        )

    return {
        k: date_id(v) if k.endswith('date') else span(v)
        for k, v in locals().copy().items()
        if k in ('title', 'organization', 'start_date', 'end_date')
    }


class SourceEducationTests(SimpleTestCase):
    def test_inline_and_multiline_exact_source(self):
        for separator in (', ', '\n'):
            source = (
                'Education: BS Computer Science'
                + separator
                + 'Example University'
                + separator
                + '2020 - 2024'
            )
            record = selection(source, 'BS Computer Science', 'Example University', '2020', '2024')
            result = materialize(source, {'education': [record]})
            self.assertEqual(result[0]['title'], 'BS Computer Science')
            self.assertEqual(result[0]['organization'], 'Example University')
            self.assertEqual(result[0]['end_date'], '2024')
            self.assertIn(result[0]['evidence'], source)
            self.assertEqual(
                supported_resume(ResumeFacts(education=result).model_dump(), source)['education'],
                result,
            )

    def test_multiple_qualifications_missing_dates_and_duplicates(self):
        source = 'Education\nBS Physics, First University, 2020 - 2024\nMS Physics, Second University'
        first = selection(source, 'BS Physics', 'First University', '2020', '2024')
        second = selection(source, 'MS Physics', 'Second University')
        result = materialize(source, {'education': [first, second, first]})
        self.assertEqual(len(result), 2)
        self.assertIsNone(result[1]['end_date'])
        self.assertNotIn('Second University', result[0]['evidence'])

    def test_generated_values_and_bad_references_fail(self):
        source = 'BS Physics, University'
        for value in (True, '1', 0, 999):
            record = selection(source, 'BS Physics', 'University')
            record['title']['first'] = value
            with self.subTest(value=value), self.assertRaises((ValidationError, AIError)):
                materialize(source, {'education': [record]})
        record = selection(source, 'BS Physics', 'University')
        record['evidence'] = 'https://invented.example'
        with self.assertRaises(ValidationError):
            materialize(source, {'education': [record]})

    def test_wrong_dates_and_cross_record_associations_fail(self):
        source = 'BS Physics, First University, 2020\nMS Physics, Second University, 2024'
        first = selection(source, 'BS Physics', 'Second University', '2020')
        second = selection(source, 'MS Physics', 'First University', None, '2024')
        with self.assertRaises(AIError):
            materialize(source, {'education': [first, second]})
        record = selection(source, 'BS Physics', 'First University', 'MS Physics')
        with self.assertRaises((AIError, ValidationError)):
            materialize(source, {'education': [record]})

    def test_warning_preserved_and_deduplicated_on_revalidation(self):
        facts = ResumeFacts(education=[{'title': 'Invented', 'evidence': 'BS Physics'}]).model_dump()
        result = supported_resume(facts, 'BS Physics')
        self.assertFalse(result['education'])
        self.assertTrue(result['warnings'])
        warnings = result['warnings'][:]
        self.assertEqual(supported_resume(result, 'BS Physics')['warnings'], warnings)

    def test_section_boundaries_resource_limits_and_overlapping_fields(self):
        for source in (
            'BS Physics\n\nExample University',
            'BS Physics\nExperience:\nExample University',
        ):
            with self.subTest(source=source), self.assertRaises(AIError):
                materialize(source, {'education': [selection(source, 'BS Physics', 'Example University')]})
        with self.assertRaises(AIError):
            materialize('word ' * 701, {'education': []})
        source = 'BS Physics, University'
        with self.assertRaises(AIError):
            materialize(source, {'education': [selection(source, 'BS Physics', 'Physics')]})

    def test_month_precision_and_absent_education(self):
        source = 'BS Physics, University, Aug 2018 - May 2022'
        record = materialize(
            source,
            {'education': [selection(source, 'BS Physics', 'University', 'Aug 2018', 'May 2022')]},
        )[0]
        self.assertEqual(record['start_date'], 'Aug 2018')
        self.assertEqual(record['end_date'], 'May 2022')
        self.assertIsNone(record['description'])
        self.assertEqual(materialize('Skills: Python', {'education': []}), [])

    def test_reported_token_boundary_failure_uses_date_ids(self):
        source = 'Test Candidate\nSkills: Python, Excel\nEducation: BS Computer Science, Example University, 2020 - 2024'
        record = {
            'title': {'first': 10, 'last': 12},
            'organization': {'first': 14, 'last': 15},
            'start_date': 'D1',
            'end_date': 'D2',
        }
        self.assertEqual([m.group() for m in date_candidates(source)], ['2020', '2024'])
        result = materialize(source, {'education': [record]})[0]
        self.assertEqual((result['start_date'], result['end_date']), ('2020', '2024'))
        for invalid in ({'first': 17, 'last': 18}, True, '1', 0, 3):
            with self.subTest(invalid=invalid), self.assertRaises((AIError, ValidationError)):
                materialize(source, {'education': [dict(record, start_date=invalid)]})

    def test_dates_keep_locations_and_do_not_extract_iso_fragments(self):
        self.assertEqual([m.group() for m in date_candidates('2020-2024')], ['2020', '2024'])
        source = '2020-07-01 01/07/2020 12020 20201 Aug 2018 - May 2022 Present'
        self.assertEqual([m.group() for m in date_candidates(source)], ['Aug 2018', 'May 2022', 'Present'])
        source = 'BS Physics, A University, 2020 - 2024\nMS Physics, B University, 2020 - 2024'
        self.assertEqual(len(date_candidates(source)), 4)
        first = selection(source, 'BS Physics', 'A University', '2020', '2024')
        second = selection(source, 'MS Physics', 'B University')
        second.update(start_date='D3', end_date='D4')
        result = materialize(source, {'education': [first, second]})
        self.assertEqual(len(result), 2)
        with self.assertRaises(AIError):
            materialize(source, {'education': [dict(first, end_date='D4'), second]})

    def test_date_schema_uses_only_source_ids_and_rejects_reported_numeric_ids(self):
        source = 'Test Candidate\nSkills: Python, Excel\nEducation: BS Computer Science, Example University, 2020 - 2024'
        schema = selection_schema(source)
        definition = schema.model_json_schema()['$defs']['EducationSelection']
        self.assertEqual(definition['properties']['start_date']['anyOf'][0]['enum'], ['D1', 'D2'])
        self.assertIn('D1: 2020', numbered(source))
        self.assertIn('D2: 2024', numbered(source))
        record = selection(source, 'BS Computer Science', 'Example University', '2020', '2024')
        for bad in (17, 19, '17', '19', 'D17', 'D0', 'D01', 'd1', True, {'first': 17, 'last': 18}):
            with self.subTest(bad=bad), self.assertRaises(ValidationError):
                schema.model_validate({'education': [dict(record, start_date=bad)]})
        self.assertEqual(materialize(source, {'education': [record]})[0]['end_date'], '2024')

    def test_date_free_source_only_permits_null_and_schema_is_not_stale(self):
        source = 'BS Physics, University'
        record = selection(source, 'BS Physics', 'University')
        self.assertIsNone(materialize(source, {'education': [record]})[0]['start_date'])
        for bad in ('D1', 1):
            with self.assertRaises(ValidationError):
                selection_schema(source).model_validate({'education': [dict(record, start_date=bad)]})
        dated = source + ', 2020'
        with self.assertRaises(ValidationError):
            selection_schema(dated).model_validate({'education': [dict(record, start_date='D2')]})
        self.assertEqual(
            materialize(dated, {'education': [dict(record, start_date='D1')]})[0]['start_date'],
            '2020',
        )

    def test_reported_university_separator_is_removed_without_changing_evidence(self):
        source = 'Test Candidate\nSkills: Python, Excel\nEducation: BS Computer Science, Example University, 2020 - 2024'
        record = {
            'title': {'first': 10, 'last': 12},
            'organization': {'first': 14, 'last': 16},
            'start_date': 'D1',
            'end_date': 'D2',
        }
        result = materialize(source, {'education': [record]})[0]
        self.assertEqual(result['organization'], 'Example University')
        self.assertEqual(result['evidence'], 'BS Computer Science, Example University, 2020 - 2024')
        checked = supported_resume(ResumeFacts(education=[result]).model_dump(), source)
        self.assertEqual(checked['education'], [result])
        self.assertFalse(checked['warnings'])

    def test_degree_separator_and_internal_name_punctuation(self):
        source = "B.Sc., St. Anne's University, West Campus, 2020 - 2024"
        record = selection(source, 'B.Sc.,', "St. Anne's University, West Campus,", '2020', '2024')
        result = materialize(source, {'education': [record]})[0]
        self.assertEqual(result['title'], 'B.Sc.')
        self.assertEqual(result['organization'], "St. Anne's University, West Campus")
        self.assertEqual(result['evidence'], source)

    def test_unproven_commas_and_other_punctuation_are_not_stripped(self):
        for source, name in [
            ('BS Physics, Example University,', 'Example University,'),
            ('BS Physics, Example University,\n2020', 'Example University,'),
            ('BS Physics, Example University. 2020', 'Example University.'),
            ('BS Physics, Example University, GPA 3.5 2020', 'Example University,'),
        ]:
            with self.subTest(source=source):
                record = selection(source, 'BS Physics', name, '2020' if '2020' in source else None)
                self.assertEqual(materialize(source, {'education': [record]})[0]['organization'], name)

    def test_separator_only_selection_is_not_accepted_as_a_name(self):
        source = 'BS Physics, 2020'
        record = selection(source, ',', None, '2020')
        with self.assertRaises(AIError):
            materialize(source, {'education': [record]})


class LayoutEducationTests(SimpleTestCase):
    """Common education layouts are read without a model; unclear ones are not."""

    def fields(self, records):
        return [(r['title'], r['organization'], r['start_date'], r['end_date']) for r in records]

    def test_reported_docx_layout_and_acceptance_line(self):
        from agents_service.agents.cv.source_education import layout_education

        source = (
            'Education\nBachelor of Science in Health Informatics\n'
            'Riverstone College of Technology | August 2018 - May 2022\n'
            'Columbus, Ohio, United States | GPA: 3.6 / 4.0\nSkills\nPython, SQL'
        )
        self.assertEqual(
            self.fields(layout_education(source)),
            [
                (
                    'Bachelor of Science in Health Informatics',
                    'Riverstone College of Technology',
                    'August 2018',
                    'May 2022',
                )
            ],
        )
        line = 'Test Candidate\nSkills: Python, Excel\nEducation: BS Computer Science, Example University, 2020 - 2024'
        self.assertEqual(
            self.fields(layout_education(line)),
            [('BS Computer Science', 'Example University', '2020', '2024')],
        )

    def test_multiple_qualifications_and_evidence_is_source_text(self):
        from agents_service.agents.cv.source_education import layout_education

        source = (
            'Education\nMaster of Science in Data Science\nExample University\n2019 - 2021\n\n'
            'B.Tech in Computer Engineering, Sample Institute of Technology, 2015 - 2019\nSkills\nPython'
        )
        records = layout_education(source)
        self.assertEqual(len(records), 2)
        self.assertTrue(all(r['evidence'] in source for r in records))
        self.assertNotIn('Skills', records[1]['evidence'])

    def test_no_education_returns_empty_and_unclear_returns_none(self):
        from agents_service.agents.cv.source_education import layout_education

        self.assertEqual(
            layout_education(
                'Summary\nI want to be a leader.\nWork Experience\nAnalyst, ran an MBA project\n2020 - 2021'
            ),
            [],
        )
        self.assertIsNone(layout_education('Education\nRandom text only'))
        self.assertIsNone(layout_education('Education\nBachelor of Arts\n2010 2012 2014'))


class LocalEducationFallbackTests(SimpleTestCase):
    SOURCE = (
        'Education\nBachelor of Science in Health Informatics\n'
        'Riverstone College of Technology | August 2018 - May 2022'
    )

    def run_local(self, reply):
        from unittest.mock import patch

        from agents_service.agents.cv.local_reader import extract_local_resume
        from agents_service.ai.providers import Completion

        with (
            patch('agents_service.agents.cv.local_reader.extract_owner', return_value=[]),
            patch('agents_service.agents.cv.local_reader.GROUPS', [('education',)]),
            patch('agents_service.agents.cv.local_reader.chunks', return_value=[self.SOURCE]),
            patch('agents_service.agents.cv.local_reader.provider') as factory,
        ):
            factory.return_value.__enter__.return_value.generate.return_value = Completion(data=reply)
            return extract_local_resume(self.SOURCE, {})['education']

    def test_wrong_span_empty_and_generated_values_use_layout(self):
        for reply in (
            {
                'education': [
                    {
                        'title': {'first': 1, 'last': 30},
                        'organization': None,
                        'start_date': None,
                        'end_date': None,
                    }
                ]
            },
            {'education': []},
            {
                'education': [
                    {
                        'title': 'Bachelor',
                        'organization': 'X',
                        'start_date': '2018',
                        'end_date': '2022',
                    }
                ]
            },
        ):
            with self.subTest(reply=reply):
                records = self.run_local(reply)
                self.assertEqual(len(records), 1)
                self.assertEqual(records[0]['organization'], 'Riverstone College of Technology')
                self.assertEqual(
                    (records[0]['start_date'], records[0]['end_date']), ('August 2018', 'May 2022')
                )
