import argparse
import json
from dataclasses import asdict
from tempfile import TemporaryDirectory

from intake.measurements import Measurement
from intake.stations import SourceReference, StationDay
from storage import Conflict, NotOwner
from tests.fixture import Events, Interruption
from warehouse import Warehouse, freeze, get_frozen, init_sample, move, post, reconcile_day


def replacements(day):
    return {
        "R1": Measurement(None, SourceReference(day.station, "resurvey-a", "sheet-2/03")),
        "R2": Measurement(2, SourceReference(day.station, "resurvey-b", "sheet-3/03")),
        "Z1": Measurement(0, SourceReference(day.station, "resurvey-z", "sheet-4/02")),
        "Z1-other": Measurement(0, SourceReference(day.station, "resurvey-z", "sheet-5/02")),
    }


def joint():
    from warehouse import admit_resurvey, post_resurvey

    events = Events()
    observed = {}
    with TemporaryDirectory(prefix="resurvey-joint-") as root:
        with Warehouse(root, events=events) as w:
            _, opened = init_sample(w)
            day, batch_id = opened.day, opened.batch_id
            rows = replacements(day)
            original = freeze(w, day)
            post(w, day, "C1", "east", 11)
            admit_resurvey(w, day, batch_id, 3, "R2", rows["R2"])
            admit_resurvey(w, day, batch_id, 3, "R1", rows["R1"])
            pending = freeze(w, day)

            def post_missing(_move):
                events.interrupt_at("resurvey_committed")
                try:
                    post_resurvey(w, day, "R1", "east")
                except Interruption as lost:
                    observed["lost_receipt"] = lost.payload

            def hand_over(_request):
                events.on("candidate_copied", post_missing)
                observed["move"] = move(w, day, "M1", "west")

            events.on("resurvey_prepared", hand_over)
            try:
                post_resurvey(w, day, "R2", "east")
            except NotOwner as error:
                observed["late_east"] = type(error).__name__

        with Warehouse(root) as w:
            w.close_region("east")
            observed["before_second"] = reconcile_day(w, day)
            observed["replayed_receipt"] = post_resurvey(w, day, "R1", "west")
            observed["second_receipt"] = post_resurvey(w, day, "R2", "west")
            late = freeze(w, day)
            w.close_region("west")
            w.directory.close()
            w.sources.close()
            observed["original"] = get_frozen(w, original.number)
            observed["pending"] = get_frozen(w, pending.number)
            observed["late"] = get_frozen(w, late.number)
    return observed


def zero():
    from warehouse import admit_resurvey, post_resurvey

    observed = {}
    with TemporaryDirectory(prefix="resurvey-zero-") as root, Warehouse(root) as w:
        _, opened = init_sample(w)
        day, batch_id = opened.day, opened.batch_id
        rows = replacements(day)
        admit_resurvey(w, day, batch_id, 2, "Z1", rows["Z1"])
        try:
            admit_resurvey(w, day, batch_id, 2, "Z1", rows["Z1-other"])
        except Conflict as error:
            observed["changed_source"] = type(error).__name__
        observed["receipt"] = post_resurvey(w, day, "Z1", "east")
        observed["frozen"] = get_frozen(w, freeze(w, day).number)
    return observed


def inputs():
    return replacements(StationDay("CP01", "2026-11-18"))


def main():
    operations = {"inputs": inputs, "joint": joint, "zero": zero}
    parser = argparse.ArgumentParser()
    parser.add_argument("case", choices=tuple(operations))
    args = parser.parse_args()
    print(json.dumps(operations[args.case](), default=asdict, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
