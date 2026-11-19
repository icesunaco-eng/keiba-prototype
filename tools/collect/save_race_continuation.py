"""Verify one saved Safari race result against the JRA archive and store its receipt."""

import argparse
from datetime import date, datetime
import hashlib
import json
from pathlib import Path
import re
import socket
import tempfile
import unicodedata
import zipfile

from lxml import html

from save_kokura_continuation import CLOCK, HORSE_URL, INPUT_SHA, JST, ROOT, normalized_name, sha256


OUTPUT = ROOT / 'HEROINES並行取得'
FIRST_DATE = date(2026, 2, 7)
LAST_DATE = date(2026, 3, 15)
VENUES = {'東京', '中山', '中京', '京都', '阪神', '小倉', '新潟', '福島', '函館', '札幌'}
QUEUE_FIELDS = ('race_date', 'venue', 'race_number', 'local_race_key', 'entry_rows',
                'source_url', 'source_sha256', 'source_page', 'source_column')
NON_FINISH = {'取': 'cancelled', '取消': 'cancelled', '出走取消': 'cancelled',
              '除': 'excluded', '除外': 'excluded', '競走除外': 'excluded',
              '中': 'did_not_finish', '中止': 'did_not_finish', '競走中止': 'did_not_finish',
              '失': 'disqualified', '失格': 'disqualified'}
SECONDS_ONLY = re.compile(r'(?:[0-5]?\d)\.\d')


def verified_pdf(path, expected_sha):
    with path.open('rb') as source:
        if source.read(5) != b'%PDF-':
            raise ValueError('Local JRA file is not a PDF')
        source.seek(0)
        if hashlib.file_digest(source, 'sha256').hexdigest() != expected_sha:
            raise ValueError('Local JRA PDF SHA differs from the pinned JRA race record')


def read_jra(archive_path, expected):
    key = expected['local_race_key']
    with archive_path.open('rb') as source:
        if hashlib.file_digest(source, 'sha256').hexdigest() != INPUT_SHA:
            raise ValueError('JRA input ZIP differs from its recorded checksum')
        source.seek(0)
        with zipfile.ZipFile(source) as archive:
            with archive.open('structured/races.jsonl') as data:
                races = [row for line in data if (row := json.loads(line))['local_race_key'] == key]
            with archive.open('structured/entries.jsonl') as data:
                entries = [row for line in data if (row := json.loads(line))['local_race_key'] == key]
    if len(races) != 1 or any(races[0][field] != expected[field] for field in QUEUE_FIELDS):
        raise ValueError('The queue row differs from the pinned JRA race record')
    if len(entries) != expected['entry_rows'] or len({row['horse_number'] for row in entries}) != len(entries):
        raise ValueError('Unexpected JRA row count or duplicate horse number')
    if any(row['source_sha256'] != expected['source_sha256'] for row in entries):
        raise ValueError('The runner rows refer to a different JRA PDF')
    return entries


def read_browser_header(raw, race_id):
    if not re.fullmatch(r'2026\d{8}', race_id):
        raise ValueError('Expected the 12-digit netkeiba race ID observed in Safari')
    document = html.fromstring(raw, parser=html.HTMLParser(encoding='utf-8', no_network=True))
    url = f'https://race.netkeiba.com/race/result.html?race_id={race_id}'
    title = document.xpath('string(/html/head/title)').strip()
    if document.xpath('//link[@rel="canonical"]/@href') != [url]:
        raise ValueError('The saved result does not have the expected canonical race URL')
    header = re.search(r'\|\s*(\d{4})年(\d{1,2})月(\d{1,2})日\s+(\S+?)(\d{1,2})R\s',
                       unicodedata.normalize('NFKC', title))
    if not header:
        raise ValueError('The browser title does not identify a race date, venue and race number')
    year, month, day, venue, number = header.groups()
    race_date = date(int(year), int(month), int(day))
    race_number = int(number)
    if (not FIRST_DATE <= race_date <= LAST_DATE or venue not in VENUES or not 1 <= race_number <= 12
            or race_id[-2:] != f'{race_number:02}'):
        raise ValueError('The browser race is outside the remaining assignment or has inconsistent identifiers')
    return document, url, title, race_date, venue, race_number


def get_queue_row(path, race_date, venue, number):
    contents = json.loads(path.read_text(encoding='utf-8'))
    rows = contents['races'] if isinstance(contents, dict) else contents
    matches = [row for row in rows if (row['race_date'], row['venue'], row['race_number'])
               == (race_date.isoformat(), venue, number)]
    if len(matches) != 1:
        raise ValueError('Expected exactly one matching race in the verified continuation queue')
    return matches[0]


