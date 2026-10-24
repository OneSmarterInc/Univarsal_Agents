"""Reproduce real local-model fragment and sparse-record responses safely."""

import time
from unittest import TestCase as SimpleTestCase
from unittest.mock import patch

from agents_service.agents.cv.evidence import supported_resume
from agents_service.agents.cv.owner import extract_owner
from agents_service.agents.cv.schemas import ResumeFacts
from agents_service.agents.linkedin.schemas import ImageObservation
from agents_service.ai.providers import AIError, Completion
from agents_service.ai.schema import explicit_schema


class LocalGroupingTests(SimpleTestCase):
    def run_owner(self, source, replies):
        with patch('agents_service.agents.cv.owner.provider') as factory:
            generate = factory.return_value.__enter__.return_value.generate
            generate.side_effect = [Completion(data=data) for data in replies]
            result = extract_owner(source, {}, time.monotonic() + 240)
            self.assertLessEqual(generate.call_count, 2)
            return result

    def test_observed_split_name_is_clarified_with_all_tokens_and_evidence(self):
        source = 'Mira\nPatel\nSkills\nPython'
        result = self.run_owner(
            source,
            [
                {'names': ['Mira', 'Patel']},
                {'decision': 'one_owner', 'full_name': 'Mira Patel'},
            ],
        )
        self.assertEqual(result, [{'value': 'Mira Patel', 'evidence': 'Mira\nPatel'}])

    def test_uncertain_or_invented_resolution_fails_closed(self):
        for decision, name in [
            ('uncertain', None),
            ('one_owner', 'Mira'),
            ('one_owner', 'Mira Other Patel'),
            ('one_owner', 'Patel Mira'),
        ]:
            with self.subTest(name=name, decision=decision), self.assertRaises(AIError) as exc:
                self.run_owner(
                    'Mira\nPatel',
                    [{'names': ['Mira', 'Patel']}, {'decision': decision, 'full_name': name}],
                )
            self.assertEqual(exc.exception.reason, 'identity_ambiguous')

    def test_separate_mononyms_are_preserved(self):
        result = self.run_owner(
            'Mira\nPatel',
            [{'names': ['Mira', 'Patel']}, {'decision': 'separate_owners', 'full_name': None}],
        )
        self.assertEqual([item['value'] for item in result], ['Mira', 'Patel'])

    def test_labels_separators_full_names_and_single_names_are_not_joined(self):
        for source, names in [
            ('Name: Mira\nName: Patel', ['Mira', 'Patel']),
            ('Mira\n\nPatel', ['Mira', 'Patel']),
            ('Mira Patel\nOther Person', ['Mira Patel', 'Other Person']),
            ('Madonna', ['Madonna']),
        ]:
            with self.subTest(source=source):
                result = self.run_owner(source, [{'names': names}])
                self.assertEqual([item['value'] for item in result], names)

    def test_clarification_timeout_remains_bounded(self):
        with patch('agents_service.agents.cv.owner.provider') as factory:
            factory.return_value.__enter__.return_value.generate.return_value = Completion(
                data={'names': ['Mira', 'Patel']}
            )
            with self.assertRaises(AIError) as exc:
                extract_owner('Mira\nPatel', {}, time.monotonic() - 1)
            self.assertEqual(exc.exception.reason, 'timeout')
            factory.assert_not_called()

    def test_local_record_fields_explicit_cloud_schema_unchanged(self):
        for model in (ResumeFacts, ImageObservation):
            original = model.model_json_schema()
            explicit = explicit_schema(model)
            for definition in explicit['$defs'].values():
                if 'properties' in definition:
                    self.assertEqual(set(definition['required']), set(definition['properties']))
                    self.assertFalse(definition['additionalProperties'])
            self.assertEqual(original, model.model_json_schema())
        fact = explicit_schema(ResumeFacts)['$defs']['Fact']
        self.assertTrue({'organization', 'start_date', 'end_date', 'evidence'} <= set(fact['required']))

    def test_hallucinated_education_evidence_remains_rejected(self):
        source = 'Education: BS Computer Science, Example University, 2020 - 2024'
        raw = ResumeFacts(
            education=[
                {
                    'title': 'BS Computer Science',
                    'evidence': 'https://exampleuniversity.edu/degrees/computer-science',
                }
            ]
        ).model_dump()
        checked = supported_resume(raw, source)
        self.assertEqual(checked['education'], [])
        self.assertTrue(checked['warnings'])

    def test_ambiguous_owner_reaches_file_error_without_savable_records(self):
        from pathlib import Path

        from tests.agents.kormic_adapter import extract

        with (
            patch('tests.agents.kormic_adapter.read_document', return_value='Mira\nPatel'),
            patch(
                'tests.agents.kormic_adapter.model_extract',
                side_effect=AIError('identity_ambiguous', field='name'),
            ) as model,
        ):
            result = extract([Path('synthetic.txt')], 'cv', {'full_name': 'Mira Patel'}, provider=model)
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['records'], [])
        self.assertIn('full name on one line', result['files'][0]['error'])
        self.assertEqual(result['files'][0]['diagnostic']['reason'], 'identity_ambiguous')

    def test_linkedin_selects_original_lines_instead_of_repairing_model_quotes(self):
        from agents_service.agents.linkedin.local_reader import extract_local

        source = 'Experience\nSoftware Engineer\nSample Systems\n2021 - 2023'
        selection = {'title_line': 2, 'company_line': 3, 'date_line': 4, 'description_lines': []}
        with patch('agents_service.agents.linkedin.local_reader.provider') as factory:
            generate = factory.return_value.__enter__.return_value.generate
            generate.side_effect = [
                Completion(data={'experiences': [selection]}),
                Completion(data={'skills': []}),
            ]
            result = extract_local(source, {})
        job = result['experiences'][0]
        self.assertEqual(job['title'], 'Software Engineer')
        self.assertEqual(job['company'], 'Sample Systems')
        self.assertEqual((job['start_date'], job['end_date']), ('2021', '2023'))
        self.assertIsNone(job['description'])
        self.assertEqual(job['evidence'], source.split('\n', 1)[1])
        self.assertEqual(generate.call_count, 2)
        self.assertEqual(generate.call_args_list[0].kwargs['name'], 'employment_lines')

    def test_linkedin_observed_generated_facts_are_no_longer_valid_output(self):

        from agents_service.agents.linkedin.local_reader import extract_local

        source = 'Experience\nSoftware Engineer\nSample Systems\n2021 - 2023'
        bad = {
            'title': 'Software Engineer',
            'company': 'Sample Systems',
            'start_date': '2021-07-01',
            'description': 'Developed Java and Python APIs',
            'evidence': 'Sample Systems 2021 - 2023',
        }
        with patch('agents_service.agents.linkedin.local_reader.provider') as factory:
            generate = factory.return_value.__enter__.return_value.generate
            generate.side_effect = [
                Completion(data={'experiences': [bad]}),
                Completion(data={'skills': []}),
            ]
            result = extract_local(source, {})
        # Generated values are rejected; the job comes from the source layout only.
        self.assertEqual(generate.call_count, 2)
        (job,) = result['experiences']
        self.assertEqual((job['start_date'], job['end_date']), ('2021', '2023'))
        self.assertFalse(job['description'])
        self.assertNotIn('Java', str(result))
        # The grouped layout (employer, total duration, role, dates) is read
        # from the source too; the generated values never appear.
        grouped = 'Experience\nSample Systems\n3 yrs\nSoftware Engineer\n2021 - 2023'
        with patch('agents_service.agents.linkedin.local_reader.provider') as factory:
            generate = factory.return_value.__enter__.return_value.generate
            generate.side_effect = [
                Completion(data={'experiences': [bad]}),
                Completion(data={'skills': []}),
            ]
            result = extract_local(grouped, {})
        (job,) = result['experiences']
        self.assertEqual(
            (job['title'], job['company'], job['start_date']),
            ('Software Engineer', 'Sample Systems', '2021'),
        )
        self.assertNotIn('Java', str(result))
