"""Bind exact-source speech stages to immutable historical-plan installation."""

import argparse
import json
from contextlib import contextmanager
from pathlib import Path
from types import FunctionType

import dubbing_state as state
from . import editorial_history, exact_source_window_stage
from .speech_variants import require


KIND = 'exact-source-immutable-historical-intro-plan-runtime-v1'
SCOPE = editorial_history.SCOPE
PARENT_FUNCTIONS = {name: getattr(editorial_history, name) for name in
    ('implementations', 'authority', 'historical_derivation', 'runtime', 'validate', 'profile', 'install')}


def implementations():
    require(all(getattr(editorial_history, name) is function for name, function in PARENT_FUNCTIONS.items()),
        'Reject changed frozen historical-plan functions')
    require(editorial_history.state is state and state.load is editorial_history.FROZEN_LOAD
        and state.matches is editorial_history.FROZEN_MATCHES, 'Reject global historical state I/O hooks')
    exact_source_window_stage.implementations()
    return {**PARENT_FUNCTIONS['implementations'](),
        'frozen_history': state.identity(editorial_history.__file__),
        'exact_source_stage': state.identity(exact_source_window_stage.__file__),
        'exact_source_history': state.identity(__file__)}


def authority(path):
    implementations()
    binding = ENGINE['native_authority'](path)
    stage = state.load(binding['stage_manifest']['path'])
    require(stage['kind'] == exact_source_window_stage.KIND
        and stage['adapter'] == state.identity(exact_source_window_stage.__file__)
        and stage['mapping_policy'] == exact_source_window_stage.POLICY
        and state.matches(stage['config']), 'Historical authority requires an exact-source stage')
    require(state.load(stage['config']['path'])['work'] == binding['canonical_work'],
        'Historical exact-source stage belongs to another canonical project')
    return binding


def private_engine():
    namespace = {**editorial_history.__dict__, '__file__': __file__, '__name__': __name__,
        'KIND': KIND, 'implementations': implementations, 'authority': authority,
        'editorial_sample_grid': exact_source_window_stage}
    for name in ('authority', 'historical_derivation', 'validate', 'profile', 'install', 'runtime'):
        function = PARENT_FUNCTIONS[name]
        if name == 'runtime':
            function = function.__wrapped__
        require(function.__closure__ is None and function.__globals__ is editorial_history.__dict__,
            'Reject foreign or closure-bearing historical-plan functions')
        copied = FunctionType(function.__code__, namespace, function.__name__, function.__defaults__)
        copied.__kwdefaults__ = function.__kwdefaults__
        if name == 'authority':
            namespace['native_authority'] = copied
        else:
            namespace[name] = contextmanager(copied) if name == 'runtime' else copied
    return namespace


ENGINE = private_engine()


@contextmanager
def runtime(path):
    implementations()
    with ENGINE['runtime'](path) as binding:
        yield binding


def validate(path):
    implementations()
    return ENGINE['validate'](path)


def install(path, operation, install_config, output, stage_config, stage):
    implementations()
    return ENGINE['install'](path, operation, install_config, output, stage_config, stage)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('validate', 'prepare', 'apply', 'check'))
    parser.add_argument('authority', type=Path)
    parser.add_argument('--install-config', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--stage-config', type=Path)
    parser.add_argument('--stage', type=Path)
    arguments = parser.parse_args()
    installation = (arguments.install_config, arguments.output, arguments.stage_config, arguments.stage)
    if arguments.action == 'validate':
        require(not any(value is not None for value in installation), 'Unexpected installation arguments')
        result = validate(arguments.authority)
    else:
        require(all(value is not None for value in installation), 'Explicit installation and stage arguments required')
        result = install(arguments.authority, arguments.action, *installation)
    print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