def read_browser_rows(document, race_id):
    table_rows = document.xpath('//table[@id="All_Result_Table"]/tbody/tr[td]')
    if not table_rows:
        raise ValueError('No complete netkeiba race results table was found')
    rows = []
    for row in table_rows:
        cells = row.xpath('./td')
        if len(cells) < 8:
            raise ValueError('Unexpected row in the netkeiba race results table')
        links = cells[3].xpath('.//a[@href]')
        if len(links) > 1:
            raise ValueError('More than one link appears in the runner name cell')
        href = links[0].get('href') if links else None
        horse_id = HORSE_URL.fullmatch(href) if href else None
        rank_raw = cells[0].text_content().strip()
        rank = unicodedata.normalize('NFKC', rank_raw)
        position = int(rank) if re.fullmatch(r'[0-9]{1,2}', rank) else None
        rows.append({
            'race_id': race_id,
            'horse_number': int(cells[2].text_content().strip()),
            'horse_name': (links[0] if links else cells[3]).text_content().strip(),
            'finish_position': position,
            'finish_position_raw': rank_raw,
            'status': 'finished' if position is not None else NON_FINISH.get(rank, 'unknown'),
            'time_raw': cells[7].text_content().strip(),
            'horse_profile_href': href,
            'netkeiba_horse_id_candidate': horse_id.group(1) if horse_id else None,
        })
    numbers = [row['horse_number'] for row in rows]
    ids = [row['netkeiba_horse_id_candidate'] for row in rows if row['netkeiba_horse_id_candidate']]
    if len(set(numbers)) != len(numbers) or len(set(ids)) != len(ids):
        raise ValueError('Duplicate browser horse number or candidate profile ID in this race')
    return rows


def normalized_reference_time(raw_clock):
    if raw_clock and CLOCK.fullmatch(raw_clock):
        return raw_clock
    if raw_clock and SECONDS_ONLY.fullmatch(raw_clock):
        return f'0:{raw_clock.zfill(4)}'
    return None


def compare(jra_entries, browser_rows, local_key, race_id):
    by_number = {row['horse_number']: row for row in browser_rows}
    jra_numbers = {entry['horse_number'] for entry in jra_entries}
    if set(by_number) - jra_numbers:
        raise ValueError('The browser result includes numbers absent from the JRA runner records')

    matched, held, ditto_review = [], [], []
    previous_clock = None
    for entry in jra_entries:
        number = entry['horse_number']
        browser = by_number.get(number)
        raw_clock = entry['finish_time_raw']
        numeric_clock = raw_clock is not None and CLOCK.fullmatch(raw_clock)
        comparison = {
            'race_id': race_id, 'horse_number': number, **(browser or {}),
            'jra_local_race_key': local_key, 'jra_horse_name': entry['horse_name'],
            'jra_finish_position': entry['finish_position'], 'jra_status': entry['status'],
            'jra_time_raw': raw_clock, 'jra_time_basis': entry['finish_time_basis'],
            'identity_verified': False, 'prediction_time_verified': False,
        }
        reasons = []
        if browser is None:
            if entry['status'] == 'finished':
                raise ValueError(f'Finished horse {number} is absent from the browser result')
            reasons.append('not_listed_in_browser_result')
        else:
            if normalized_name(browser['horse_name']) != normalized_name(entry['horse_name']):
                reasons.append('horse_name_difference')
            if browser['finish_position'] != entry['finish_position']:
                reasons.append('finish_position_difference')
            if browser['status'] != entry['status']:
                reasons.append('jra_browser_status_difference')
            if browser['netkeiba_horse_id_candidate'] is None:
                reasons.append('horse_profile_id_not_observed')
        if entry['status'] != 'finished':
            reasons.append(f'jra_{entry["status"]}')
        elif raw_clock == '〃':
            reasons.append('jra_raw_ditto')
            reference = normalized_reference_time(previous_clock)
            displayed = normalized_reference_time(browser['time_raw']) if browser else None
            ditto_review.append({**comparison, 'reference_clock': previous_clock,
                                 'reference_clock_normalized': reference,
                                 'clock_matches': displayed == reference if displayed and reference else None})
        elif not numeric_clock:
            reasons.append('unsupported_jra_time_format')
        elif browser and raw_clock != browser['time_raw']:
            reasons.append('numeric_clock_difference')

        if reasons:
            held.append({**comparison, 'hold_reason': reasons[0], 'hold_reasons': reasons})
        else:
            matched.append(comparison)
        if raw_clock != '〃':
            previous_clock = raw_clock if entry['status'] == 'finished' else None

    return matched, held, ditto_review


