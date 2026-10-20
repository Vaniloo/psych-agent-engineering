"""Prepare private, ignored SearXNG settings without printing its secret."""

import argparse
import json
import secrets
from pathlib import Path


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--data-dir', type=Path, default=Path('data/web-search'))
    args = p.parse_args()
    folder = args.data_dir.resolve()
    (folder/'config').mkdir(parents=True, exist_ok=True)
    (folder/'cache').mkdir(exist_ok=True)
    settings = folder/'config/settings.yml'
    if not settings.exists():
        settings.write_text('use_default_settings: true\nserver:\n  secret_key: '+
            json.dumps(secrets.token_urlsafe(32))+
            '\n  limiter: false\n  image_proxy: false\nsearch:\n  formats: [html, json]\n'+
            'outgoing:\n  request_timeout: 6\n  max_request_timeout: 8\n')
        settings.chmod(0o600)
    env = folder/'compose.env'
    env.write_text('SEARCH_DATA_DIR='+json.dumps(str(folder))+'\n')
    env.chmod(0o600)
    print('Prepared private settings in', folder)


if __name__ == '__main__':
    main()
