"""Audit and save one ordered 2025 race from a local Safari source file."""

import argparse
from collections import Counter, defaultdict
from contextlib import contextmanager
from datetime import datetime
import fcntl
from io import BytesIO
import json
import os
from pathlib import Path
import re
import socket
import stat
import tempfile
import unicodedata
import zipfile

from lxml import html

from save_kokura_continuation import CLOCK, INPUT_SHA, JST, ROOT, normalized_name, sha256
from save_race_continuation import normalized_reference_time, read_browser_rows, verified_pdf, write_jsonl


from _paths import heroines_2025_artifacts, jra_pdf_root, prior_jra_archive
ARTIFACTS = heroines_2025_artifacts()
PDF_ROOT = jra_pdf_root()
PRIOR_OUTPUT = ROOT / 'HEROINES並行取得'
OUTPUT = ROOT / 'HEROINES並行取得_2025-06-01_2025-07-13'
PRIOR_JRA_ARCHIVE = prior_jra_archive()
MYU_REPORTED_RACE_KEYS_SHA = '31bc64a2e703e04f7413252132d90c02a37a2dbedad24475b03a833cb2b75916'
INPUT_HASHES = {
    'queue.json': '8b8110885e6ba6ebec777669c203f8dcba643880a7bb627dd8d75d25e5f80cd1',
    'entries.jsonl': '38c75ba716f89bb7a83872aa396d4556db06812bc45da9d985ebeaa5bd3d001d',
    'validation.json': '92bfc42a62710903cc17c1a0ea0386327b45f397127dc2522df362cfef503856',
}
VENUES = {
    '函館': ('hakodate', '02'), '福島': ('fukushima', '03'), '東京': ('tokyo', '05'),
    '京都': ('kyoto', '08'), '阪神': ('hanshin', '09'), '小倉': ('kokura', '10'),
}
FIRST_DATE, LAST_DATE = '2025-06-01', '2025-07-13'
SOURCE_BASE = 'https://jra.jp/datafile/seiseki/report/2025/'
RESULT_BASE = 'https://race.netkeiba.com/race/result.html?race_id='
STATUS_FOR_BROWSER = {
    'finished': 'finished', 'withdrawn': 'cancelled',
    'excluded': 'excluded', 'did_not_finish': 'did_not_finish',
}
SAVED_FILES = {'raw/result.html', 'entries.jsonl', 'netkeiba.jsonl', 'comparisons.jsonl'}
MAX_BATCH_CAPTURES = 6


def require(condition, message):
    if not condition:
        raise ValueError(message)


def read_pinned(name):
    raw = (ARTIFACTS / name).read_bytes()
    require(sha256(raw) == INPUT_HASHES[name], f'Prepared {name} SHA-256 mismatch')
    return raw


def race_details(race):
    venue, code = VENUES[race['venue']]
    day, number = race['race_date'], race['race_number']
    require(FIRST_DATE <= day <= LAST_DATE and 1 <= number <= 12,
            'Race falls outside the assigned dates or numbering')
    require(race['local_race_key'] == f'jra-local:{day}:{venue}:{number:02}'
            and race['legacy_race_key'] == f"{day}|{race['venue']}|{number}",
            'Race key differs from the prepared local date, venue or race number')
    filename = race['source_url'].removeprefix(SOURCE_BASE)
    meeting = re.fullmatch(rf'2025-(\d{{1,2}}){venue}(\d{{1,2}})\.pdf', filename)
    require(meeting is not None and race['source_url'] == SOURCE_BASE + filename,
            'Official PDF URL does not identify the venue and meeting')
    pdf = PDF_ROOT / filename
    require(pdf == Path(race['local_original_pdf_path']) and pdf.resolve() == pdf
            and race['source_column'] in ('left', 'right') and race['source_page'] >= 1,
            'Local PDF path or page differs from the prepared source locator')
    meet, meeting_day = map(int, meeting.groups())
    return f'2025{code}{meet:02}{meeting_day:02}{number:02}', pdf


