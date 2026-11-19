"""Verify, resume and save the exact Aice handoff from local Safari sources."""

import argparse
from collections import Counter, defaultdict
from contextlib import contextmanager
from datetime import date, datetime
import fcntl
import json
import os
from pathlib import Path
import re
import socket
import tempfile
import unicodedata

from lxml import html

from save_heroines_2025_continuation import read_batch_source, verify_navigation
from save_kokura_continuation import CLOCK, JST, ROOT, normalized_name, sha256
from save_race_continuation import read_browser_rows, verified_pdf, write_jsonl


OUTPUT = ROOT / 'HEROINES並行取得_2026-04-18_2026-05-31'
ZIP_SHA = '02d6e2d017b73e84e80faeef82510dfefc3151374cc3f56a237ce50c55b815d6'
MANIFEST_SHA = 'dc573ebe50a1c64b87b8e534a5b1f8081ee4da964c1e9f62800f4babf96984d8'
FIRST_RECEIPT_SHA = '97289ff0522ac3ec267fc5c7ac4626f73baf9f99e7e8f3e34573092ca6c323dc'
RESULT_BASE = 'https://race.netkeiba.com/race/result.html?race_id='
PDF_BASE = 'https://jra.jp/datafile/seiseki/report/2026/'
FIRST_RACE = '2026-04-18|中山|5'
LAST_RACE = '2026-05-31|京都|12'
RACES = 476
RUNNERS = 6719
BATCH_MAX = 6
VENUES = {'福島': ('03', 'fukushima'), '新潟': ('04', 'niigata'), '東京': ('05', 'tokyo'),
          '中山': ('06', 'nakayama'), '京都': ('08', 'kyoto'), '阪神': ('09', 'hanshin')}
STATUS = {'finished': 'finished', 'withdrawn': 'cancelled', 'excluded': 'excluded',
          'did_not_finish': 'did_not_finish'}
FILES = {'raw/result.html', 'jra_entries.jsonl', 'horse_id_candidates.jsonl', 'comparisons.jsonl'}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def local_bytes(path):
    require(path.resolve() == path and path.is_file(), f'Input must be a local regular file: {path}')
    return path.read_bytes()


def decode_lines(raw):
    return [json.loads(line) for line in raw.splitlines()]


