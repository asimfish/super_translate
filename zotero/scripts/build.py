"""Build a deterministic XPI using only the standard library."""
import hashlib
import json
from pathlib import Path
import zipfile

root = Path(__file__).resolve().parents[1]
manifest = json.loads((root / 'manifest.json').read_text())
files = [root / name for name in ('manifest.json', 'bootstrap.js', 'prefs.js')]
for directory in ('content', 'locale'):
    files.extend(p for p in (root / directory).rglob('*') if p.is_file())
out = root / 'dist'
out.mkdir(exist_ok=True)
target = out / f"supertranslate-zotero-{manifest['version']}.xpi"
inventory = {}
with zipfile.ZipFile(target, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
    for path in sorted(files):
        name = path.relative_to(root).as_posix()
        data = path.read_bytes()
        info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
        info.compress_type = zipfile.ZIP_DEFLATED
        info.external_attr = 0o100644 << 16
        archive.writestr(info, data)
        inventory[name] = hashlib.sha256(data).hexdigest()
receipt = {'file': target.name, 'sha256': hashlib.sha256(target.read_bytes()).hexdigest(), 'files': inventory}
(out / 'SHA256.json').write_text(json.dumps(receipt, indent=2) + '\n')
print(json.dumps({'path': str(target), 'sha256': receipt['sha256'], 'files': len(files)}))
