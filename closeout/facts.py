from dataclasses import dataclass, replace

from intake.batches import SourceBatch, batch_from_identity, batch_identity
from ledger.postings import OfficialDay, Posting, posting_body, posting_from_row
from ledger.resurvey_evidence import (ResurveyReceipt, effective_source_litres,
                                      posting_body as resurvey_posting_body, posting_evidence)
from storage import decode, encode, litre_text, parse_litres


@dataclass(frozen=True)
class FrozenFacts:
    number: int
    batch: SourceBatch
    region: str
    epoch: int
    initial_litres: int
    current_litres: int
    postings: tuple[Posting, ...]
    source_litres: int = 0
    adjustment_litres: int = 0
    resurveys: tuple[ResurveyReceipt, ...] = ()

    @property
    def day(self):
        return self.batch.day


def capture_facts(book: OfficialDay, region, epoch):
    return encode({
        'batch': batch_identity(book.batch),
        'region': region,
        'epoch': epoch,
        'initial': litre_text(book.initial_litres),
        'current': litre_text(book.current_litres),
        'postings': [[entry.sequence, entry.correction_id, posting_body(entry)] for entry in book.postings],
        'resurveys': [[entry.sequence, entry.resurvey_id, resurvey_posting_body(entry)]
                      for entry in book.resurvey_evidence],
        'source': litre_text(book.source_litres),
        'adjustment': litre_text(book.adjustment_litres),
    })


def load_facts(number, body):
    contents = decode(body)
    batch = batch_from_identity(contents['batch'])
    if 'resurveys' in contents:
        resurveys = tuple(posting_evidence(batch.day, sequence, resurvey_id, row_body)
                          for sequence, resurvey_id, row_body in contents['resurveys'])
        source = parse_litres(contents['source'])
        adjustment = parse_litres(contents['adjustment'])
        kinds = {receipt.sequence for receipt in resurveys}
        postings = tuple(replace(posting, kind='resurvey') if posting.sequence in kinds else posting
                         for posting in (posting_from_row(*row) for row in contents['postings']))
    else:
        # Legacy frozen bodies predate in-place resurveys: every posting was a
        # manual total adjustment and the original batch is the only source.
        resurveys = ()
        postings = tuple(posting_from_row(*row) for row in contents['postings'])
        source = effective_source_litres(batch, ())
        adjustment = sum(posting.delta for posting in postings)
    return FrozenFacts(number, batch, contents['region'], contents['epoch'],
                       parse_litres(contents['initial']), parse_litres(contents['current']),
                       postings, source, adjustment, resurveys)
