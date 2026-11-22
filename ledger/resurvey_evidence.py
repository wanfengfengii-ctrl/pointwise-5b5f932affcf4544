"""Immutable evidence and codecs for in-place source-position resurveys.

A resurvey replaces exactly one position of the original opening batch.  The
opening batch itself never changes: the official book keeps the original rows
and records each replacement as its own formal posting carrying the actual
before/after measurements and their provenance.
"""
from dataclasses import dataclass

from intake.measurements import Measurement, measurement_data, measurement_from_data
from intake.stations import StationDay
from storage import decode, encode, litre_text, parse_litres


@dataclass(frozen=True)
class ResurveyAdmission:
    day: StationDay
    batch_id: str
    position: int
    resurvey_id: str
    measurement: Measurement


@dataclass(frozen=True)
class ResurveyReceipt:
    day: StationDay
    batch_id: str
    position: int
    resurvey_id: str
    before: Measurement
    after: Measurement
    sequence: int
    issued_region: str
    issued_epoch: int
    old_litres: int
    new_litres: int
    delta: int


def admission_identity(admission):
    return encode([admission.day.key, admission.batch_id, admission.position,
                   measurement_data(admission.measurement)])


def admission_from_identity(resurvey_id, identity):
    """Rebuild the immutable admission from its stored identity."""
    day, batch_id, position, data = decode(identity)
    return ResurveyAdmission(StationDay(*day), batch_id, position, resurvey_id,
                             measurement_from_data(data))


def posting_body(receipt):
    """Posting evidence: opening batch, position and the actual before/after images."""
    return encode([receipt.batch_id, receipt.position,
                   measurement_data(receipt.before), measurement_data(receipt.after),
                   litre_text(receipt.old_litres), litre_text(receipt.new_litres),
                   litre_text(receipt.delta), receipt.issued_region, receipt.issued_epoch])


def posting_evidence(day, sequence, resurvey_id, body):
    batch_id, position, before, after, old, new, delta, region, epoch = decode(body)
    return ResurveyReceipt(day, batch_id, position, resurvey_id,
                           measurement_from_data(before), measurement_from_data(after),
                           sequence, region, epoch, parse_litres(old), parse_litres(new),
                           parse_litres(delta))


def receipt_body(receipt):
    return encode([receipt.day.key, receipt.resurvey_id, receipt.sequence, receipt.batch_id,
                   receipt.position, measurement_data(receipt.before),
                   measurement_data(receipt.after), litre_text(receipt.old_litres),
                   litre_text(receipt.new_litres), litre_text(receipt.delta),
                   receipt.issued_region, receipt.issued_epoch])


def receipt_from_body(body):
    day, resurvey_id, sequence, batch_id, position, before, after, old, new, delta, region, epoch = decode(body)
    return ResurveyReceipt(StationDay(*day), batch_id, position, resurvey_id,
                           measurement_from_data(before), measurement_from_data(after),
                           sequence, region, epoch, parse_litres(old), parse_litres(new),
                           parse_litres(delta))


def measurement_litres(measurement):
    """Missing readings are metered as zero while retaining their identity."""
    return 0 if measurement.litres is None else measurement.litres


def apply_positions(batch, receipts):
    """Apply committed resurveys in official order to the original positions."""
    rows = list(batch.measurements)
    for receipt in sorted(receipts, key=lambda item: item.sequence):
        if not 1 <= receipt.position <= len(rows):
            raise ValueError('resurvey position is outside the opening batch')
        rows[receipt.position - 1] = receipt.after
    return rows


def position_before(batch, receipts, position):
    if not 1 <= position <= len(batch.measurements):
        raise ValueError('resurvey position is outside the opening batch')
    return apply_positions(batch, receipts)[position - 1]


def effective_source_litres(batch, receipts):
    return sum(measurement_litres(row) for row in apply_positions(batch, receipts))
