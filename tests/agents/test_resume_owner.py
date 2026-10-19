"""Owner-context tests use synthetic CVs and controlled model output."""

import tempfile
import time
from pathlib import Path
from unittest import TestCase as SimpleTestCase
from unittest.mock import patch

from agents_service.agents.cv.local_reader import extract_local_resume
from agents_service.agents.cv.owner import (
    extract_owner,
    owner_contexts,
    supported_owner,
)
from agents_service.ai.providers import AIError, Completion
from tests.agents.kormic_adapter import extract


class ResumeOwnerTests(SimpleTestCase):
    def identify(self, source, names):
        with patch('agents_service.agents.cv.owner.provider') as factory:
            factory.return_value.__enter__.return_value.generate.return_value = Completion(
                data={'names': names}
            )
            return extract_owner(source, {}, time.monotonic() + 240)

    def pipeline(self, source, names, profile):
        owners = self.identify(source, names)
        data = {
            'name': owners[0]['value'] if owners else None,
            '_owner_candidates': owners,
            'skills': ['Python'],
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'Misleading_Name.txt'
            path.write_text(source)
            with patch('tests.agents.kormic_adapter.read_document', return_value=source):
                return extract([path], 'cv', {'full_name': profile}, provider=lambda *_: data)

    def test_body_and_reference_names_are_not_identity_context(self):
        source = (
            'Mira Patel\nData Analyst\nother.person@example.com\nProfessional Summary\nPython analyst\n'
            'References\nName: Other Person\nProjects\nCollaborator: Someone Else'
        )
        self.assertEqual(owner_contexts(source), ['Mira Patel\nData Analyst\nother.person@example.com'])
        self.assertEqual(
            [x['value'] for x in self.identify(source, ['Mira Patel', 'Other Person'])],
            ['Mira Patel'],
        )

    def test_email_name_is_not_owner_even_if_model_suggests_it(self):
        source = 'Mira Patel\nother.person@example.com\nSkills\nPython'
        self.assertFalse(supported_owner('other.person', owner_contexts(source)[0], source))
        self.assertEqual(self.identify(source, ['other.person']), [])

    # Kormic compared these names with the profile (its name check). The agent
    # returns every owner name with its sources and leaves that decision to each project.

    def names(self, result):
        return [r for r in result['records'] if r['section'] == 'name']

    def test_owner_name_is_returned_whatever_the_profile(self):
        source = 'Mira Patel\nSkills\nPython'
        for profile in ('Mira Patel', 'MIRA PATEL', 'Other Person'):
            with self.subTest(profile=profile):
                result = self.pipeline(source, ['Mira Patel'], profile)
                self.assertEqual(result['status'], 'ready')
                self.assertEqual([r['value'] for r in self.names(result)], ['Mira Patel'])

    def test_initials_and_other_owners_are_all_preserved(self):
        source = 'Mira Patel\nM Patel\nSkills\nPython'
        result = self.pipeline(source, ['Mira Patel', 'M Patel'], 'Mira Patel')
        self.assertEqual([r['value'] for r in self.names(result)], ['Mira Patel', 'M Patel'])
        self.assertTrue(any(w.startswith('Multiple possible CV owner names') for w in result['warnings']))
        source = 'Mira Patel\nOther Person\nSkills\nPython'
        result = self.pipeline(source, ['Mira Patel', 'Other Person'], 'Mira Patel')
        self.assertEqual(len(self.names(result)), 2)

    def test_two_full_names_are_both_returned(self):
        source = 'Mira Patel\nMaya Patel\nSkills\nPython'
        result = self.pipeline(source, ['Mira Patel', 'Maya Patel'], 'M Patel')
        self.assertEqual({r['value'] for r in self.names(result)}, {'Mira Patel', 'Maya Patel'})

    def test_missing_owner_returns_no_name_record(self):
        result = self.pipeline('Skills\nPython', [], 'Mira Patel')
        self.assertEqual(self.names(result), [])
        self.assertTrue(any(r['section'] == 'skills' for r in result['records']))

    def test_wrapped_name_and_later_explicit_owner_context(self):
        source = 'Mira\nPatel\nSkills\nPython\nPersonal Details\nFull Name: Mira Patel'
        owners = self.identify(source, ['Mira Patel'])
        self.assertEqual(len(owners), 2)
        self.assertTrue(all(x['value'] == 'Mira Patel' for x in owners))
        result = self.pipeline(source, ['Mira Patel'], 'Mira Patel')
        # One name, quoted from both places in the CV.
        self.assertEqual(len(self.names(result)), 1)
        self.assertEqual(len(self.names(result)[0]['sources']), 2)

    def test_repeated_document_headers_preserve_conflicting_owner(self):
        source = 'Mira Patel\nSkills\nPython\nCurriculum Vitae\nOther Person\nEducation\nBS'
        self.assertEqual(owner_contexts(source), ['Mira Patel', 'Other Person'])
        result = self.pipeline(source, ['Mira Patel', 'Other Person'], 'Mira Patel')
        self.assertEqual({r['value'] for r in self.names(result)}, {'Mira Patel', 'Other Person'})

    def test_no_owner_requests_for_body_chunks_and_sections_still_extracted(self):
        source = 'Mira Patel\nSkills\nPython\n' + 'Project work with Other Person.\n' * 70
        requests = []

        def generate(**kwargs):
            requests.append(kwargs)
            props = kwargs['schema']['properties']
            defaults = (
                __import__('agents_service.agents.cv.schemas', fromlist=['ResumeFacts'])
                .ResumeFacts()
                .model_dump()
            )
            return Completion(
                data={
                    field: ['Python']
                    if field == 'skills' and 'Python' in kwargs['messages'][0]['content']
                    else defaults[field]
                    for field in props
                }
            )

        with (
            patch('agents_service.agents.cv.owner.provider') as owner,
            patch('agents_service.agents.cv.local_reader.provider') as body,
        ):
            owner.return_value.__enter__.return_value.generate.return_value = Completion(
                data={'names': ['Mira Patel']}
            )
            body.return_value.__enter__.return_value.generate.side_effect = generate
            result = extract_local_resume(source, {})
        owner.return_value.__enter__.return_value.generate.assert_called_once()
        self.assertTrue(all('name' not in req['schema']['properties'] for req in requests))
        self.assertEqual(result['name'], 'Mira Patel')
        self.assertEqual(result['skills'], ['Python'])

    def test_owner_limits_and_truncation_fail_closed(self):
        with self.assertRaises(AIError):
            owner_contexts('Name: ' + 'x' * 1801)
        with patch('agents_service.agents.cv.owner.provider') as factory:
            factory.return_value.__enter__.return_value.generate.return_value = Completion(finish='length')
            with self.assertRaises(AIError):
                extract_owner('Mira Patel', {}, time.monotonic() + 240)
