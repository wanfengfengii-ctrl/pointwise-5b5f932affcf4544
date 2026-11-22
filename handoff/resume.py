import sqlite3

from handoff.acceptance import accept_day
from handoff.ownership import completed_move, publish_move
from ledger.postings import settle_for_move
from storage import WarehouseError


def resume_move(warehouse, record):
    current = warehouse.directory.get_move(record.move_id)
    done = completed_move(current)
    if done is not None:
        return done
    try:
        source = warehouse.region(record.source)
        target = warehouse.region(record.target)
        preview = source.read_day(record.day, record.source_epoch)
        target.stage_day(record.day, record.target_epoch, record.move_id, preview)
        warehouse.emit('candidate_copied', record)
        settled = settle_for_move(source, record.day, record.source_epoch, record.move_id)
        warehouse.emit('source_settled', settled)
        accepted = accept_day(target, record.target_epoch, settled)
        warehouse.emit('target_accepted', accepted)
        result = publish_move(warehouse.directory, record, accepted)
    except (WarehouseError, sqlite3.Error):
        # Another executor may have published while this caller was reading the prior epoch.
        done = completed_move(warehouse.directory.get_move(record.move_id))
        if done is None:
            raise
        return done
    warehouse.emit('move_published', result)
    return result