def read_inputs():
    queue = json.loads(read_pinned('queue.json'))
    entries = [json.loads(line) for line in read_pinned('entries.jsonl').splitlines()]
    races = queue['races']
    require(queue['schema'] == 'heroines-432-offline-v1'
            and len(races) == 432 and len(entries) == 5846, 'Unexpected prepared input scope')
    by_race = defaultdict(list)
    for entry in entries:
        by_race[entry['local_race_key']].append(entry)
    race_keys, race_ids, originals = set(), set(), {}
    for race in races:
        race_id, pdf = race_details(race)
        key = race['local_race_key']
        require(key not in race_keys and race_id not in race_ids, f'Duplicate prepared race {key}')
        race_keys.add(key)
        race_ids.add(race_id)
        expected = (race['race_date'], race['venue'], race['race_number'], race['legacy_race_key'],
                    race['source_url'], race['source_sha256'], race['source_page'],
                    '左' if race['source_column'] == 'left' else '右', str(pdf))
        rows = by_race[key]
        require(len(rows) == race['entry_rows'] == len({row['horse_number'] for row in rows}),
                f'Repeated or missing JRA horse numbers in {key}')
        for row in rows:
            locator = tuple(row[field] for field in (
                'date', 'venue', 'race_no', 'race_key', 'source_url', 'source_pdf_sha256',
                'source_page', 'source_side_raw', 'local_original_pdf_path'))
            require(locator == expected and row['status'] in STATUS_FOR_BROWSER
                    and row['runner_key'] == f"{row['race_key']}|{row['horse_number']}",
                    f'Runner identity or source differs from its prepared race {key}')
            require(row['finish_position_raw'] is None and row['finish_position_representation'] is None
                    and row['time_raw'] == row['source_record']['raw'].get('time')
                    and row['finish_position_candidate'] == row['source_record'].get('finish_pos')
                    and all(row[field] is False for field in
                            ('identity_verified', 'prediction_time_verified', 'production_eligible')),
                    f'Raw value or verification status changed in {key}')
        source_sha = race['source_sha256']
        require(originals.setdefault(pdf, source_sha) == source_sha, f'Inconsistent JRA PDF SHA for {pdf}')
    require(set(by_race) == race_keys and len(originals) == 36
            and sum(row['time_raw'] is None for row in entries) == 18,
            'Prepared input coverage, original PDFs or missing-clock count differs')
    return races, by_race, originals, race_keys, race_ids


def read_result(raw, race_id, race):
    document = html.fromstring(raw, parser=html.HTMLParser(encoding='utf-8', no_network=True))
    canonical = RESULT_BASE + race_id
    require(document.xpath('//link[@rel="canonical"]/@href') == [canonical],
            'Safari source canonical URL does not identify the expected race')
    title = document.xpath('string(/html/head/title)').strip()
    end = re.search(r'\|\s*(\d{4})年(\d{1,2})月(\d{1,2})日\s+(\S+?)(\d{1,2})R\s',
                    unicodedata.normalize('NFKC', title))
    require(end is not None, 'Safari title lacks the race date, venue and number')
    year, month, day, venue, number = end.groups()
    require((f'{year}-{int(month):02}-{int(day):02}', venue, int(number))
            == (race['race_date'], race['venue'], race['race_number']),
            'Safari title date, venue or number differs from the queue')
    return title, read_browser_rows(document, race_id)


