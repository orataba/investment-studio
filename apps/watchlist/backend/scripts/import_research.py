#!/usr/bin/env python3
"""Preview or publish an external, explicitly selected researcher compilation."""
import argparse
import json
import os
from pathlib import Path
import sys
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--api-base', required=True, help='Watchlist base URL, without /api')
    parser.add_argument('--origin', help='Trusted browser origin; defaults to the scheme and host of --api-base')
    parser.add_argument('--instrument-id', required=True)
    parser.add_argument('--input', required=True, type=Path, help='External ResearchImport JSON')
    parser.add_argument('--dry-run', action='store_true', help='Read-only preview (the default)')
    parser.add_argument('--preview-output', type=Path, help='Save the reviewable preview outside the repository')
    parser.add_argument('--publish', action='store_true', help='Publish exactly the package using a saved preview')
    parser.add_argument('--preview', type=Path, help='Previously reviewed preview JSON required for publication')
    args = parser.parse_args()
    if args.publish and (args.dry_run or not args.preview):
        parser.error('--publish requires --preview and cannot be combined with --dry-run')
    if args.publish and args.preview_output:
        parser.error('--preview-output is only for dry-run previews')
    package = json.loads(args.input.read_text())
    if package.get('instrument_id') != args.instrument_id:
        parser.error('--instrument-id must exactly match the package; imports never infer a target')
    endpoint = urlsplit(args.api_base)
    if endpoint.scheme not in {'http', 'https'} or not endpoint.netloc or endpoint.username or endpoint.password:
        parser.error('--api-base must be an HTTP(S) URL without credentials')
    origin = args.origin or f'{endpoint.scheme}://{endpoint.netloc}'
    parsed_origin = urlsplit(origin)
    if (parsed_origin.scheme not in {'http', 'https'} or not parsed_origin.netloc or
            parsed_origin.username or parsed_origin.password or parsed_origin.path not in {'', '/'} or
            parsed_origin.query or parsed_origin.fragment):
        parser.error('--origin must contain only an HTTP(S) scheme and host, with optional port')
    headers = {'Content-Type': 'application/json', 'Origin': origin.rstrip('/')}
    token, cookie = os.getenv('WATCHLIST_IMPORT_TOKEN'), os.getenv('WATCHLIST_IMPORT_COOKIE')
    if token:
        headers['Authorization'] = 'Bearer ' + token
    elif cookie:
        headers['Cookie'] = cookie
    if args.publish:
        preview = json.loads(args.preview.read_text())
        if preview.get('instrument_id') != args.instrument_id or preview.get('import_id') != package.get('import_id'):
            parser.error('Saved preview belongs to a different import or instrument')
        body = {'package': package, 'expected_versions': preview['expected_versions']}
        route = 'publish'
    else:
        body, route = package, 'preview'
    request = Request(args.api_base.rstrip('/') + '/api/research/imports/' + route,
                      data=json.dumps(body, ensure_ascii=False).encode(), headers=headers, method='POST')
    try:
        with urlopen(request, timeout=90) as response:
            result = json.load(response)
    except HTTPError as error:
        print(f'Import rejected ({error.code}): {error.read().decode()}', file=sys.stderr)
        return 1
    output = json.dumps(result, ensure_ascii=False, indent=2)
    if args.preview_output:
        args.preview_output.write_text(output + '\n')
    print(output)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