def read_handoff(check_all_pdfs=False):
    home = OUTPUT / 'handoff-input'
    raw_manifest = local_bytes(home / 'manifest.json')
    require(sha256(raw_manifest) == MANIFEST_SHA, 'Handoff manifest differs from the verified ZIP')
    records = json.loads(raw_manifest)
    manifest = {item['path']: item for item in records}
    require(len(records) == len(manifest) == 53, 'Unexpected handoff manifest membership')

    def checked(name):
        raw = local_bytes(home / name)
        expectation = manifest[name]
        require(len(raw) == expectation['bytes'] and sha256(raw) == expectation['sha256'],
                f'Handoff file differs from manifest: {name}')
        return raw

    queue = decode_lines(checked('races.jsonl'))
    entries = decode_lines(checked('runner_table.jsonl'))
    expected_runners = json.loads(checked('evidence/handoff-remaining-runner-keys.json'))
    prior_runners = json.loads(checked('evidence/saved-runner-keys.json'))
    checkpoint = json.loads(checked('evidence/checkpoint.json'))
    pdf_map = {item['package_path']: item for item in json.loads(checked('pdf-path-map.json'))}
    require(len(pdf_map) == 40 and checkpoint['next'] == FIRST_RACE and checkpoint['unfinished_call'] is False
            and (checkpoint['saved_races'], checkpoint['saved_rows'], checkpoint['remaining_races'],
                 checkpoint['remaining_rows']) == (268, 3795, RACES, RUNNERS), 'Handoff scope differs from checkpoint')
    require(len(queue) == RACES and len(entries) == len(expected_runners) == len(set(expected_runners)) == RUNNERS
            and len(prior_runners) == len(set(prior_runners)) == 3795
            and not set(prior_runners) & set(expected_runners)
            and queue[0]['race_key'] == FIRST_RACE and queue[-1]['race_key'] == LAST_RACE,
            'Handoff queue membership is not the stated 476-race remainder')

    prior_keys = {key.rsplit('|', 1)[0] for key in prior_runners}
    require(len(prior_keys) == 268, 'Aice saved race keys differ from checkpoint')
    runners = defaultdict(list)
    supplied_keys = set()
    for entry in entries:
        key, number = entry['runner_key'], entry['horse_number']
        require(type(number) is int and 1 <= number <= 18 and key == f'{entry["race_key"]}|{number}'
                and key not in supplied_keys and entry['status'] in STATUS and entry['finish_position_raw'] is None
                and entry['finish_position_representation'] is None and entry['gate']['identity_verified'] is False
                and entry['gate']['pit_verified'] is False and entry['gate']['production_eligible'] is False,
                f'Unexpected runner identity or verification state: {key}')
        supplied_keys.add(key)
        runners[entry['race_key']].append(entry)
    require(supplied_keys == set(expected_runners), 'Runner rows differ from the exact remaining key set')

    jobs, local_keys, race_ids, used_pdfs = [], set(), set(), set()
    for race in queue:
        day, venue, number, key = race['date'], race['venue'], race['race_no'], race['race_key']
        code, slug = VENUES[venue]
        require(date(2026, 4, 18) <= date.fromisoformat(day) <= date(2026, 5, 31)
                and type(number) is int and 1 <= number <= 12 and race['status'] == 'performed'
                and key == f'{day}|{venue}|{number}' and key not in prior_keys,
                f'Unexpected or already-saved race: {key}')
        context = race['source_record']['context']
        meet, meeting_day = context['meeting'], context['meeting_day']
        require(type(meet) is int and type(meeting_day) is int and 1 <= meet <= 9 and 1 <= meeting_day <= 12,
                f'Invalid meeting locator: {key}')
        race_id = f'2026{code}{meet:02}{meeting_day:02}{number:02}'
        local_key = f'jra-local:{day}:{slug}:{number:02}'
        filename = f'2026-{meet}{slug}{meeting_day}.pdf'
        package = f'pdf/{filename}'
        locator = pdf_map[package]
        require(local_key not in local_keys and race_id not in race_ids
                and race['source_url'] == PDF_BASE + filename
                and race['source_pdf_sha256'] == locator['sha256'] == manifest[package]['sha256']
                and locator['bytes'] == manifest[package]['bytes'] and race['source_side_raw'] in (0, 1)
                and type(race['source_page']) is int and race['source_page'] >= 1,
                f'Invalid or duplicate program or original-PDF mapping: {key}')
        local_keys.add(local_key)
        race_ids.add(race_id)
        used_pdfs.add(package)
        group = runners[key]
        require(len(group) == race['table_rows'] and len({row['horse_number'] for row in group}) == len(group),
                f'Runner count differs from the race: {key}')
        fields = ('date', 'venue', 'race_no', 'source_url', 'source_pdf_sha256', 'source_page', 'source_side_raw')
        for row in group:
            require(all(row[f] == race[f] for f in fields)
                    and row['local_original_pdf_path'] == locator['original_path']
                    and row['time_raw'] == row['source_record']['result']['time_raw_column'],
                    f'Runner source or raw time differs from handoff: {row["runner_key"]}')
        jobs.append({'race_date': day, 'venue': venue, 'race_number': number, 'legacy_race_key': key,
                     'jra_local_race_key': local_key, 'race_id': race_id,
                     'expected_netkeiba_race_id': race_id, 'source_browser_url': RESULT_BASE + race_id,
                     'entry_rows': len(group), 'source_pdf_url': race['source_url'],
                     'source_pdf_path': f'handoff-input/{package}', 'source_pdf_sha256': locator['sha256'],
                     'source_pdf_page': race['source_page'], 'source_pdf_column': ('left', 'right')[race['source_side_raw']]})
    require(len(runners) == RACES and used_pdfs == set(pdf_map), 'Unexpected handoff race or original-PDF coverage')

    partial = defaultdict(list)
    evidence = json.loads(checked('evidence/existing-partial-evidence.json'))
    for row in evidence:
        if row['runner_key'] in supplied_keys:
            require(row['namespace'] == 'netkeiba_horse' and row['identity_verified'] is False
                    and row['pit_verified'] is False and row['production_eligible'] is False,
                    'Prior partial evidence cannot establish identity or prediction-time availability')
            partial[row['runner_key']].append(row)
    require(len(evidence) == 60 and sum(map(len, partial.values())) == 35, 'Unexpected partial-evidence coverage')
    if check_all_pdfs:
        for name in sorted(used_pdfs):
            require(checked(name).startswith(b'%PDF-'), f'Original is not a PDF: {name}')
    verify_prior_local(local_keys, race_ids)
    return jobs, runners, partial