def compare(entries, browser_rows, race, race_id):
    browser_by_number = {row['horse_number']: row for row in browser_rows}
    require(set(browser_by_number) <= {row['horse_number'] for row in entries},
            'Safari includes a horse number absent from the JRA result')
    comparisons = []
    previous_clock = None
    for entry in entries:
        number = entry['horse_number']
        browser = browser_by_number.get(number)
        status, raw_time = entry['status'], entry['time_raw']
        require(browser is not None or status != 'finished', f'Finished horse {number} is absent from Safari')
        reasons = ['jra_raw_finish_position_unavailable']
        rank_review = clock_agrees = ditto_agrees = None
        if browser is None:
            reasons.append('not_listed_in_browser_result')
        else:
            if browser['status'] == 'unknown':
                raise ValueError(f'Unknown Safari result status for horse {number}')
            if normalized_name(browser['horse_name']) != normalized_name(entry['horse_name_display']):
                reasons.append('horse_name_difference')
            if browser['status'] != STATUS_FOR_BROWSER[status]:
                reasons.append('jra_browser_status_difference')
            if browser['netkeiba_horse_id_candidate'] is None:
                reasons.append('horse_profile_id_not_observed')
            if entry['finish_position_candidate'] is not None and browser['finish_position'] is not None:
                rank_review = entry['finish_position_candidate'] == browser['finish_position']
                if not rank_review:
                    reasons.append('derived_finish_position_difference')
        if status != 'finished':
            reasons.append(f'jra_{status}')
        elif raw_time == '〃':
            reasons.append('jra_raw_ditto')
            reference = normalized_reference_time(previous_clock)
            displayed = normalized_reference_time(browser['time_raw']) if browser else None
            if reference is not None and displayed is not None:
                ditto_agrees = reference == displayed
        elif raw_time is None:
            reasons.append('jra_raw_clock_missing')
        elif not CLOCK.fullmatch(raw_time):
            reasons.append('unsupported_jra_time_format')
        else:
            clock_agrees = bool(browser and raw_time == browser['time_raw'])
            if not clock_agrees:
                reasons.append('numeric_clock_difference')
        comparisons.append({
            'race_id': race_id, 'jra_local_race_key': race['local_race_key'], 'horse_number': number,
            'browser_result': browser, 'jra_horse_name': entry['horse_name_display'], 'jra_status': status,
            'jra_finish_position_raw': entry['finish_position_raw'],
            'jra_finish_position_candidate': entry['finish_position_candidate'],
            'jra_finish_position_basis': entry['finish_position_basis'],
            'derived_finish_position_matches': rank_review, 'jra_time_raw': raw_time,
            'raw_clock_matches': clock_agrees,
            'ditto_reference_clock': previous_clock if raw_time == '〃' else None,
            'separate_ditto_clock_matches': ditto_agrees, 'hold_reasons': reasons,
            'identity_verified': False, 'prediction_time_verified': False, 'production_eligible': False,
        })
        if raw_time != '〃':
            previous_clock = raw_time if status == 'finished' else None
    return comparisons


def read_receipt(path, verify_files=False):
    raw = path.read_bytes()
    doc = json.loads(raw)
    if verify_files:
        require(set(doc['files_sha256']) == SAVED_FILES, f'Unexpected artifacts in {path}')
        for name, digest in doc['files_sha256'].items():
            require(sha256((path.parent / name).read_bytes()) == digest, f'Saved checksum differs in {path}: {name}')
    return {**doc, 'receipt_path': path.relative_to(OUTPUT).as_posix(), 'receipt_sha256': sha256(raw)}


def prior_heroines_keys():
    old = [json.loads(p.read_bytes()) for p in PRIOR_OUTPUT.glob('*/*/*R/receipt.json')]
    old_keys = {r['jra_local_race_key'] for r in old}
    old_ids = {r['race_id'] for r in old}
    require(len(old) == len(old_keys) == len(old_ids) == 405, 'Prior 405 checkpoint set changed')
    return old_keys, old_ids


def verify_reported_myu_scope(target_keys, old_keys):
    raw = PRIOR_JRA_ARCHIVE.read_bytes()
    require(sha256(raw) == INPUT_SHA, 'Prior approved JRA archive SHA-256 mismatch')
    with zipfile.ZipFile(BytesIO(raw)) as archive, archive.open('structured/races.jsonl') as rows:
        old = [race for line in rows if (race := json.loads(line))['race_date'] <= '2026-02-01'
               and race['local_race_key'] not in old_keys]
    keys = {race['local_race_key'] for race in old}
    legacy = sorted(f"{race['race_date']}|{race['venue']}|{race['race_number']}" for race in old)
    fingerprint = sha256('\n'.join(legacy).encode('utf-8'))
    require(len(old) == len(keys) == 1893 and sum(race['entry_rows'] for race in old) == 25903
            and fingerprint == MYU_REPORTED_RACE_KEYS_SHA, 'Rebuilt prior keys differ from the Myu receipt report')
    require(not target_keys & keys, 'Target overlaps a verified key in the prior Myu scope')
    return {'race_keys': len(keys), 'rows': 25903, 'legacy_key_set_sha256': fingerprint, 'overlap': 0,
            'method': 'reconstructed from pinned JRA input and matched to the reported original receipt key-set SHA'}