def write_jsonl(path, rows):
    with path.open('w', encoding='utf-8') as output:
        for row in rows:
            output.write(json.dumps(row, ensure_ascii=False) + '\n')


def save_result(queue, archive, pdf, raw_path, race_id, navigation_started_at,
                navigation_time_basis='browser_control_start'):
    raw = raw_path.read_bytes()
    browser_saved_at = datetime.fromtimestamp(raw_path.stat().st_mtime, JST)
    document, url, title, race_date, venue, number = read_browser_header(raw, race_id)
    expected = get_queue_row(queue, race_date, venue, number)
    destination = OUTPUT / race_date.isoformat() / venue / f'{number}R'
    if destination.exists() or destination.is_symlink():
        raise ValueError('A checkpoint already exists for this race')
    local_key = expected['local_race_key']
    for prior_path in OUTPUT.glob('*/*/*/receipt.json'):
        prior = json.loads(prior_path.read_text(encoding='utf-8'))
        if prior['race_id'] == race_id or prior['jra_local_race_key'] == local_key:
            raise ValueError(f'The race key or ID is already in {prior_path}')
    entries = read_jra(archive, expected)
    verified_pdf(pdf, expected['source_sha256'])
    browser_rows = read_browser_rows(document, race_id)
    matched, held, ditto_review = compare(entries, browser_rows, local_key, race_id)
    counts = {
        'rows': len(entries), 'browser_result_rows': len(browser_rows),
        'profile_id_candidates': sum(bool(row['netkeiba_horse_id_candidate']) for row in browser_rows),
        'raw_clock_matches': len(matched), 'raw_clock_holds': len(held),
        'ditto_review_rows': len(ditto_review), 'ditto_review_matches': sum(row['clock_matches'] is True for row in ditto_review),
    }

    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.race-staging-', dir=destination.parent) as directory:
        stage = Path(directory) / 'checkpoint'
        (stage / 'raw').mkdir(parents=True)
        (stage / 'raw' / 'result.html').write_bytes(raw)
        for name, rows in (('netkeiba', browser_rows), ('entries', entries), ('matched_candidates', matched),
                           ('held', held), ('ditto_review', ditto_review)):
            write_jsonl(stage / f'{name}.jsonl', rows)
        hashes = {path.relative_to(stage).as_posix(): sha256(path.read_bytes())
                  for path in sorted(stage.rglob('*')) if path.is_file()}
        receipt = {
            'state': 'saved_heroines_pending_source_merge', 'host': socket.gethostname(),
            'navigation_started_at_jst': navigation_started_at.astimezone(JST).isoformat(),
            'navigation_time_basis': navigation_time_basis,
            'browser_source_saved_at_jst': browser_saved_at.isoformat(), 'saved_at_jst': datetime.now(JST).isoformat(),
            'source_url': url, 'title': title, 'source_browser': 'Safari page source',
            'race_id': race_id, 'jra_local_race_key': local_key,
            'source_jra_url': expected['source_url'], 'source_pdf_path': str(pdf.resolve()),
            'source_pdf_sha256': expected['source_sha256'], 'source_pdf_page': expected['source_page'],
            'source_pdf_column': expected['source_column'], 'jra_input_zip_sha256': INPUT_SHA,
            **counts, 'identity_verification': 'pending', 'prediction_time_verification': 'pending',
            'handoff_basis': 'remaining assignment queue verified against JRA input; source checkpoint reconciliation pending',
            'files_sha256': hashes,
        }
        (stage / 'receipt.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        stage.rename(destination)

    readback = json.loads((destination / 'receipt.json').read_text(encoding='utf-8'))
    if readback != receipt or any(sha256((destination / name).read_bytes()) != digest for name, digest in hashes.items()):
        raise ValueError('Checkpoint reread differs from the saved receipt or hashed artifacts')
    return {**readback, 'path': str(destination)}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('queue', type=Path)
    parser.add_argument('jra_archive', type=Path)
    parser.add_argument('local_pdf', type=Path)
    parser.add_argument('raw_html', type=Path)
    parser.add_argument('netkeiba_race_id')
    parser.add_argument('navigation_started_at', type=datetime.fromisoformat)
    parser.add_argument('--navigation-time-basis',
                        choices=('browser_control_start', 'safari_main_frame_log'),
                        default='browser_control_start')
    args = parser.parse_args()
    if args.navigation_started_at.utcoffset() is None:
        parser.error('navigation_started_at must include a timezone')
    result = save_result(args.queue, args.jra_archive, args.local_pdf, args.raw_html,
                         args.netkeiba_race_id, args.navigation_started_at, args.navigation_time_basis)
    print(json.dumps(result, ensure_ascii=False, indent=2))
