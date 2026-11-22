from intake.measurements import sum_litres
from ledger.receipts import PostingReceipt
from ledger.resurvey_evidence import measurement_litres
from storage import InconsistentBook, InvalidInput


class ReconciliationError(InconsistentBook):
    pass


def check_day_balance(book, totals, epoch):
    try:
        from_source = sum_litres(book.batch.measurements)
    except InvalidInput as error:
        raise ReconciliationError('source batch has no effective readings') from error
    if from_source != book.initial_litres:
        raise ReconciliationError('source measurements disagree with the initial value')
    if len(book.postings) != book.frontier or len(book.resurveys) + len(book.receipts) != book.frontier:
        raise ReconciliationError('incomplete official postings or receipts')
    _check_resurvey_evidence(book, epoch)
    receipts = {receipt.correction_id: receipt for receipt in book.receipts}
    seen = set()
    previous = book.initial_litres
    last_epoch = 1
    last_region = None
    for sequence, (posting, (_, _, delta)) in enumerate(zip(book.postings, totals.per_posting, strict=True), 1):
        correction = posting.correction_id
        if posting.sequence != sequence:
            raise ReconciliationError(f'invalid official sequence at {correction}')
        if posting.kind == 'correction':
            if correction in seen:
                raise ReconciliationError(f'duplicate official receipt at {correction}')
            seen.add(correction)
        if posting.old_litres != previous:
            raise ReconciliationError(f'disconnected old value at {correction}')
        if posting.delta != delta:
            raise ReconciliationError(f'incorrect signed delta at {correction}')
        if not last_epoch <= posting.issued_epoch <= epoch:
            raise ReconciliationError(f'invalid issuing epoch at {correction}')
        if last_region is not None and posting.issued_epoch == last_epoch and posting.issued_region != last_region:
            raise ReconciliationError(f'inconsistent issuing region at {correction}')
        if posting.kind == 'correction':
            expected = PostingReceipt(book.batch.day, correction, sequence, posting.issued_region,
                                      posting.issued_epoch, previous, posting.new_litres, delta)
            if receipts.get(correction) != expected:
                raise ReconciliationError(f'missing or mismatched first receipt at {correction}')
        previous = posting.new_litres
        last_epoch, last_region = posting.issued_epoch, posting.issued_region
    if previous != book.current_litres or totals.closing_litres != book.current_litres:
        raise ReconciliationError('current value disagrees with the official chain')
    if book.source_litres + book.adjustment_litres != book.current_litres:
        raise ReconciliationError('source and adjustment totals disagree with the current value')


def _check_resurvey_evidence(book, epoch):
    positions = list(book.batch.measurements)
    firsts = {receipt.resurvey_id: receipt for receipt in book.resurveys}
    if len(firsts) != len(book.resurveys):
        raise ReconciliationError('duplicate resurvey first receipt')
    for evidence in book.resurvey_evidence:
        if evidence.batch_id != book.batch.batch_id:
            raise ReconciliationError(f'resurvey {evidence.resurvey_id} names another opening batch')
        if not 1 <= evidence.position <= len(positions):
            raise ReconciliationError(f'invalid resurvey position at {evidence.resurvey_id}')
        current = positions[evidence.position - 1]
        old = measurement_litres(current)
        new = measurement_litres(evidence.after)
        if evidence.before != current:
            raise ReconciliationError(f'resurvey {evidence.resurvey_id} does not carry the actual old image')
        if (evidence.old_litres, evidence.new_litres, evidence.delta) != (old, new, new - old):
            raise ReconciliationError(f'incorrect resurvey delta at {evidence.resurvey_id}')
        if not 1 <= evidence.issued_epoch <= epoch:
            raise ReconciliationError(f'invalid issuing epoch at {evidence.resurvey_id}')
        receipt = firsts.get(evidence.resurvey_id)
        if receipt is None or (receipt.sequence, receipt.batch_id, receipt.position,
                               receipt.before, receipt.after, receipt.old_litres,
                               receipt.new_litres, receipt.delta, receipt.issued_region,
                               receipt.issued_epoch) != (
                evidence.sequence, evidence.batch_id, evidence.position, evidence.before,
                evidence.after, evidence.old_litres, evidence.new_litres, evidence.delta,
                evidence.issued_region, evidence.issued_epoch):
            raise ReconciliationError(f'missing or mismatched first receipt at {evidence.resurvey_id}')
        positions[evidence.position - 1] = evidence.after
    if len(book.resurveys) != len(book.resurvey_evidence):
        raise ReconciliationError('resurvey evidence and first receipts disagree in count')