def verify_prior_local(local_keys, race_ids):
    for folder, count in (('HEROINES並行取得', 405), ('HEROINES並行取得_2025-06-01_2025-07-13', 432)):
        root = ROOT / folder
        progress = json.loads(local_bytes(root / 'acquisition_progress.json'))
        old_keys, old_ids = set(), set()
        for entry in progress['receipts']:
            raw = local_bytes(root / entry['receipt_path'])
            require(sha256(raw) == entry['receipt_sha256'], f'Prior HEROINES receipt changed: {entry["receipt_path"]}')
            receipt = json.loads(raw)
            old_keys.add(receipt['jra_local_race_key'])
            old_ids.add(receipt['race_id'])
        require(len(progress['receipts']) == len(old_keys) == len(old_ids) == count
                and not (local_keys & old_keys or race_ids & old_ids), f'Overlap or incomplete prior HEROINES scope: {folder}')


def parse_browser(raw, job):
    document = html.fromstring(raw, parser=html.HTMLParser(encoding='utf-8', no_network=True))
    require(document.xpath('//link[@rel="canonical"]/@href') == [job['source_browser_url']],
            'Safari canonical URL does not match the expected next race')
    title = document.xpath('string(/html/head/title)').strip()
    header = re.search(r'\|\s*(\d{4})年(\d{1,2})月(\d{1,2})日\s+(\S+?)(\d{1,2})R\s',
                       unicodedata.normalize('NFKC', title))
    require(header is not None, 'Safari title lacks official race date, venue and number')
    year, month, day, venue, number = header.groups()
    require((date(int(year), int(month), int(day)).isoformat(), venue, int(number))
            == (job['race_date'], job['venue'], job['race_number']), 'Safari race title differs from the handoff queue')
    return title, read_browser_rows(document, job['race_id'])


def normalized_clock(value):
    return unicodedata.normalize('NFKC', value.strip()) if value is not None else None


def clock_tenths(value):
    normalized = normalized_clock(value)
    if normalized is None or not CLOCK.fullmatch(normalized):
        return None
    minutes, remainder = normalized.split(':')
    seconds, fraction = remainder.split('.')
    return int(minutes) * 600 + int(seconds) * 10 + int(fraction)


