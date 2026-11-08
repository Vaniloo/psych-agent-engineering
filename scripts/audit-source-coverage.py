"""Audit checked-in source, local imports and current documentation links without GPU/network."""

import argparse
import ast
import hashlib
import json
import re
import subprocess
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / 'agent-runtime'
SOURCE_ROOTS = ('agent-runtime/psych_agent_runtime', 'agent-runtime/training',
                'agent-runtime/benchmarks', 'agent-runtime/tests', 'backend/src',
                'frontend', 'contracts', 'scripts')
DOCS = ('README.md', 'HANDOFF.md', 'docs/architecture.md', 'docs/model-training-handoff.md',
        'docs/source-learning-roadmap.md', 'docs/interview-guide.md', 'docs/resume.md',
        'docs/code-cleanup-20261108.md', 'agent-runtime/training/README.md',
        'agent-runtime/training/public_tools/README.md')
EXTENSIONS = {'.py', '.java', '.js', '.cjs', '.html', '.css', '.sh', '.yaml', '.yml', '.sql'}


def audit():
    tracked = set(subprocess.check_output(['git', 'ls-files', '-z'], cwd=ROOT).decode().split('\0'))
    files = sorted(p for folder in SOURCE_ROOTS for p in (ROOT / folder).rglob('*')
                   if p.is_file() and p.suffix in EXTENSIONS
                   and not {'__pycache__', 'node_modules', 'target', '.venv'} & set(p.parts))
    missing_git, imports, links, syntax = [], [], [], []
    for p in files:
        relative = p.relative_to(ROOT).as_posix()
        if relative not in tracked:
            missing_git.append(relative)
        if p.suffix != '.py':
            continue
        try:
            tree = ast.parse(p.read_text(), filename=relative)
        except SyntaxError as exc:
            syntax.append(f'{relative}:{exc.lineno}')
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            if node.level:
                base = p.parent
                for _ in range(node.level - 1):
                    base = base.parent
                target = base.joinpath(*(node.module or '').split('.')) if node.module else base
            elif node.module and node.module.split('.')[0] in {'training', 'psych_agent_runtime', 'benchmarks'}:
                target = RUNTIME.joinpath(*node.module.split('.'))
            else:
                continue
            if not (target.is_dir() or target.with_suffix('.py').is_file()):
                imports.append(f'{relative}:{node.lineno}:{node.module}')
    for name in DOCS:
        p = ROOT / name
        if name not in tracked:
            missing_git.append(name)
        if not p.is_file():
            links.append(name)
            continue
        for ref in re.findall(r'\]\(([^)]+)\)', p.read_text()):
            ref = unquote(ref.strip('<>').split('#', 1)[0])
            if not ref or re.match(r'[a-zA-Z]+:', ref):
                continue
            target = (p.parent / ref).resolve()
            if not target.exists():
                links.append(f'{name}: {ref}')
            elif target.is_file() and target.is_relative_to(ROOT):
                relative = target.relative_to(ROOT).as_posix()
                if relative not in tracked:
                    missing_git.append(relative)
    manifests = {}
    for date in ('20261014', '20261020'):
        folder = RUNTIME / 'training/artifacts' / date
        manifests[date] = {'manifestPresent': (folder / 'manifest.json').is_file(),
                           'manifestTracked': (folder / 'manifest.json').relative_to(ROOT).as_posix() in tracked}
    index = {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    return {'status': 'passed' if not (missing_git or imports or links or syntax) and all(all(v.values()) for v in manifests.values()) else 'failed',
            'sourceFiles': len(files), 'pythonFiles': sum(p.suffix == '.py' for p in files),
            'trainingPythonFiles': sum('/training/' in p.as_posix() and p.suffix == '.py' for p in files),
            'sourceTreeSha256': hashlib.sha256(json.dumps(index, sort_keys=True).encode()).hexdigest(),
            'missingFromGitIndex': sorted(set(missing_git)), 'missingLocalModules': imports,
            'brokenCurrentDocLinks': links, 'syntaxErrors': syntax, 'weightManifests': manifests,
            'scope': 'source completeness, local module paths and current entry documentation; not GPU training or deployment validation'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path)
    args = parser.parse_args()
    result = audit()
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + '\n'
    if args.out:
        args.out.write_text(rendered)
    print(rendered)
    raise SystemExit(result['status'] != 'passed')
