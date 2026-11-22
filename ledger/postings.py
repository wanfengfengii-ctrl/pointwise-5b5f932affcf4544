from dataclasses import dataclass

from intake.batches import SourceBatch, batch_from_identity
from intake.stations import StationDay
from ledger.preparations import load_preparation
from ledger.receipts import (PostingReceipt, check_receipt_request, find_receipt, receipt_from_body,
                             record_receipt)
from ledger.region_store import BookImage, book_key
from ledger.resurvey_evidence import (ResurveyReceipt, effective_source_litres, measurement_litres,
                                      position_before, posting_body as resurvey_posting_body,
                                      posting_evidence, receipt_body as resurvey_receipt_body,
                                      receipt_from_body as resurvey_receipt_from_body)
from storage import Conflict, InconsistentBook, NotOwner, decode, encode, litre_text, parse_litres


@dataclass(frozen=True)
class Posting:
    sequence: int
    correction_id: str
    old_litres: int
    new_litres: int
    delta: int
    issued_region: str
    issued_epoch: int
    kind: str = 'correction'


@dataclass(frozen=True)
class OfficialDay:
    batch: SourceBatch
    initial_litres: int
    current_litres: int
    frontier: int
    postings: tuple[Posting, ...]
    receipts: tuple[PostingReceipt, ...]
    resurveys: tuple[ResurveyReceipt, ...] = ()
    resurvey_evidence: tuple[ResurveyReceipt, ...] = ()

    @property
    def source_litres(self):
        return effective_source_litres(self.batch, self.resurvey_evidence)

    @property
    def adjustment_litres(self):
        return sum(entry.delta for entry in self.postings if entry.kind == 'correction')


@dataclass(frozen=True)
class SettledDay:
    day: StationDay
    source: str
    source_epoch: int
    move_id: str
    image: BookImage


def posting_body(posting):
    return encode([litre_text(posting.old_litres), litre_text(posting.new_litres),
                   litre_text(posting.delta), posting.issued_region, posting.issued_epoch])


def posting_from_row(sequence, correction_id, body):
    old, new, delta, region, epoch = decode(body)
    return Posting(sequence, correction_id, parse_litres(old), parse_litres(new),
                   parse_litres(delta), region, epoch)


def interpret_image(day, image):
    try:
        batch = batch_from_identity(image.batch)
        corrections = [posting_from_row(sequence, correction_id, body)
                       for sequence, correction_id, body in image.postings]
        receipts = []
        for key, body in image.receipts:
            receipt = receipt_from_body(body)
            if receipt.correction_id != key or receipt.day != day:
                raise InconsistentBook('receipt is stored under the wrong correction key')
            receipts.append(receipt)
        evidence = [posting_evidence(day, sequence, resurvey_id, body)
                    for sequence, resurvey_id, body in image.resurvey_postings]
        resurveys = []
        for key, body in image.resurvey_receipts:
            receipt = resurvey_receipt_from_body(body)
            if receipt.resurvey_id != key or receipt.day != day:
                raise InconsistentBook('receipt is stored under the wrong resurvey key')
            resurveys.append(receipt)
        initial, current = parse_litres(image.initial_litres), parse_litres(image.current_litres)
    except (TypeError, ValueError, KeyError) as error:
        raise InconsistentBook('invalid official book material') from error
    if batch.day != day:
        raise InconsistentBook('source batch belongs to another station day')
    postings = _combined_postings(initial, corrections, evidence)
    return OfficialDay(batch, initial, current, image.frontier, postings, tuple(receipts),
                       tuple(sorted(resurveys, key=lambda row: row.sequence)),
                       tuple(sorted(evidence, key=lambda row: row.sequence)))


def _combined_postings(initial, corrections, evidence):
    """Merge both posting kinds into the single official sequence.

    Correction entries set the day value absolutely; resurvey entries pin
    sample evidence and derive the day value from the running frontier.
    """
    slots = {}
    for posting in corrections:
        if posting.sequence in slots:
            raise InconsistentBook('duplicate official sequence')
        slots[posting.sequence] = posting
    for posting in evidence:
        if posting.sequence in slots:
            raise InconsistentBook('duplicate official sequence')
        slots[posting.sequence] = posting
    combined = []
    running = initial
    for sequence in sorted(slots):
        entry = slots[sequence]
        if isinstance(entry, Posting):
            combined.append(entry)
            running = entry.new_litres
        else:
            new = running + entry.delta
            combined.append(Posting(sequence, entry.resurvey_id, running, new, entry.delta,
                                    entry.issued_region, entry.issued_epoch, 'resurvey'))
            running = new
    return tuple(combined)


def read_official_day(store, day, epoch):
    return interpret_image(day, store.read_day(day, epoch))


