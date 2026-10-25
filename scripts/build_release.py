#!/usr/bin/env python3
"""Reproducible, allowlisted packages; no credentials, history or local environment."""
import hashlib
import json
from pathlib import Path
import re
import zipfile

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / 'dist'
SKILL = ROOT / 'skills/kaigong-detective'
FILES = ['README.md', 'THIRD_PARTY_NOTICES.md', 'start.command', 'start.bat',
         'standalone/开工侦探.html', 'standalone/server.py', 'standalone/__init__.py', 'standalone/preview.png',
         'docs/advanced-status.md', 'docs/images/deep-investigation.png',
         'docs/images/monitor-prototype.png', 'docs/images/screenshot-prototype.png',
         'scripts/build_release.py', 'tests/test_standalone.py']


def skill_files():
    return sorted(p for p in SKILL.rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix != '.pyc')


def audit(files):
    for p in files:
        if p.name.startswith('.env') or p.suffix == '.pyc' or '__pycache__' in p.parts or p.is_symlink():
            raise ValueError('不允许进入分发包的文件：' + str(p.relative_to(ROOT)))
        if p.suffix in {'.py', '.md', '.html', '.json', '.yaml', '.command', '.bat'}:
            text = p.read_text('utf-8')
            if re.search(r'sk-[A-Za-z0-9]{16,}', text):
                raise ValueError('发现疑似真实密钥，已停止打包：' + str(p.relative_to(ROOT)))
            if re.search(r'/Users/[A-Za-z][^/\s]*/', text):
                raise ValueError('发现作者电脑的绝对路径：' + str(p.relative_to(ROOT)))
    provenance = json.loads((SKILL / 'references/upstream/provenance.json').read_text())
    for name, meta in provenance['files'].items():
        p = SKILL / 'references/upstream' / name
        if hashlib.sha256(p.read_bytes()).hexdigest() != meta['sha256']:
            raise ValueError('上游原文件哈希不匹配：' + name)


def archive(path, pairs):
    with zipfile.ZipFile(path, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for source, target in pairs:
            info = zipfile.ZipInfo(str(target), (2026, 10, 25, 0, 0, 0))
            mode = 0o755 if source.name == 'start.command' else 0o644
            info.external_attr = (0o100000 | mode) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, source.read_bytes())
    return {'file': path.name, 'bytes': path.stat().st_size, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


def main():
    skill = skill_files()
    files = [ROOT / f for f in FILES] + skill
    audit(files)
    DIST.mkdir(exist_ok=True)
    reports = [archive(DIST / 'kaigong-detective.zip', [(p, Path('kaigong-detective') / p.relative_to(SKILL)) for p in skill])]
    (DIST / 'kaigong-detective.skill').write_bytes((DIST / 'kaigong-detective.zip').read_bytes())
    pairs = [(p, Path('kaigong-detective') / p.relative_to(ROOT)) for p in files]
    for name in ('kaigong-detective.zip', 'kaigong-detective.skill'):
        pairs.append((DIST / name, Path('kaigong-detective/dist') / name))
    reports.append(archive(DIST / 'kaigong-detective-local.zip', pairs))
    (DIST / '开工侦探.html').write_bytes((ROOT / 'standalone/开工侦探.html').read_bytes())
    print(json.dumps({'packages': reports, 'skill_files': len(skill), 'source_files': len(files), 'audit': 'passed'}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
