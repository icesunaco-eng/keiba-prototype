"""Audit local race receipts and rebuild the HEROINES continuation progress."""

import argparse
from datetime import date, datetime
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile


from _paths import collect_root
ROOT = collect_root()
OUTPUT = ROOT / 'HEROINES並行取得'
PROGRESS = OUTPUT / 'acquisition_progress.json'
QUEUE = OUTPUT / 'continuation_queue.json'
DONOR = {'races': 1893, 'rows': 25903}
ASSIGNMENT = {'races': 2298, 'rows': 31700}
FIRST_RACES = {f'jra-local:2026-02-01:kokura:{number}': rows
               for number, rows in ((10, 15), (11, 15), (12, 18))}
VENUES = {'札幌': 'sapporo', '函館': 'hakodate', '福島': 'fukushima', '新潟': 'niigata',
          '東京': 'tokyo', '中山': 'nakayama', '中京': 'chukyo', '京都': 'kyoto',
          '阪神': 'hanshin', '小倉': 'kokura'}
TABLES = ('entries', 'netkeiba', 'matched_candidates', 'held', 'ditto_review')
ARTIFACTS = {f'{name}.jsonl' for name in TABLES} | {'raw/result.html'}
INDEX_FIELDS = ('race_id', 'jra_local_race_key', 'navigation_started_at_jst',
                'saved_at_jst', 'rows', 'profile_id_candidates', 'raw_clock_matches',
                'raw_clock_holds', 'ditto_review_matches')


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def local_bytes(path):
    require(path.resolve() == path, f'Symlink or noncanonical path: {path}')
    return path.read_bytes()


def queue_keys(records):
    keys = []
    for race in records:
        day = date.fromisoformat(race['race_date'])
        number = race['race_number']
        venue = VENUES.get(race['venue'])
        key = race['local_race_key']
        require(type(number) is int and 1 <= number <= 12 and venue is not None
                and date(2026, 2, 7) <= day <= date(2026, 3, 15)
                and key == f'jra-local:{day}:{venue}:{number:02}', f'Invalid queue key: {key}')
        keys.append(key)
    require(len(set(keys)) == len(keys), 'Duplicate local key in queue records')
    return keys


def horse_numbers(rows, race_field, race_key):
    numbers = set()
    for row in rows:
        number = row['horse_number']
        require(row[race_field] == race_key and type(number) is int and 1 <= number <= 18
                and number not in numbers, f'Wrong race key or duplicate/invalid horse number: {race_key}')
        numbers.add(number)
    return numbers