def compare(job, entries, browser, partial):
    by_number = {row['horse_number']: row for row in browser}
    require(set(by_number) <= {row['horse_number'] for row in entries},
            'Safari includes a horse number absent from the handoff race')
    comparison = []
    for entry in entries:
        number = entry['horse_number']
        observed = by_number.get(number)
        status = entry['status']
        require(observed is not None or status != 'finished', f'Finished horse {number} is absent from Safari')
        require(observed is None or observed['status'] != 'unknown', f'Unknown Safari result status for horse {number}')
        name = entry['horse_name_display']
        time_raw = entry['time_raw']
        held = ['jra_raw_finish_position_unavailable']
        name_match = None if observed is None else normalized_name(observed['horse_name']) == normalized_name(name)
        status_match = None if observed is None else observed['status'] == STATUS[status]
        candidate = observed['netkeiba_horse_id_candidate'] if observed else None
        rank = entry['finish_position_candidate']
        rank_match = None if rank is None or observed is None or observed['finish_position'] is None else rank == observed['finish_position']
        if observed is None:
            held.append('not_listed_in_browser_result')
        if name_match is False:
            held.append('horse_name_difference')
        if status_match is False:
            held.append('jra_browser_status_difference')
        if candidate is None:
            held.append('horse_profile_id_not_observed')
        if rank_match is False:
            held.append('derived_finish_position_difference')

        was_clock_match, ditto_match, ditto_reference = None, None, None
        browser_clock = observed['time_raw'] if observed else None
        if status != 'finished':
            clock_status = f'jra_{status}'
        elif time_raw == '〃':
            clock_status = 'jra_raw_ditto'
            result = entry['source_record']['result']
            if result['time_method'] == 'ditto_previous_printed_time':
                ditto_reference = result['time_seconds_candidate']
            displayed = clock_tenths(browser_clock)
            if ditto_reference is not None and displayed is not None:
                ditto_match = displayed == round(ditto_reference * 10)
        elif time_raw is None:
            clock_status = 'jra_raw_clock_missing'
        elif clock_tenths(time_raw) is None:
            clock_status = 'unsupported_jra_time_format'
        else:
            was_clock_match = clock_tenths(time_raw) == clock_tenths(browser_clock)
            clock_status = 'match' if was_clock_match else 'numeric_clock_difference'
        if clock_status != 'match':
            held.append(clock_status)

        evidence = partial.get(entry['runner_key'], [])
        # History is carry-in evidence only. It never replaces the observed profile ID.
        conflict = bool(candidate and any(row['id_candidate'] != candidate for row in evidence))
        if conflict:
            held.append('handoff_partial_horse_id_conflict')
        comparison.append({
            'race_id': job['race_id'], 'jra_local_race_key': job['jra_local_race_key'],
            'runner_key': entry['runner_key'], 'horse_number': number, 'jra_name': name,
            'browser_name': observed['horse_name'] if observed else None, 'horse_name_matches': name_match,
            'jra_status': status, 'browser_status_matches': status_match,
            'jra_finish_position_raw': entry['finish_position_raw'],
            'jra_finish_position_candidate': rank, 'jra_finish_position_basis': entry['finish_position_basis'],
            'browser_finish_position_raw': observed['finish_position_raw'] if observed else None,
            'derived_position_matches': rank_match, 'jra_time_raw': time_raw, 'jra_time_normalized': normalized_clock(time_raw),
            'browser_time_raw': browser_clock, 'explicit_clock_matches': was_clock_match, 'clock_status': clock_status,
            'separate_ditto_reference_seconds_candidate': ditto_reference, 'separate_ditto_matches': ditto_match,
            'horse_profile_href': observed['horse_profile_href'] if observed else None,
            'netkeiba_horse_id_candidate': candidate, 'handoff_partial_evidence': evidence,
            'handoff_partial_candidate_conflict': conflict, 'hold_reasons': held,
            'identity_verified': False, 'prediction_time_verified': False, 'production_eligible': False,
        })
    return comparison


def race_path(job):
    return OUTPUT / job['race_date'] / job['venue'] / f"{job['race_number']}R"


