"""Save one of the three reported-unsaved Kokura results from a local Safari HTML file."""

import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import socket
import tempfile
import unicodedata
import zipfile
from zoneinfo import ZoneInfo

from lxml import html


from _paths import collect_root
ROOT = collect_root()
DAY = ROOT / 'HEROINES並行取得' / '2026-02-01' / '小倉'
PDF = ROOT / '原本' / 'JRA' / '2026' / '2026-1kokura4.pdf'
INPUT_SHA = 'cbc00f1f3e6f354602042d98e6a9a22bf8d218ff1cff9421240f6158229bc82d'
EXPECTED = {10: 15, 11: 15, 12: 18}
HORSE_URL = re.compile(r'https://db\.netkeiba\.com/horse/([0-9A-Za-z]{10})/?')
CLOCK = re.compile(r'\d+:[0-5]\d\.\d')
JST = ZoneInfo('Asia/Tokyo')


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def normalized_name(name):
    return unicodedata.normalize('NFC', name.strip())


def read_jra(archive_path, key, expected_count):
    with archive_path.open('rb') as source:
        if hashlib.file_digest(source, 'sha256').hexdigest() != INPUT_SHA:
            raise ValueError('Existing JRA input ZIP differs from its recorded checksum')
        source.seek(0)
        with zipfile.ZipFile(source) as archive:
            with archive.open('structured/races.jsonl') as data:
                race = [row for line in data if (row := json.loads(line))['local_race_key'] == key]
            with archive.open('structured/entries.jsonl') as data:
                entries = [row for line in data if (row := json.loads(line))['local_race_key'] == key]
    if len(race) != 1 or race[0]['entry_rows'] != expected_count or len(entries) != expected_count:
        raise ValueError('JRA input does not have the expected race and runner counts')
    digest = sha256(PDF.read_bytes())
    if any(row['source_sha256'] != digest for row in [race[0], *entries]):
        raise ValueError('Local JRA PDF SHA differs from the input records')
    if len({row['horse_number'] for row in entries}) != expected_count:
        raise ValueError('Duplicate JRA horse number')
    return race[0], entries


def read_browser_result(raw, race_id, number, expected_count):
    document = html.fromstring(raw, parser=html.HTMLParser(encoding='utf-8', no_network=True))
    url = f'https://race.netkeiba.com/race/result.html?race_id={race_id}'
    title = document.xpath('string(/html/head/title)').strip()
    if document.xpath('//link[@rel="canonical"]/@href') != [url] or f'2026年2月1日 小倉{number}R' not in title:
        raise ValueError('The saved result does not identify the intended race')
    table_rows = document.xpath('//table[@id="All_Result_Table"]/tbody/tr[td]')
    if len(table_rows) != expected_count:
        raise ValueError('Unexpected result row count')
    rows = []
    for row in table_rows:
        cells = row.xpath('./td')
        links = cells[3].xpath('.//a[@href]')
        if len(links) != 1 or not (horse_id := HORSE_URL.fullmatch(links[0].get('href'))):
            raise ValueError('A result row lacks an unambiguous horse profile link')
        rows.append({
            'race_id': race_id,
            'horse_number': int(cells[2].text_content().strip()),
            'horse_name': links[0].text_content().strip(),
            'finish_position': int(cells[0].text_content().strip()),
            'time_raw': cells[7].text_content().strip(),
            'horse_profile_href': links[0].get('href'),
            'netkeiba_horse_id_candidate': horse_id.group(1),
        })
    if any(len({row[field] for row in rows}) != expected_count for field in ('horse_number', 'netkeiba_horse_id_candidate')):
        raise ValueError('Duplicate browser horse number or profile ID within the race')
    return url, title, rows


def compare(jra_entries, browser_rows, local_key):
    by_number = {row['horse_number']: row for row in browser_rows}
    matched, held, ditto_review = [], [], []
    previous_clock = None
    for entry in jra_entries:
        browser = by_number.pop(entry['horse_number'], None)
        if (browser is None or normalized_name(browser['horse_name']) != normalized_name(entry['horse_name'])
                or browser['finish_position'] != entry['finish_position'] or entry['status'] != 'finished'):
            raise ValueError(f'Race, number, name, rank or status conflict at horse {entry["horse_number"]}')
        raw_clock = entry['finish_time_raw']
        comparison = {**browser, 'jra_local_race_key': local_key, 'jra_time_raw': raw_clock,
                      'jra_time_basis': entry['finish_time_basis'], 'identity_verified': False,
                      'prediction_time_verified': False}
        if raw_clock == '〃':
            held.append({**comparison, 'hold_reason': 'jra_raw_ditto'})
            ditto_review.append({**comparison, 'reference_clock': previous_clock,
                                 'clock_matches': previous_clock is not None and browser['time_raw'] == previous_clock})
        elif CLOCK.fullmatch(raw_clock):
            previous_clock = raw_clock
            if raw_clock == browser['time_raw']:
                matched.append(comparison)
            else:
                held.append({**comparison, 'hold_reason': 'numeric_clock_difference'})
        else:
            previous_clock = None
            held.append({**comparison, 'hold_reason': 'unsupported_jra_time_format'})
    return matched, held, ditto_review