def audit_receipt(path, queue_by_key, archive_sha, verified_pdfs, owner):
    receipt_bytes = local_bytes(path)
    receipt = json.loads(receipt_bytes)
    race_id, key = receipt['race_id'], receipt['jra_local_race_key']
    day, venue, race_directory, _ = path.relative_to(OUTPUT).parts
    number = int(race_directory.removesuffix('R'))
    require(venue in VENUES and race_directory == f'{number}R'
            and key == f'jra-local:{day}:{VENUES[venue]}:{number:02}'
            and re.fullmatch(r'2026[0-9]{8}', race_id) and race_id.endswith(f'{number:02}'),
            f'Receipt identity differs from its path: {path}')
    require(key in FIRST_RACES or key in queue_by_key, f'Receipt outside allowed queue: {key}')
    require(receipt['host'] == owner and receipt['state'] == 'saved_heroines_pending_source_merge'
            and receipt['source_url'] == f'https://race.netkeiba.com/race/result.html?race_id={race_id}'
            and receipt['jra_input_zip_sha256'] == archive_sha
            and receipt['identity_verification'] == receipt['prediction_time_verification'] == 'pending',
            f'Invalid receipt provenance or verification state: {key}')

    started = datetime.fromisoformat(receipt['navigation_started_at_jst'])
    captured = datetime.fromisoformat(receipt['browser_source_saved_at_jst'])
    saved = datetime.fromisoformat(receipt['saved_at_jst'])
    require(all(t.utcoffset() is not None for t in (started, captured, saved))
            and started <= captured <= saved, f'Invalid receipt event order: {key}')

    source_url = receipt['source_jra_url']
    filename = source_url.rsplit('/', 1)[-1]
    pdf = ROOT / '原本' / 'JRA' / '2026' / filename
    source_sha = receipt['source_pdf_sha256']
    require(source_url == f'https://jra.jp/datafile/seiseki/report/2026/{filename}'
            and re.fullmatch(r'2026-[1-9][a-z]+[0-9]+\.pdf', filename)
            and Path(receipt['source_pdf_path']) == pdf, f'Wrong local JRA PDF path: {key}')
    if pdf not in verified_pdfs:
        data = local_bytes(pdf)
        require(data.startswith(b'%PDF-'), f'JRA file is not a PDF: {pdf}')
        verified_pdfs[pdf] = digest(data)
    require(verified_pdfs[pdf] == source_sha, f'JRA PDF checksum mismatch: {key}')

    expected = queue_by_key.get(key)
    if expected is not None:
        require((source_url, source_sha, receipt['source_pdf_page'], receipt['source_pdf_column'])
                == tuple(expected[field] for field in ('source_url', 'source_sha256', 'source_page', 'source_column')),
                f'JRA provenance differs from queue: {key}')

    hashes = receipt['files_sha256']
    require(set(hashes) == ARTIFACTS, f'Incomplete or unexpected artifact list: {key}')
    tables = {}
    for name, checksum in hashes.items():
        data = local_bytes(path.parent / name)
        require(digest(data) == checksum, f'Artifact checksum mismatch: {key} {name}')
        if name.endswith('.jsonl'):
            tables[name.removesuffix('.jsonl')] = [json.loads(line) for line in data.splitlines()]

    entries = horse_numbers(tables['entries'], 'local_race_key', key)
    browser = horse_numbers(tables['netkeiba'], 'race_id', race_id)
    matched, held, ditto = [horse_numbers(tables[name], 'jra_local_race_key', key)
                            for name in ('matched_candidates', 'held', 'ditto_review')]
    require(entries == matched | held and not matched & held and browser <= entries and ditto <= held,
            f'Invalid result, held, or ditto partition: {key}')
    for name in ('matched_candidates', 'held', 'ditto_review'):
        require(all(row['race_id'] == race_id and row['identity_verified'] is False
                    and row['prediction_time_verified'] is False for row in tables[name]),
                f'Unexpected identity, prediction-time, or race state in {name}: {key}')
    for row in tables['entries']:
        require((row['race_date'], row['venue'], row['race_number'], row['source_url'], row['source_sha256'],
                 row['source_page'], row['source_column'])
                == (day, venue, number, source_url, source_sha, receipt['source_pdf_page'], receipt['source_pdf_column']),
                f'JRA runner provenance differs from receipt: {key}')

    candidates = []
    for row in tables['netkeiba']:
        candidate = row['netkeiba_horse_id_candidate']
        if candidate is not None:
            link = row['horse_profile_href']
            require(re.fullmatch(r'[0-9A-Za-z]{10}', candidate) and isinstance(link, str)
                    and link.rstrip('/') == f'https://db.netkeiba.com/horse/{candidate}',
                    f'Candidate ID differs from horse profile link: {key}')
            candidates.append(candidate)
    require(len(set(candidates)) == len(candidates), f'Duplicate horse candidate ID in race: {key}')
    counts = dict(rows=len(entries), profile_id_candidates=len(candidates), raw_clock_matches=len(matched),
                  raw_clock_holds=len(held), ditto_review_rows=len(ditto),
                  ditto_review_matches=sum(row['clock_matches'] is True for row in tables['ditto_review']))
    require(all(receipt[field] == count for field, count in counts.items())
            and receipt.get('browser_result_rows', len(browser)) == len(browser)
            and len(entries) == (expected['entry_rows'] if expected else FIRST_RACES[key]),
            f'Receipt or queue counts do not agree: {key}')
    summary = {field: receipt[field] for field in INDEX_FIELDS}
    summary.update(receipt_path=path.relative_to(OUTPUT).as_posix(), receipt_sha256=digest(receipt_bytes))
    return summary, candidates