def read_checkpoint(job, original_entries):
    destination = race_path(job)
    path = destination / 'receipt.json'
    raw = local_bytes(path)
    receipt = json.loads(raw)
    digest = sha256(raw)
    legacy = job['legacy_race_key'] == FIRST_RACE
    require(all(receipt[f] == job[f] for f in ('race_id', 'jra_local_race_key', 'legacy_race_key', 'race_date',
                'venue', 'race_number', 'source_pdf_url', 'source_pdf_sha256', 'source_pdf_page', 'source_pdf_column'))
            and receipt['rows'] == job['entry_rows'] and receipt['identity_verification'] == 'pending'
            and receipt['prediction_time_verification'] == 'pending', f'Saved checkpoint differs from handoff: {path}')
    require((legacy and digest == FIRST_RECEIPT_SHA) or
            (not legacy and receipt['handoff_zip_sha256'] == ZIP_SHA and receipt['handoff_manifest_sha256'] == MANIFEST_SHA
             and receipt['source_pdf_path'] == job['source_pdf_path']), f'Unexpected saved receipt provenance: {path}')
    if legacy:
        verified_pdf(OUTPUT / receipt['source_pdf_path'], job['source_pdf_sha256'])
    require(set(receipt['files_sha256']) == FILES, f'Saved artifact list differs: {path}')
    artifacts = {}
    for name, expected in receipt['files_sha256'].items():
        data = local_bytes(destination / name)
        require(sha256(data) == expected, f'Saved artifact checksum differs: {path} {name}')
        artifacts[name] = data
    saved_entries = decode_lines(artifacts['jra_entries.jsonl'])
    comparison = decode_lines(artifacts['comparisons.jsonl'])
    browser = decode_lines(artifacts['horse_id_candidates.jsonl'])
    if legacy:
        _, source_browser = parse_browser(artifacts['raw/result.html'], job)
        require(source_browser == browser, 'Opening race HTML differs from its saved candidates')
        by_number = {e['horse_number']: e for e in original_entries}
        require(set(by_number) == {e['horse_number'] for e in saved_entries}, 'Opening race runner keys differ from handoff')
        for old in saved_entries:
            official = by_number[old['horse_number']]
            require(normalized_name(old['horse_name_raw']) == normalized_name(official['horse_name_display'])
                    and normalized_clock(old['finish_time_raw']) == normalized_clock(official['time_raw'])
                    and old['finish_position_raw'] is official['finish_position_raw'] is None
                    and old['finish_position_from_pdf_row_order'] == official['finish_position_candidate']
                    and old['status'] == official['status'], 'Opening race JRA original differs from handoff')
    else:
        require(saved_entries == original_entries, f'Saved JRA source rows differ from original handoff: {path}')
    numbers = {r['horse_number'] for r in original_entries}
    require(numbers == {r['horse_number'] for r in comparison} and len(comparison) == len(numbers)
            and all(r['identity_verified'] is False and r['prediction_time_verified'] is False for r in comparison)
            and len(browser) == len({r['horse_number'] for r in browser})
            and {r['horse_number'] for r in browser} <= numbers
            and sum(r['netkeiba_horse_id_candidate'] is not None for r in browser) == receipt['candidate_rows']
            and sum(r['explicit_clock_matches'] is True for r in comparison) == receipt['explicit_clock_match_rows']
            and len(comparison) - receipt['explicit_clock_match_rows'] == receipt['clock_hold_rows']
            and sum(r['separate_ditto_matches'] is True for r in comparison) == receipt['separate_ditto_match_rows'],
            f'Saved comparison identity or counts differ: {path}')
    started, captured, stored = (datetime.fromisoformat(receipt[f]) for f in
                               ('navigation_started_at_jst', 'browser_source_saved_at_jst', 'saved_at_jst'))
    require(all(t.utcoffset() is not None for t in (started, captured, stored)) and started <= captured <= stored,
            f'Saved navigation, capture and receipt order is invalid: {path}')
    relative = path.relative_to(OUTPUT).as_posix()
    index = []
    for row in comparison:
        number = row['horse_number']
        index.append({'race_id': job['race_id'], 'jra_local_race_key': job['jra_local_race_key'],
                      'race_horse_key': f'{job["jra_local_race_key"]}:{number:02}', 'horse_number': number,
                      'horse_id_candidate': row['netkeiba_horse_id_candidate'],
                      'receipt_path': relative, 'receipt_sha256': digest})
    return {**receipt, 'receipt_path': relative, 'receipt_sha256': digest}, index