def save_result(number, archive_path, raw_path, navigation_started_at):
    destination = DAY / f'{number}R'
    if destination.exists() or destination.is_symlink():
        raise ValueError('A checkpoint already exists for this race')
    if any(not (DAY / f'{earlier}R' / 'receipt.json').is_file() for earlier in range(10, number)):
        raise ValueError('Save the preceding confirmed-unsaved races first')
    race_id = f'2026100104{number:02}'
    local_key = f'jra-local:2026-02-01:kokura:{number:02}'
    expected_count = EXPECTED[number]
    raw = raw_path.read_bytes()
    browser_saved_at = datetime.fromtimestamp(raw_path.stat().st_mtime, JST)
    race, entries = read_jra(archive_path, local_key, expected_count)
    url, title, rows = read_browser_result(raw, race_id, number, expected_count)
    matched, held, ditto_review = compare(entries, rows, local_key)

    DAY.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.race-staging-', dir=DAY) as directory:
        stage = Path(directory) / 'checkpoint'
        stage.mkdir()
        (stage / 'raw').mkdir()
        (stage / 'raw' / 'result.html').write_bytes(raw)
        for name, records in (('netkeiba', rows), ('entries', entries), ('matched_candidates', matched),
                              ('held', held), ('ditto_review', ditto_review)):
            (stage / f'{name}.jsonl').write_text(''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in records), encoding='utf-8')
        file_hashes = {path.relative_to(stage).as_posix(): sha256(path.read_bytes())
                       for path in sorted(stage.rglob('*')) if path.is_file()}
        receipt = {
            'state': 'saved_heroines_pending_source_merge', 'host': socket.gethostname(),
            'navigation_started_at_jst': navigation_started_at.astimezone(JST).isoformat(),
            'browser_source_saved_at_jst': browser_saved_at.isoformat(), 'saved_at_jst': datetime.now(JST).isoformat(),
            'source_url': url, 'title': title, 'source_browser': 'Safari page source',
            'race_id': race_id, 'jra_local_race_key': local_key,
            'source_jra_url': race['source_url'], 'source_pdf_path': str(PDF), 'source_pdf_sha256': race['source_sha256'],
            'source_pdf_page': race['source_page'], 'source_pdf_column': race['source_column'], 'jra_input_zip_sha256': INPUT_SHA,
            'rows': len(rows), 'profile_id_candidates': len(rows), 'raw_clock_matches': len(matched),
            'raw_clock_holds': len(held), 'ditto_review_rows': len(ditto_review),
            'ditto_review_matches': sum(row['clock_matches'] for row in ditto_review),
            'identity_verification': 'pending', 'prediction_time_verification': 'pending',
            'handoff_basis': 'user-reported unsaved race; source checkpoint reconciliation pending',
            'files_sha256': file_hashes,
        }
        (stage / 'receipt.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        stage.rename(destination)
    saved = json.loads((destination / 'receipt.json').read_text(encoding='utf-8'))
    if saved != receipt or any(sha256((destination / path).read_bytes()) != digest for path, digest in file_hashes.items()):
        raise ValueError('Checkpoint reread differs from the saved receipt or hashed files')
    return {**saved, 'path': str(destination)}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('number', type=int, choices=EXPECTED)
    parser.add_argument('jra_archive', type=Path)
    parser.add_argument('raw_html', type=Path)
    parser.add_argument('navigation_started_at', type=datetime.fromisoformat)
    args = parser.parse_args()
    if args.navigation_started_at.utcoffset() is None:
        parser.error('navigation_started_at must include a timezone')
    print(json.dumps(save_result(args.number, args.jra_archive, args.raw_html, args.navigation_started_at), ensure_ascii=False, indent=2))