def inspect_saved(races, race_keys, race_ids):
    require(OUTPUT.resolve() == OUTPUT, 'Independent output path resolves elsewhere')
    old_keys, old_ids = prior_heroines_keys()
    require(not (race_keys & old_keys or race_ids & old_ids), 'Target overlaps a prior HEROINES checkpoint')
    paths = {p.parent for p in OUTPUT.glob('*/*/*R/receipt.json')}
    saved = []
    for race in races:
        dest = destination(race)
        if dest not in paths:
            break
        require(dest.resolve() == dest, f'Saved checkpoint resolves elsewhere: {dest}')
        receipt = read_receipt(dest / 'receipt.json', verify_files=True)
        race_id, _ = race_details(race)
        require(receipt['jra_local_race_key'] == race['local_race_key'] and receipt['race_id'] == race_id
                and receipt['rows'] == race['entry_rows']
                and receipt['source_pdf_sha256'] == race['source_sha256']
                and receipt['inputs_sha256'] == INPUT_HASHES, f'Invalid stored checkpoint {dest}')
        saved.append(receipt)
    require(len(paths) == len(saved), 'A saved race lies after a gap or outside the prepared queue')
    if len(saved) < len(races):
        next_path = destination(races[len(saved)])
        require(not next_path.exists() and not next_path.is_symlink(),
                f'Unresolved partial checkpoint for next race: {next_path}')
    return saved


def destination(race):
    return OUTPUT / race['race_date'] / race['venue'] / f"{race['race_number']}R"


def progress(races, saved):
    fields = ('rows', 'candidate_rows', 'raw_clock_match_rows', 'raw_clock_hold_rows',
              'separate_ditto_matches', 'jra_raw_rank_unavailable_rows', 'jra_raw_clock_null_rows')
    totals = {name: sum(row[name] for row in saved) for name in fields}
    next_race = None
    if len(saved) < len(races):
        race = races[len(saved)]
        race_id, _ = race_details(race)
        next_race = {**race, 'expected_netkeiba_race_id': race_id,
                     'source_browser_url': RESULT_BASE + race_id}
    return {
        'state': 'collection_complete' if next_race is None else 'ready_for_next_race',
        'host': socket.gethostname(), 'assigned_races': 432, 'assigned_rows': 5846,
        'saved_races': len(saved), **totals, 'remaining_races': 432 - len(saved),
        'remaining_rows': 5846 - totals['rows'], 'next_race': next_race,
        'inputs_sha256': INPUT_HASHES, 'receipts': saved,
    }


def read_current_progress(races, saved):
    report = progress(races, saved)
    index = OUTPUT / 'acquisition_progress.json'
    if index.is_file():
        recorded = json.loads(index.read_bytes())
        require(all(recorded.get(name) == value for name, value in report.items() if name != 'state'),
                'Progress index differs from saved checkpoints')
        report['state'] = recorded['state']
        if 'latest_stop_evidence' in recorded:
            report['latest_stop_evidence'] = recorded['latest_stop_evidence']
    return report