def inspect_saved(jobs, runners):
    paths = {p.parent for p in OUTPUT.glob('*/*/*R/receipt.json')}
    saved, integration = [], []
    for job in jobs:
        if race_path(job) not in paths:
            break
        receipt, index = read_checkpoint(job, runners[job['legacy_race_key']])
        if saved:
            prior = saved[-1]
            batch, prior_batch = receipt.get('batch_capture'), prior.get('batch_capture')
            same_batch = batch and prior_batch and batch['manifest_sha256'] == prior_batch['manifest_sha256']
            if same_batch:
                require(batch['ordinal'] == prior_batch['ordinal'] + 1 and batch['total'] == prior_batch['total'],
                        'Stored batch capture order differs from its preceding receipt')
            elif batch:
                require(batch['ordinal'] == 1, 'Stored batch does not start at its first capture')
            verify_navigation(datetime.fromisoformat(receipt['navigation_started_at_jst']),
                              datetime.fromisoformat(prior['navigation_started_at_jst']),
                              datetime.fromisoformat(prior['browser_source_saved_at_jst' if same_batch else 'saved_at_jst']))
        saved.append(receipt)
        integration.extend(index)
    require(saved and len(saved) == len(paths), 'Saved handoff race occurs after a gap, lies outside the queue, or lacks opening receipt')
    if len(saved) < RACES:
        next_path = race_path(jobs[len(saved)])
        require(not next_path.exists() and not next_path.is_symlink(), f'Unresolved partial checkpoint: {next_path}')
    return saved, integration


def atomic_write(path, data):
    require(path.parent.resolve() == path.parent, 'Evidence directory resolves outside its expected path')
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        staging = Path(stream.name)
        try:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
            staging.replace(path)
        finally:
            staging.unlink(missing_ok=True)


def progress(jobs, saved, index):
    fields = ('rows', 'candidate_rows', 'horse_number_and_name_match_rows', 'explicit_clock_match_rows',
              'clock_hold_rows', 'separate_ditto_match_rows', 'handoff_partial_conflict_rows')
    totals = {f: sum(r.get(f, 0) for r in saved) for f in fields}
    next_race = None if len(saved) == RACES else jobs[len(saved)]
    return {'state': 'collection_complete' if next_race is None else 'ready_for_next_race',
            'host': socket.gethostname(), 'expected_races_reported': RACES, 'expected_rows_reported': RUNNERS,
            'saved_races': len(saved), 'saved_rows': totals['rows'],
            'remaining_races_reported_after_saved': RACES - len(saved),
            'remaining_rows_reported_after_saved': RUNNERS - totals['rows'], **totals,
            'distinct_candidate_ids': len({r['horse_id_candidate'] for r in index if r['horse_id_candidate']}),
            'last_saved': saved[-1], 'next_race': next_race, 'receipts': saved,
            'handoff_zip_sha256': ZIP_SHA, 'handoff_manifest_sha256': MANIFEST_SHA,
            'existing_opening_receipt_preserved': FIRST_RECEIPT_SHA,
            'integration_path': 'integration_index.jsonl',
            'identity_verification': 'pending', 'prediction_time_verification': 'pending'}


def publish_indexes(jobs, saved, index):
    payload = ''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in index).encode()
    require(len(index) == len({r['race_horse_key'] for r in index}), 'Integration index contains a duplicate race and horse key')
    status = progress(jobs, saved, index)
    status['integration_sha256'] = sha256(payload)
    atomic_write(OUTPUT / 'integration_index.jsonl', payload)
    atomic_write(OUTPUT / 'acquisition_progress.json', (json.dumps(status, ensure_ascii=False, indent=2) + '\n').encode())


def prepare_capture(job, entries, partial, started, captured, raw):
    require(started <= captured <= datetime.now(JST), 'Safari source file does not follow its own navigation start')
    verified_pdf(OUTPUT / job['source_pdf_path'], job['source_pdf_sha256'])
    title, browser = parse_browser(raw, job)
    checks = compare(job, entries, browser, partial)
    return title, browser, checks


