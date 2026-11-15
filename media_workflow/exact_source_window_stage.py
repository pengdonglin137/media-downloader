"""Stage complete speech without clearing samples outside exact source windows."""

import argparse
import json
from contextlib import contextmanager
from decimal import Decimal
from fractions import Fraction
from pathlib import Path
from types import FunctionType

import numpy as np

import dubbing_state as state
from . import editorial_sample_grid, editorial_stage
from .speech_variants import require


KIND = 'exact-source-preserving-complete-editorial-stage-v1'
POLICY = 'preserve-exact-source-exterior-within-legacy-ownership-v1'
RATE = editorial_sample_grid.RATE
FROZEN_CHECK = editorial_sample_grid.FROZEN_CHECK
PARENT_FUNCTIONS = {name: getattr(editorial_sample_grid, name) for name in
    ('implementations', 'sample_mapping', 'configuration', 'selected_cases', 'narration', 'prepare', 'check')}


def implementations():
    require(editorial_sample_grid.state is state and editorial_sample_grid.legacy is editorial_stage
        and editorial_sample_grid.FROZEN_CHECK is FROZEN_CHECK, 'Reject global sample-grid state hooks')
    require(all(getattr(editorial_sample_grid, name) is function for name, function in PARENT_FUNCTIONS.items()),
        'Reject foreign sample-grid functions')
    return {**PARENT_FUNCTIONS['implementations'](),
        'frozen_sample_grid': state.identity(editorial_sample_grid.__file__),
        'exact_source_window_stage': state.identity(__file__)}


def sample_mapping(start, end, count, window_samples):
    return ENGINE['sample_mapping'](start, end, count, window_samples)


def replace_owned(waveform, cases, replacements):
    require(isinstance(waveform, np.ndarray) and waveform.ndim == 1
        and waveform.dtype == np.dtype('<i2'), 'Expected mono PCM16 baseline samples')
    require(len(cases) == len(replacements) and cases, 'Incomplete complete-audio replacement inventory')
    result = waveform.copy()
    cursor = 0
    for case, samples in zip(cases, replacements):
        require(isinstance(samples, np.ndarray) and samples.ndim == 1
            and samples.dtype == np.dtype('<i2'), 'Expected complete PCM16 synthesis')
        expected = sample_mapping(case['source_start'], case['source_end'], len(samples),
            case['preview']['source_window_samples'])
        require(case['sample_mapping'] == expected and case['start_sample'] == expected['start_sample']
            and case['end_sample'] == expected['end_sample'], 'Exact source mapping changed')
        first, last = expected['start_sample'], expected['end_sample']
        require(cursor <= expected['legacy_start_sample'] <= first < last
            <= expected['legacy_end_sample'] <= len(result), 'Overlapping or out-of-range ownership')
        result[first:last] = 0
        result[first:first + len(samples)] = samples
        cursor = expected['legacy_end_sample']
    return result


def private_engine():
    namespace = {**editorial_sample_grid.__dict__, '__file__': __file__, '__name__': __name__,
        'KIND': KIND, 'POLICY': POLICY, 'implementations': implementations, 'replace_owned': replace_owned}
    for name, function in PARENT_FUNCTIONS.items():
        require(function.__closure__ is None and function.__globals__ is editorial_sample_grid.__dict__,
            'Reject foreign or closure-bearing sample-grid functions')
        if name != 'implementations':
            namespace[name] = FunctionType(function.__code__, namespace, function.__name__, function.__defaults__)
    return namespace


ENGINE = private_engine()


def prepare(config_path, output):
    implementations()
    return ENGINE['prepare'](config_path, output)


def check(config_path, output):
    implementations()
    return ENGINE['check'](config_path, output)


@contextmanager
def runtime(config_path, output):
    config_path, output = Path(config_path).resolve(), Path(output).resolve()
    require(editorial_stage.check is FROZEN_CHECK, 'Reject nested or foreign stage-check dispatch')
    check(config_path, output)
    contract = state.load(state.load(output / 'manifest.json')['contract']['path'])
    require(all(Fraction(Decimal(case['source_start'])) * RATE == case['start_sample']
        for case in contract['cases']), 'Caption projection requires an exact source-start sample')

    def dispatch(selected_config, selected_output):
        if Path(selected_config).resolve() == config_path and Path(selected_output).resolve() == output:
            return check(config_path, output)
        return FROZEN_CHECK(selected_config, selected_output)

    editorial_stage.check = dispatch
    try:
        yield
    finally:
        editorial_stage.check = FROZEN_CHECK


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('prepare', 'check'))
    parser.add_argument('config', type=Path)
    parser.add_argument('--output', required=True, type=Path)
    arguments = parser.parse_args()
    function = prepare if arguments.action == 'prepare' else check
    print(json.dumps(function(arguments.config, arguments.output), ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
