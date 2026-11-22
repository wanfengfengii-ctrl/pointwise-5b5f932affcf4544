"""Resurvey intake and formal posting for one original sample position."""
from intake.measurements import Measurement, normalize_measurements
from intake.stations import require_day, validate_source
from ledger.postings import commit_resurvey
from ledger.resurvey_evidence import ResurveyAdmission, admission_identity, admission_from_identity
from handoff.ownership import read_current_book
from storage import Conflict, InvalidInput, NotFound, NotOwner, identifier


def _normalize(day, measurement):
    if not isinstance(measurement, Measurement):
        raise InvalidInput('expected a resurvey measurement')
    validate_source(day, measurement.source)
    return normalize_measurements(day, (measurement,))[0]


def admit_resurvey(warehouse, day, batch_id, position, resurvey_id, measurement):
    require_day(day)
    identifier(batch_id, 'batch ID')
    identifier(resurvey_id, 'resurvey ID')
    if type(position) is not int or position < 1:
        raise InvalidInput('resurvey position must be a positive integer')
    normalized = _normalize(day, measurement)
    assignment = warehouse.directory.get_assignment(day)
    if assignment is None or not assignment['published'] or assignment['batch_id'] != batch_id:
        raise InvalidInput('resurvey must name the opened original batch')
    try:
        batch = warehouse.sources.get_batch(batch_id).batch
    except NotFound as error:
        raise InvalidInput('resurvey must name an admitted original batch') from error
    if batch.day != day or position > len(batch.measurements):
        raise InvalidInput('resurvey position is outside the opening batch')
    admission = ResurveyAdmission(day, batch_id, position, resurvey_id, normalized)
    identity = admission_identity(admission)
    with warehouse.directory.transaction(write=True) as connection:
        existing = connection.execute(
            'SELECT content FROM resurvey_admissions WHERE resurvey_id = ?', (resurvey_id,)
        ).fetchone()
        if existing is None:
            connection.execute(
                'INSERT INTO resurvey_admissions VALUES (?, ?, ?, ?, ?, ?)',
                (resurvey_id, *day.key, batch_id, position, identity),
            )
        elif existing['content'] != identity:
            raise Conflict(f'resurvey {resurvey_id} has different parameters')
    return admission


def find_admission(warehouse, day, resurvey_id):
    with warehouse.directory.transaction() as connection:
        row = connection.execute(
            '''SELECT content FROM resurvey_admissions
               WHERE resurvey_id = ? AND station = ? AND day = ?''', (resurvey_id, *day.key),
        ).fetchone()
    if row is None:
        return None
    return admission_from_identity(resurvey_id, row['content'])


def post_resurvey(warehouse, day, resurvey_id, region):
    require_day(day)
    identifier(resurvey_id, 'resurvey ID')
    warehouse.require_region(region)
    route, book = read_current_book(warehouse, day)
    for receipt in book.resurveys:
        if receipt.resurvey_id == resurvey_id:
            return receipt
    admission = find_admission(warehouse, day, resurvey_id)
    if admission is None:
        raise NotFound(f'resurvey {resurvey_id} was not admitted for {day.key}')
    if route.region != region:
        raise NotOwner(f'{region} is not the current owner of {day.key}')
    store = warehouse.region(region)
    warehouse.emit('resurvey_prepared', admission)
    receipt, created = commit_resurvey(store, day, admission, region, route.epoch)
    if created:
        warehouse.emit('resurvey_committed', receipt)
    return receipt
