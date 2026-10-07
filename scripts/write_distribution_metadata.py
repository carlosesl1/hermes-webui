"""Write Docker/source-archive metadata without interpolating executable Python.

Build callers supply HERMES_VERSION, HERMES_SOURCE and HERMES_REVISION.
This script needs only the standard library and does not import the application.
"""
import os
from pathlib import Path
import re
import sys


def main():
    source = os.environ.get('HERMES_SOURCE', 'https://github.com/carlosesl1/hermes-webui')
    revision = os.environ.get('HERMES_REVISION', 'unknown')
    version = os.environ.get('HERMES_VERSION', 'unknown')
    if not re.fullmatch(r'https://github\.com/[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9_][A-Za-z0-9_.-]*', source):
        raise SystemExit('HERMES_SOURCE must be a canonical https://github.com/owner/repo URL')
    if revision != 'unknown' and not re.fullmatch(r'[0-9a-f]{40}', revision):
        raise SystemExit('HERMES_REVISION must be a full lowercase Git SHA or unknown')
    values = {'__version__': version, '__source__': source, '__revision__': revision}
    Path(sys.argv[1]).write_text(
        ''.join(f'{key} = {value!r}\n' for key, value in values.items()), encoding='utf-8'
    )


if __name__ == '__main__':
    main()
