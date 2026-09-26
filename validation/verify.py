"""Validate an exact source revision and reproduce the unpatched failure."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

config = json.loads((Path(__file__).parent / 'case.json').read_text())
source = Path(sys.argv[1]).resolve()
evidence = Path(sys.argv[2]).resolve()
evidence.mkdir(parents=True, exist_ok=True)
env = os.environ.copy()
env['MPLBACKEND'] = 'Agg'
env.pop('PYTHONPATH', None)
python = sys.executable


def run(label, command, expected=0, cwd=source, timeout=1200):
    result = subprocess.run(command, cwd=cwd, env=env, capture_output=True,
                            text=True, timeout=timeout)
    output = result.stdout + result.stderr
    (evidence / (label + '.log')).write_text(output)
    print(label, 'exit', result.returncode, flush=True)
    if result.returncode != expected:
        print(output[-15000:], flush=True)
        raise AssertionError((label, result.returncode, expected))
    return result


def tests(label, failures=0, coverage=False):
    xml = evidence / (label + '.xml')
    command = [python, '-m', 'pytest', 'tests', '-q', '--tb=short',
               '--junitxml', str(xml)]
    if coverage:
        command += ['--cov=' + config['module'], '--cov-branch',
                    '--cov-report=json:' + str(evidence / 'coverage.json')]
    run(label, command, expected=1 if failures else 0)
    counts = [sum(int(s.get(k, 0)) for s in ET.parse(xml).iter('testsuite'))
              for k in ('tests', 'failures', 'errors', 'skipped')]
    assert counts == [config['total'], failures, 0, 0], counts
    print(label, 'counts', counts, flush=True)


actual = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=source,
                                 text=True).strip()
assert actual == config['source'], (actual, config['source'])
print('TESTED SOURCE', actual, flush=True)
run('versions', [python, '-m', 'pip', 'freeze'])
run('dependencies', [python, '-m', 'pip', 'check'])
run('new-test-lint', [python, '-m', 'flake8', config['test']])
run('patch-check', ['git', 'diff', '--check', config['base'], 'HEAD'])
tests('fixed', coverage=True)
production = source / config['production']
fixed = production.read_bytes()
try:
    production.write_bytes(subprocess.check_output(
        ['git', 'show', config['base'] + ':' + config['production']], cwd=source))
    tests('original', failures=config['failures'])
finally:
    production.write_bytes(fixed)
assert production.read_bytes() == fixed
run('restoration', ['git', 'diff', '--exit-code'])
tests('restored')

if config['module'] == 'simupy_flight':
    result = run('nesc', [python, 'nesc_test_cases/run_nesc_cases.py'])
    statuses = re.findall(r'^case\s+(\S+)\s+(passed|failed|skipped)\s*$',
                          result.stdout, flags=re.MULTILINE)
    assert len(statuses) == 17 and all(x[1] == 'passed' for x in statuses), statuses
    print('NESC CASES', statuses, flush=True)
    with tempfile.TemporaryDirectory() as directory:
        outside = Path(directory)
        dist = outside / 'dist'
        run('package-build', [python, '-m', 'build', '--outdir', str(dist)])
        wheel, = dist.glob('*.whl')
        run('install-wheel', [python, '-m', 'pip', 'install', '--no-deps',
                              '--force-reinstall', str(wheel)])
        target = outside / 'test_daveml_relations.py'
        shutil.copy2(source / config['test'], target)
        check = "from pathlib import Path; import simupy_flight; p=Path(simupy_flight.__file__).resolve(); print(p); assert 'site-packages' in p.parts"
        run('installed-import', [python, '-c', check], cwd=outside)
        xml = evidence / 'installed.xml'
        run('installed-tests', [python, '-m', 'pytest', str(target), '-q',
                                '--junitxml', str(xml)], cwd=outside)
        counts = [sum(int(s.get(k, 0)) for s in ET.parse(xml).iter('testsuite'))
                  for k in ('tests', 'failures', 'errors', 'skipped')]
        assert counts == [45, 0, 0, 0], counts
else:
    run('gdal-version', ['gdal_translate', '--version'])
print('VALIDATION COMPLETE', config['repo'], actual, flush=True)