def commit(jobs, job, entries, saved, index, started, captured, raw, prepared, batch_capture):
    title, browser, checks = prepared
    dest = race_path(job)
    require(not dest.exists() and not dest.is_symlink() and dest.parent.resolve() == dest.parent,
            'Checkpoint exists or resolves outside the intended output')
    dest.parent.mkdir(parents=True, exist_ok=True)
    require(dest.parent.resolve() == dest.parent, 'Checkpoint directory resolves elsewhere')
    with tempfile.TemporaryDirectory(prefix='.staging-', dir=dest.parent) as scratch:
        stage = Path(scratch) / 'race'
        (stage / 'raw').mkdir(parents=True)
        (stage / 'raw/result.html').write_bytes(raw)
        for name, rows in (('jra_entries', entries), ('horse_id_candidates', browser), ('comparisons', checks)):
            write_jsonl(stage / f'{name}.jsonl', rows)
        matches = sum(r['explicit_clock_matches'] is True for r in checks)
        receipt = {f: job[f] for f in ('race_id', 'jra_local_race_key', 'legacy_race_key', 'race_date', 'venue', 'race_number',
                                        'source_pdf_url', 'source_pdf_path', 'source_pdf_sha256', 'source_pdf_page', 'source_pdf_column')}
        receipt.update({'state': 'saved_candidates_with_jra_comparison', 'host': socket.gethostname(),
            'title': title, 'source_browser': 'Safari page source', 'source_url': job['source_browser_url'],
            'handoff_zip_sha256': ZIP_SHA, 'handoff_manifest_sha256': MANIFEST_SHA,
            'handoff_manifest_and_prior_aice_keys_verified': True,
            'navigation_started_at_jst': started.astimezone(JST).isoformat(),
            'browser_source_saved_at_jst': captured.isoformat(), 'saved_at_jst': datetime.now(JST).isoformat(),
            'rows': len(entries), 'browser_result_rows': len(browser),
            'candidate_rows': sum(r['netkeiba_horse_id_candidate'] is not None for r in browser),
            'horse_number_and_name_match_rows': sum(r['horse_name_matches'] is True for r in checks),
            'explicit_clock_match_rows': matches, 'clock_hold_rows': len(checks) - matches,
            'separate_ditto_match_rows': sum(r['separate_ditto_matches'] is True for r in checks),
            'handoff_partial_conflict_rows': sum(r['handoff_partial_candidate_conflict'] for r in checks),
            'identity_verification': 'pending', 'prediction_time_verification': 'pending',
            'files_sha256': {f: sha256((stage / f).read_bytes()) for f in sorted(FILES)}})
        if batch_capture is not None:
            receipt['batch_capture'] = batch_capture
        (stage / 'receipt.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        stage.rename(dest)
    verified, added = read_checkpoint(job, entries)
    require(all(verified[name] == value for name, value in receipt.items()), 'Saved receipt changed after write')
    saved.append(verified)
    index.extend(added)
    publish_indexes(jobs, saved, index)
    return verified


@contextmanager
def writer_lock():
    require(OUTPUT.resolve() == OUTPUT and OUTPUT.is_dir(), 'Handoff output directory is missing or noncanonical')
    fd = os.open(OUTPUT / '.writer.lock', os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'a') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def save_batch(jobs, runners, partial, saved, index, manifest_path, capture_dir):
    manifest_bytes = manifest_path.read_bytes()
    manifest = json.loads(manifest_bytes)
    captures = manifest.get('captures') if isinstance(manifest, dict) else None
    require(isinstance(captures, list) and 1 <= len(captures) <= min(BATCH_MAX, RACES - len(saved)),
            'Batch must contain one to six captures from the remaining ordered queue')
    require(all(isinstance(row, dict) and set(row) == {'race_id', 'file_name', 'navigation_started_at_jst'}
                and all(isinstance(value, str) for value in row.values()) for row in captures),
            'Capture manifest must specify race_id, file_name and navigation_started_at_jst')
    require(len({item['file_name'].casefold() for item in captures}) == len(captures), 'Repeated capture filename')
    directory = capture_dir.absolute()
    require(directory.resolve() == directory, 'Capture directory must not resolve elsewhere')
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    prior = saved[-1]
    previous_started = datetime.fromisoformat(prior['navigation_started_at_jst'])
    previous_saved = datetime.fromisoformat(prior['saved_at_jst'])
    pending = []
    try:
        for offset, capture in enumerate(captures):
            job = jobs[len(saved) + offset]
            rid = capture['race_id']
            require(rid == job['race_id'] and capture['file_name'] == f'keiba-heroines-result-{rid}.html',
                    'Capture identifier or filename differs from the next ordered race')
            started = datetime.fromisoformat(capture['navigation_started_at_jst'])
            verify_navigation(started, previous_started, previous_saved)
            captured, raw = read_batch_source(fd, capture['file_name'])
            entries = runners[job['legacy_race_key']]
            prepared = prepare_capture(job, entries, partial, started, captured, raw)
            pending.append((job, entries, started, captured, raw, prepared))
            previous_started, previous_saved = started, captured
    finally:
        os.close(fd)
    manifest_sha = sha256(manifest_bytes)
    output = []
    for ordinal, (job, entries, started, captured, raw, prepared) in enumerate(pending, start=1):
        metadata = {'manifest_sha256': manifest_sha, 'ordinal': ordinal, 'total': len(pending)}
        output.append(commit(jobs, job, entries, saved, index, started, captured, raw, prepared, metadata))
    return {'committed_races': len(output), 'manifest_sha256': manifest_sha, 'receipts': output}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('preflight')
    commands.add_parser('status')
    jobs_command = commands.add_parser('jobs')
    jobs_command.add_argument('count', type=int, nargs='?', default=1)
    single = commands.add_parser('save')
    single.add_argument('raw_html', type=Path)
    single.add_argument('race_id')
    single.add_argument('navigation_started_at', type=datetime.fromisoformat)
    batch = commands.add_parser('save-batch')
    batch.add_argument('manifest', type=Path)
    batch.add_argument('capture_dir', type=Path)
    args = parser.parse_args()
    try:
        jobs, runners, partial = read_handoff(check_all_pdfs=args.command == 'preflight')
        if args.command == 'jobs':
            require(1 <= args.count <= BATCH_MAX, 'Jobs count must be between one and six')
        if args.command in ('save', 'save-batch'):
            with writer_lock():
                saved, index = inspect_saved(jobs, runners)
                if args.command == 'save-batch':
                    result = save_batch(jobs, runners, partial, saved, index, args.manifest, args.capture_dir)
                else:
                    require(len(saved) < RACES, 'Handoff queue already complete')
                    job = jobs[len(saved)]
                    require(args.race_id == job['race_id'] and args.raw_html.name == f'keiba-heroines-result-{args.race_id}.html',
                            'Capture identifier or filename differs from the next ordered race')
                    prior = saved[-1]
                    started = args.navigation_started_at
                    verify_navigation(started, datetime.fromisoformat(prior['navigation_started_at_jst']),
                                      datetime.fromisoformat(prior['saved_at_jst']))
                    parent = args.raw_html.parent.absolute()
                    require(parent.resolve() == parent, 'Capture directory must not resolve elsewhere')
                    fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                    try:
                        captured, raw = read_batch_source(fd, args.raw_html.name)
                    finally:
                        os.close(fd)
                    entries = runners[job['legacy_race_key']]
                    prepared = prepare_capture(job, entries, partial, started, captured, raw)
                    result = commit(jobs, job, entries, saved, index, started, captured, raw, prepared, None)
        else:
            saved, index = inspect_saved(jobs, runners)
            result = progress(jobs, saved, index)
            del result['receipts']
            if args.command == 'preflight':
                result['verified_original_pdfs'] = 40
                result['verified_prior_aice_races'] = 268
                result['verified_prior_heroines_races'] = 837
            if args.command == 'jobs':
                upcoming = jobs[len(saved):len(saved) + args.count]
                for job in upcoming:
                    verified_pdf(OUTPUT / job['source_pdf_path'], job['source_pdf_sha256'])
                result['jobs'] = upcoming
    except (AttributeError, KeyError, OSError, TypeError, ValueError) as error:
        parser.exit(1, f'476 handoff checkpoint refused: {error}\n')
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