def rebuild(progress, queue, paths, state):
    require(progress['queue_path'] == QUEUE.name and progress['host'] == 'HEROINES.local', 'Unexpected progress context')
    require(progress['myu_pre_pause_reported'] == queue['prior_saved_prefix_inferred_from_user_shutdown_report']
            == DONOR and queue['source_checkpoint_reconciliation_pending'] is True, 'Reported donor totals or status changed')
    queue_rows = queue['races']
    queue_order = queue_keys(queue_rows)
    canceled = queue_keys(queue['canceled_outside_conducted_races'])
    require(queue['existing_local_race_keys'] == list(FIRST_RACES) and not set(canceled) & set(queue_order),
            'Queue bootstrap keys changed or a canceled race entered the collection queue')
    require(len(queue_order) + len(FIRST_RACES) == ASSIGNMENT['races'] - DONOR['races']
            and sum(row['entry_rows'] for row in queue_rows) + sum(FIRST_RACES.values())
            == ASSIGNMENT['rows'] - DONOR['rows'], 'Queue coverage differs from the assigned race or row totals')
    dates = [row['race_date'] for row in queue_rows]
    require(dates == sorted(dates) and dates[0] == '2026-02-07' and dates[-1] == '2026-03-15',
            'Queue dates differ from the approved continuation window or order')
    queue_by_key = dict(zip(queue_order, queue_rows, strict=True))

    receipts = {}
    race_ids, all_candidates, verified_pdfs = set(), set(), {}
    for path in paths:
        row, candidates = audit_receipt(path, queue_by_key, queue['source_archive_sha256'], verified_pdfs, progress['host'])
        key = row['jra_local_race_key']
        require(key not in receipts and row['race_id'] not in race_ids, f'Duplicate race key or race ID: {key}')
        receipts[key] = row
        race_ids.add(row['race_id'])
        all_candidates.update(candidates)

    order = [*FIRST_RACES, *queue_order]
    prefix = order[:len(receipts)]
    require(len(receipts) >= len(FIRST_RACES) and set(prefix) == set(receipts), 'Saved receipts are not a contiguous queue prefix')
    index = [receipts[key] for key in prefix]
    old_receipts = progress['receipts']
    require(old_receipts == index[:len(old_receipts)], 'Previously indexed receipt data or checksums changed')
    for previous, current in zip(index, index[1:]):
        old_start = datetime.fromisoformat(previous['navigation_started_at_jst'])
        new_start = datetime.fromisoformat(current['navigation_started_at_jst'])
        require((new_start - old_start).total_seconds() >= 10
                and datetime.fromisoformat(previous['saved_at_jst']) <= new_start,
                f'Acquisition interval or save order differs at {current["race_id"]}')

    local = {'races': len(index), 'rows': sum(row['rows'] for row in index),
             'profile_id_candidate_rows': sum(row['profile_id_candidates'] for row in index),
             'distinct_candidate_ids': len(all_candidates),
             'original_clock_matches': sum(row['raw_clock_matches'] for row in index),
             'original_clock_holds': sum(row['raw_clock_holds'] for row in index),
             'separate_ditto_matches': sum(row['ditto_review_matches'] for row in index)}
    summed = {field: count + local[field] for field, count in DONOR.items()}
    remaining = {field: count - summed[field] for field, count in ASSIGNMENT.items()}
    next_race = queue_by_key.get(order[len(index)]) if len(index) < len(order) else None
    return {**progress, 'state': progress['state'] if state is None else state, 'receipts': index,
            'heroines_only': local, 'combined_provisional': summed, 'remaining': remaining,
            'next_race': next_race, 'final_race_in_queue': queue_rows[-1]}


def receipt_paths():
    return sorted(OUTPUT.glob('*/*/*/receipt.json'))


def write_progress(updated, previous, queue_bytes, paths):
    previous_mode = stat.S_IMODE(PROGRESS.stat().st_mode)
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=OUTPUT, prefix='.progress-', delete=False) as stream:
        staged = Path(stream.name)
        try:
            json.dump(updated, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
            stream.flush()
            os.fchmod(stream.fileno(), previous_mode)
            os.fsync(stream.fileno())
            require(local_bytes(PROGRESS) == previous and local_bytes(QUEUE) == queue_bytes
                    and receipt_paths() == paths, 'Progress inputs changed during reindex. Run the audit again.')
            os.replace(staged, PROGRESS)
        finally:
            staged.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--write', action='store_true', help='Atomically replace acquisition_progress.json after verification.')
    parser.add_argument('--state', help='Set the progress state. Omit to retain the current state.')
    args = parser.parse_args()
    try:
        previous, queue_bytes, paths = local_bytes(PROGRESS), local_bytes(QUEUE), receipt_paths()
        updated = rebuild(json.loads(previous), json.loads(queue_bytes), paths, args.state)
        if args.write:
            write_progress(updated, previous, queue_bytes, paths)
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.exit(1, f'Progress reindex refused: {error}\n')
    summary = {key: updated[key] for key in ('state', 'heroines_only', 'combined_provisional', 'remaining', 'next_race')}
    print(json.dumps({'written': args.write, **summary}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