@contextmanager
def writer_lock():
    require(OUTPUT.resolve() == OUTPUT, 'Independent output path resolves elsewhere')
    OUTPUT.mkdir(exist_ok=True)
    lock_path = OUTPUT / '.writer.lock'
    fd = os.open(lock_path, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def write_progress(report):
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=OUTPUT, delete=False) as stream:
        staged = Path(stream.name)
        try:
            json.dump(report, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
            staged.replace(OUTPUT / 'acquisition_progress.json')
        finally:
            staged.unlink(missing_ok=True)


def verify_navigation(started, previous_started, previous_saved):
    require(started.utcoffset() is not None, 'Navigation timestamp must have a timezone')
    if previous_started is not None:
        require((started - previous_started).total_seconds() >= 10 and started >= previous_saved,
                'Navigation spacing or order differs from the previous saved race')


def prepare_race(race, entries, race_id, started, captured, raw):
    expected, pdf = race_details(race)
    require(race_id == expected, 'Requested race is not the next prepared race')
    verified_pdf(pdf, race['source_sha256'])
    require(started <= captured <= datetime.now(JST), 'Safari source file does not follow the navigation start')
    title, browser = read_result(raw, race_id, race)
    comparisons = compare(entries, browser, race, race_id)
    return pdf, title, browser, comparisons


def commit_race(races, saved, race, entries, race_id, started, captured, raw, prepared, *, batch_capture=None):
    pdf, title, browser, comparisons = prepared
    dest = destination(race)
    require(not dest.exists() and not dest.is_symlink(), f'Checkpoint already exists: {dest}')
    counts = Counter(row['raw_clock_matches'] for row in comparisons)
    require(dest.parent.resolve() == dest.parent, 'Checkpoint directory resolves elsewhere')
    dest.parent.mkdir(parents=True, exist_ok=True)
    require(dest.parent.resolve() == dest.parent, 'Checkpoint directory resolves elsewhere')
    with tempfile.TemporaryDirectory(prefix='.staging-', dir=dest.parent) as temp:
        stage = Path(temp) / 'race'
        (stage / 'raw').mkdir(parents=True)
        (stage / 'raw/result.html').write_bytes(raw)
        for name, rows in (('entries', entries), ('netkeiba', browser), ('comparisons', comparisons)):
            write_jsonl(stage / f'{name}.jsonl', rows)
        receipt = {
            'state': 'saved_2025_candidates', 'host': socket.gethostname(),
            'race_id': race_id, 'jra_local_race_key': race['local_race_key'],
            'legacy_race_key': race['legacy_race_key'], 'title': title,
            'source_browser': 'Safari page source', 'source_url': RESULT_BASE + race_id,
            'source_jra_url': race['source_url'], 'source_pdf_path': str(pdf),
            'source_pdf_sha256': race['source_sha256'], 'source_pdf_page': race['source_page'],
            'source_pdf_column': race['source_column'], 'inputs_sha256': INPUT_HASHES,
            'navigation_started_at_jst': started.astimezone(JST).isoformat(),
            'browser_source_saved_at_jst': captured.isoformat(), 'saved_at_jst': datetime.now(JST).isoformat(),
            'rows': len(entries), 'browser_result_rows': len(browser),
            'candidate_rows': sum(row['netkeiba_horse_id_candidate'] is not None for row in browser),
            'raw_clock_match_rows': counts[True], 'raw_clock_hold_rows': len(entries) - counts[True],
            'separate_ditto_matches': sum(row['separate_ditto_clock_matches'] is True for row in comparisons),
            'jra_raw_rank_unavailable_rows': len(entries),
            'jra_raw_clock_null_rows': sum(row['time_raw'] is None for row in entries),
            'identity_verification': 'pending', 'prediction_time_verification': 'pending',
            'files_sha256': {name: sha256((stage / name).read_bytes()) for name in sorted(SAVED_FILES)},
        }
        if batch_capture is not None:
            receipt['batch_capture'] = batch_capture
        (stage / 'receipt.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        stage.rename(dest)
    readback = read_receipt(dest / 'receipt.json', verify_files=True)
    require(all(readback[key] == value for key, value in receipt.items()), 'Checkpoint reread differs from the write')
    write_progress(progress(races, saved + [readback]))
    return readback


def save_race(races, by_race, saved, raw_path, race_id, started):
    require(len(saved) < len(races), 'All assigned races are already stored')
    prior = saved[-1] if saved else None
    previous_started = datetime.fromisoformat(prior['navigation_started_at_jst']) if prior else None
    previous_saved = datetime.fromisoformat(prior['saved_at_jst']) if prior else None
    verify_navigation(started, previous_started, previous_saved)
    race = races[len(saved)]
    entries = by_race[race['local_race_key']]
    with raw_path.open('rb') as stream:
        captured = datetime.fromtimestamp(os.fstat(stream.fileno()).st_mtime, JST)
        raw = stream.read()
    prepared = prepare_race(race, entries, race_id, started, captured, raw)
    return commit_race(races, saved, race, entries, race_id, started, captured, raw, prepared)


def read_batch_source(directory_fd, file_name):
    require(isinstance(file_name, str) and re.fullmatch(r'[A-Za-z0-9_-]+\.html', file_name),
            'Each capture must specify a simple, relative .html file name')
    fd = os.open(file_name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory_fd)
    with os.fdopen(fd, 'rb') as stream:
        before = os.fstat(stream.fileno())
        require(stat.S_ISREG(before.st_mode), f'Capture is not a regular file: {file_name}')
        raw = stream.read()
        after = os.fstat(stream.fileno())
    require((before.st_size, before.st_mtime_ns, before.st_ctime_ns)
            == (after.st_size, after.st_mtime_ns, after.st_ctime_ns) and len(raw) == after.st_size,
            f'Safari source was still changing: {file_name}')
    return datetime.fromtimestamp(after.st_mtime, JST), raw


def save_batch(races, by_race, saved, manifest_path, capture_dir):
    manifest_bytes = manifest_path.read_bytes()
    manifest = json.loads(manifest_bytes)
    require(isinstance(manifest, dict) and isinstance(manifest.get('captures'), list),
            'Batch manifest must contain an ordered captures list')
    captures = manifest['captures']
    require(1 <= len(captures) <= min(MAX_BATCH_CAPTURES, len(races) - len(saved)),
            f'Batch must contain between 1 and {MAX_BATCH_CAPTURES} pending captures')
    require(all(isinstance(capture, dict) and set(capture) ==
                {'race_id', 'file_name', 'navigation_started_at_jst'} and
                all(isinstance(value, str) for value in capture.values()) for capture in captures),
            'Each capture must specify race_id, file_name and navigation_started_at_jst as strings')
    require(len({capture['file_name'].casefold() for capture in captures}) == len(captures),
            'Batch captures must refer to distinct files')
    capture_dir = capture_dir.absolute()
    require(capture_dir.resolve() == capture_dir, 'Capture directory must not resolve elsewhere')
    prior = saved[-1] if saved else None
    previous_started = datetime.fromisoformat(prior['navigation_started_at_jst']) if prior else None
    previous_saved = datetime.fromisoformat(prior['saved_at_jst']) if prior else None
    prepared_items = []
    directory_fd = os.open(capture_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for index, capture in enumerate(captures, start=len(saved)):
            race = races[index]
            entries = by_race[race['local_race_key']]
            race_id = capture['race_id']
            started = datetime.fromisoformat(capture['navigation_started_at_jst'])
            verify_navigation(started, previous_started, previous_saved)
            captured, raw = read_batch_source(directory_fd, capture['file_name'])
            prepared = prepare_race(race, entries, race_id, started, captured, raw)
            prepared_items.append((race, entries, race_id, started, captured, raw, prepared))
            previous_started, previous_saved = started, captured
    finally:
        os.close(directory_fd)

    manifest_sha256 = sha256(manifest_bytes)
    committed = []
    for index, item in enumerate(prepared_items, start=1):
        batch_capture = {'manifest_sha256': manifest_sha256, 'ordinal': index, 'total': len(prepared_items)}
        committed.append(commit_race(races, saved + committed, *item, batch_capture=batch_capture))
    return {'committed_races': len(committed), 'manifest_sha256': manifest_sha256, 'receipts': committed}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('preflight', help='Verify all prepared source file hashes and current saved keys')
    commands.add_parser('status', help='Read and verify current checkpoints')
    save = commands.add_parser('save', help='Store and reread exactly the next saved Safari result')
    save.add_argument('raw_html', type=Path)
    save.add_argument('race_id')
    save.add_argument('navigation_started_at', type=datetime.fromisoformat)
    batch = commands.add_parser('save-batch', help='Validate and store an ordered batch of local Safari source files')
    batch.add_argument('manifest', type=Path)
    batch.add_argument('capture_dir', type=Path)
    args = parser.parse_args()
    try:
        races, by_race, originals, keys, ids = read_inputs()
        if args.command in ('save', 'save-batch'):
            with writer_lock():
                saved = inspect_saved(races, keys, ids)
                if args.command == 'save':
                    result = save_race(races, by_race, saved, args.raw_html, args.race_id, args.navigation_started_at)
                else:
                    result = save_batch(races, by_race, saved, args.manifest, args.capture_dir)
        else:
            saved = inspect_saved(races, keys, ids)
            result = read_current_progress(races, saved)
            del result['receipts']
            if args.command == 'preflight':
                json.loads(read_pinned('validation.json'))
                for path, expected_sha in originals.items():
                    verified_pdf(path, expected_sha)
                result['verified_original_pdfs'] = len(originals)
                result['verified_prior_heroines_races'] = 405
                result['reported_myu_scope'] = verify_reported_myu_scope(keys, prior_heroines_keys()[0])
    except (KeyError, OSError, ValueError) as error:
        parser.exit(1, f'2025 race checkpoint refused: {error}\n')
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