def commit_posting(store, request):
    key = book_key(request.day, request.epoch)
    with store.write_transaction() as connection:
        header = store.header(connection, request.day, request.epoch)
        existing = find_receipt(connection, request.day, request.epoch, request.correction_id)
        if existing is not None:
            return check_receipt_request(existing, request.day, request.litres), False
        if store.region != request.region or header['sealed_by'] is not None:
            raise NotOwner('correction epoch has handed over posting authority')
        if header['inbound_move'] is not None and header['accepted_digest'] is None:
            raise NotOwner('the receiving book has not accepted the handoff')
        new = load_preparation(connection, request.day, request.epoch, request.correction_id)
        if new is None or new != request.litres:
            raise InconsistentBook('missing or mismatched correction preparation')
        old = parse_litres(header['current_litres'])
        sequence = header['frontier'] + 1
        posting = Posting(sequence, request.correction_id, old, new, new - old, store.region, request.epoch)
        receipt = PostingReceipt(request.day, request.correction_id, sequence, store.region,
                                 request.epoch, old, new, posting.delta)
        connection.execute('INSERT INTO postings VALUES (?, ?, ?, ?, ?, ?)',
                           (*key, sequence, request.correction_id, posting_body(posting)))
        record_receipt(connection, request.epoch, receipt)
        connection.execute(
            'UPDATE books SET current_litres = ?, frontier = ? WHERE station = ? AND day = ? AND epoch = ?',
            (litre_text(new), sequence, *key),
        )
        connection.execute(
            'DELETE FROM preparations WHERE station = ? AND day = ? AND epoch = ? AND correction_id = ?',
            (*key, request.correction_id),
        )
        return receipt, True


def find_resurvey_receipt(connection, day, epoch, resurvey_id):
    row = connection.execute(
        'SELECT body FROM resurvey_receipts WHERE station = ? AND day = ? AND epoch = ? AND resurvey_id = ?',
        (*book_key(day, epoch), resurvey_id),
    ).fetchone()
    if row is None:
        return None
    receipt = resurvey_receipt_from_body(row['body'])
    if receipt.day != day or receipt.resurvey_id != resurvey_id:
        raise InconsistentBook('resurvey receipt is stored under the wrong key')
    return receipt


def commit_resurvey(store, day, admission, region, epoch):
    """Install one formal resurvey posting and its first receipt."""
    key = book_key(day, epoch)
    with store.write_transaction() as connection:
        header = store.header(connection, day, epoch)
        existing = find_resurvey_receipt(connection, day, epoch, admission.resurvey_id)
        if existing is not None:
            return existing, False
        if store.region != region or header['sealed_by'] is not None:
            raise NotOwner('resurvey epoch has handed over posting authority')
        if header['inbound_move'] is not None and header['accepted_digest'] is None:
            raise NotOwner('the receiving book has not accepted the handoff')
        image = store.read_image(connection, day, epoch)
        book = interpret_image(day, image)
        if admission.batch_id != book.batch.batch_id:
            raise InconsistentBook('resurvey admission names another opening batch')
        try:
            before = position_before(book.batch, book.resurvey_evidence, admission.position)
        except ValueError as error:
            raise InconsistentBook('resurvey position is outside the opening batch') from error
        after = admission.measurement
        old = measurement_litres(before)
        new = measurement_litres(after)
        delta = new - old
        sequence = header['frontier'] + 1
        receipt = ResurveyReceipt(day, admission.batch_id, admission.position, admission.resurvey_id,
                                  before, after, sequence, store.region, epoch, old, new, delta)
        connection.execute('INSERT INTO resurvey_postings VALUES (?, ?, ?, ?, ?, ?)',
                           (*key, sequence, admission.resurvey_id, resurvey_posting_body(receipt)))
        connection.execute('INSERT INTO resurvey_receipts VALUES (?, ?, ?, ?, ?)',
                           (*key, admission.resurvey_id, resurvey_receipt_body(receipt)))
        current = parse_litres(header['current_litres'])
        connection.execute(
            'UPDATE books SET current_litres = ?, frontier = ? WHERE station = ? AND day = ? AND epoch = ?',
            (litre_text(current + delta), sequence, *key),
        )
        return receipt, True


def settle_for_move(store, day, epoch, move_id):
    with store.write_transaction() as connection:
        header = store.header(connection, day, epoch)
        existing = header['sealed_by']
        if existing is not None and existing != move_id:
            raise Conflict('the source book was settled for a different move')
        if existing is None:
            connection.execute('UPDATE books SET sealed_by = ? WHERE station = ? AND day = ? AND epoch = ?',
                               (move_id, *book_key(day, epoch)))
        image = store.read_image(connection, day, epoch)
        return SettledDay(day, store.region, epoch, move_id, image)
