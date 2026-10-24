"""A lookup agent that uses every shared lookup part, for the tests."""

import argparse

from pydantic import BaseModel, ConfigDict, SecretStr

from agents_service.agents.base import Agent, AgentResult, JobFailed, Tool
from agents_service.agents.records import data_record
from agents_service.lookups.http import SourceError, get_json

CALLS = {'transport': None, 'behaviour': 'ok'}


class Input(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str


class ProbeSettings(BaseModel):
    api_key: SecretStr
    daily_limit: int = 3


class ProbeAgent(Agent):
    name = 'probe'
    title = 'Probe'
    key_prefix = 'probe'
    input_kind = 'data'
    data_model = Input
    sections = ('answer',)
    reader_version = 'probe/1'
    uses_model = False
    empty_is_answer = True
    settings_model = ProbeSettings

    def run(self, job):
        behaviour = CALLS['behaviour']
        if behaviour == 'fail_retry':
            raise JobFailed('source_unavailable', retry=True)
        if behaviour == 'bad_code':
            raise JobFailed('not_a_code')
        question = {'name': job.data['name']}
        cached = job.cache.get(question)
        if cached is None:
            if not job.use_quota('search', job.agent_settings.daily_limit):
                raise JobFailed('daily_limit_reached')
            try:
                cached = get_json(
                    'https://probe.example/search',
                    params={'q': job.data['name'], 'api_key': job.agent_settings.api_key.get_secret_value()},
                    source='Probe',
                    transport=CALLS['transport'],
                )
            except SourceError as exc:
                raise JobFailed('source_unavailable', retry=exc.kind == 'unavailable') from None
            job.cache.put(question, cached, 3600)
        with job.references.local_copy('list') as (path, info):
            listed = path.read_text(encoding='utf-8').split()
        return AgentResult(
            records=[
                data_record(
                    'answer',
                    {'hits': cached.get('hits', []), 'listed': job.data['name'] in listed},
                    'https://probe.example/search',
                    info['as_of'],
                )
            ]
        )

    def tools(self):
        def add(parser: argparse.ArgumentParser):
            parser.add_argument('--file', required=True)
            parser.add_argument('--as-of', required=True)

        def load(context, args):
            from pathlib import Path

            info = context.references.put('list', Path(args.file).read_bytes(), '.txt', args.as_of)
            print(f'loaded {info["size"]} bytes as of {info["as_of"]}')
            return 0

        return {'load': Tool(help='load the list', add_arguments=add, run=load)}

    def readiness(self, settings, references):
        state = super().readiness(settings, references)
        if state != 'ok':
            return state
        return 'ok' if references.info('list') else 'no list loaded (agents tool probe load)'


AGENT = ProbeAgent()
