from closeout.facts import FrozenFacts
from intake.batches import batch_identity
from intake.measurements import Measurement
from intake.stations import SourceReference
from ledger.postings import Posting, posting_body
from reconcile.discrepancies import ReconciliationError
from storage import Conflict, InvalidInput, NotFound, NotOwner, decode, encode, litre_text
from tests.fixture import DAY, OTHER, Interruption, WarehouseCase, pause_call, source_batch
from warehouse import (admit_resurvey, freeze, get_frozen, move, post, post_resurvey,
                       reconcile_day)


def replacement(day=DAY, litres=2, record='resurvey-b', locator='sheet-3/03'):
    return Measurement(litres, SourceReference(day.station, record, locator))


class ResurveyTests(WarehouseCase):
    def setUp(self):
        super().setUp()
        self.accepted, self.opened_day = self.opened()

    def test_admission_validation_and_replay(self):
        row = replacement()
        with self.assertRaises(InvalidInput):
            admit_resurvey(self.w, DAY, 'B1', True, 'R1', row)
        for bad_position in (0, -1, 1.0, '2'):
            with self.subTest(position=bad_position):
                with self.assertRaises(InvalidInput):
                    admit_resurvey(self.w, DAY, 'B1', bad_position, 'R1', row)
        with self.assertRaises(InvalidInput):
            admit_resurvey(self.w, DAY, 'missing', 1, 'R1', row)
        with self.assertRaises(InvalidInput):
            admit_resurvey(self.w, DAY, 'B1', 4, 'R1', row)
        with self.assertRaises(InvalidInput):
            admit_resurvey(self.w, DAY, 'B1', 1, 'R1', replacement(OTHER))
        with self.assertRaises(InvalidInput):
            admit_resurvey(self.w, DAY, 'B1', 1, 'bad id', row)
        with self.assertRaises(InvalidInput):
            admit_resurvey(self.w, DAY, 'B1', 1, 'R1', Measurement(True, row.source))
        with self.assertRaises(InvalidInput):
            admit_resurvey(self.w, DAY, 'B1', 1, 'R1', {'litres': 2})
        with self.assertRaises(NotFound):
            post_resurvey(self.w, DAY, 'R1', 'east')

        admitted = admit_resurvey(self.w, DAY, 'B1', 2, 'R1', row)
        self.assertEqual((admitted.day, admitted.batch_id, admitted.position,
                          admitted.resurvey_id, admitted.measurement),
                         (DAY, 'B1', 2, 'R1', row))
        self.assertEqual(admit_resurvey(self.w, DAY, 'B1', 2, 'R1', row), admitted)
        self.assertEqual(reconcile_day(self.w, DAY).postings, ())
        self.reopen()
        self.assertEqual(admit_resurvey(self.w, DAY, 'B1', 2, 'R1', row), admitted)

        for changed in (replacement(litres=3), replacement(locator='sheet-9/03')):
            with self.subTest(changed=changed):
                with self.assertRaises(Conflict):
                    admit_resurvey(self.w, DAY, 'B1', 2, 'R1', changed)
        with self.assertRaises(Conflict):
            admit_resurvey(self.w, DAY, 'B1', 1, 'R1', row)
        self.opened(source_batch(OTHER, 'B2'))
        with self.assertRaises(Conflict):
            admit_resurvey(self.w, OTHER, 'B2', 2, 'R1', replacement(OTHER))

    def test_post_uses_actual_old_image_and_net_delta(self):
        correction = post(self.w, DAY, 'C1', 'east', 11)
        admit_resurvey(self.w, DAY, 'B1', 3, 'R1', replacement(litres=2))
        with self.assertRaises(NotOwner):
            post_resurvey(self.w, DAY, 'R1', 'west')
        receipt = post_resurvey(self.w, DAY, 'R1', 'east')
        self.assertEqual((receipt.sequence, receipt.issued_region, receipt.issued_epoch), (2, 'east', 1))
        self.assertEqual(receipt.delta, -2)
        self.assertEqual(receipt.before, self.accepted.batch.measurements[2])
        self.assertEqual((receipt.old_litres, receipt.new_litres), (4, 2))
        self.assertEqual(receipt.after, replacement(litres=2))
        self.assertEqual(post_resurvey(self.w, DAY, 'R1', 'east'), receipt)
        self.assertEqual(post(self.w, DAY, 'C1', 'east', 11), correction)

        report = reconcile_day(self.w, DAY)
        self.assertEqual(report.initial_litres, 7)
        self.assertEqual((report.current_litres, report.source_litres, report.adjustment_litres), (9, 5, 4))
        self.assertEqual([(entry.correction_id, entry.kind, entry.sequence, entry.delta)
                          for entry in report.postings],
                         [('C1', 'correction', 1, 4), ('R1', 'resurvey', 2, -2)])
        self.assertEqual([(entry.resurvey_id, entry.sequence) for entry in report.resurveys], [('R1', 2)])

    def test_second_resurvey_reads_the_prior_official_image(self):
        admit_resurvey(self.w, DAY, 'B1', 3, 'R1', replacement(litres=None, record='resurvey-a',
                                                                locator='sheet-2/03'))
        first = post_resurvey(self.w, DAY, 'R1', 'east')
        admit_resurvey(self.w, DAY, 'B1', 3, 'R2', replacement(litres=2))
        second = post_resurvey(self.w, DAY, 'R2', 'east')
        self.assertEqual((second.sequence, second.old_litres, second.new_litres, second.delta), (2, 0, 2, 2))
        self.assertEqual((second.before.litres, second.before.source.locator), (None, 'sheet-2/03'))
        self.assertEqual((first.delta, first.before.litres), (-4, 4))

    def test_missing_is_metered_as_zero_but_retains_identity(self):
        admit_resurvey(self.w, DAY, 'B1', 3, 'R1',
                       replacement(litres=None, record='resurvey-a', locator='sheet-2/03'))
        receipt = post_resurvey(self.w, DAY, 'R1', 'east')
        self.assertEqual((receipt.before.litres, receipt.after.litres), (4, None))
        self.assertEqual((receipt.old_litres, receipt.new_litres, receipt.delta), (4, 0, -4))
        report = reconcile_day(self.w, DAY)
        self.assertEqual((report.current_litres, report.source_litres, report.adjustment_litres), (3, 3, 0))
        facts = get_frozen(self.w, freeze(self.w, DAY).number)
        self.assertEqual((facts.current_litres, facts.source_litres, facts.adjustment_litres), (3, 3, 0))
        self.assertEqual(facts.batch.measurements[2].litres, 4)
        self.assertEqual(facts.batch.measurements[2].source.locator, 'sheet-1/03')
        self.assertEqual([(row.after.litres, row.after.source.locator) for row in facts.resurveys],
                         [(None, 'sheet-2/03')])

    def test_zero_delta_occupies_sequence(self):
        admit_resurvey(self.w, DAY, 'B1', 2, 'Z1',
                       replacement(litres=0, record='resurvey-z', locator='sheet-4/02'))
        with self.assertRaises(Conflict):
            admit_resurvey(self.w, DAY, 'B1', 2, 'Z1',
                           replacement(litres=0, record='other', locator='sheet-5/02'))
        receipt = post_resurvey(self.w, DAY, 'Z1', 'east')
        self.assertEqual((receipt.sequence, receipt.old_litres, receipt.new_litres, receipt.delta), (1, 0, 0, 0))
        self.assertIsNone(receipt.before.litres)
        self.assertEqual(receipt.after.litres, 0)
        report = reconcile_day(self.w, DAY)
        self.assertEqual((report.current_litres, report.net_litres, len(report.postings)), (7, 0, 1))
        facts = get_frozen(self.w, freeze(self.w, DAY).number)
        self.assertEqual((facts.current_litres, facts.source_litres), (7, 7))
        self.assertEqual(len(facts.resurveys), 1)

    def test_resurvey_and_correction_may_share_a_number(self):
        correction = post(self.w, DAY, 'X', 'east', 11)
        admit_resurvey(self.w, DAY, 'B1', 3, 'X', replacement(litres=2))
        receipt = post_resurvey(self.w, DAY, 'X', 'east')
        self.assertEqual((correction.sequence, receipt.sequence), (1, 2))
        self.assertEqual(post_resurvey(self.w, DAY, 'X', 'east'), receipt)
        self.assertEqual(post(self.w, DAY, 'X', 'east', 11), correction)
        report = reconcile_day(self.w, DAY)
        self.assertEqual([(row.correction_id, row.kind) for row in report.postings],
                         [('X', 'correction'), ('X', 'resurvey')])

    def test_handoff_carries_committed_evidence_and_pending_admission(self):
        original = freeze(self.w, DAY)
        post(self.w, DAY, 'C1', 'east', 11)
        admit_resurvey(self.w, DAY, 'B1', 3, 'R1', replacement(litres=None, record='resurvey-a',
                                                                locator='sheet-2/03'))
        first = post_resurvey(self.w, DAY, 'R1', 'east')
        admit_resurvey(self.w, DAY, 'B1', 3, 'R2', replacement(litres=2))
        moved = move(self.w, DAY, 'M1', 'west')
        self.assertEqual(moved.frontier, 2)
        self.w.close_region('east')
        self.assertEqual(post_resurvey(self.w, DAY, 'R1', 'west'), first)
        second = post_resurvey(self.w, DAY, 'R2', 'west')
        self.assertEqual((second.sequence, second.issued_region, second.issued_epoch), (3, 'west', 2))
        self.assertEqual((second.before.litres, second.before.source.locator), (None, 'sheet-2/03'))
        self.assertEqual((second.old_litres, second.new_litres, second.delta), (0, 2, 2))
        report = reconcile_day(self.w, DAY)
        self.assertEqual((report.region, report.current_litres, report.source_litres,
                          report.adjustment_litres), ('west', 9, 5, 4))
        self.assertEqual([(row.resurvey_id, row.issued_region) for row in report.resurveys],
                         [('R1', 'east'), ('R2', 'west')])
        early = get_frozen(self.w, original.number)
        self.assertEqual((early.current_litres, early.resurveys, early.adjustment_litres), (7, (), 0))

    def test_pending_admission_posts_only_from_the_new_region(self):
        admit_resurvey(self.w, DAY, 'B1', 1, 'R1', replacement(litres=9, locator='sheet-2/01'))
        with pause_call(self.events, 'candidate_copied', lambda: move(self.w, DAY, 'M1', 'west')) as moving:
            self.events.interrupt_at('resurvey_prepared')
            with self.assertRaises(Interruption):
                post_resurvey(self.w, DAY, 'R1', 'east')
        self.assertEqual(moving.result().frontier, 0)
        self.w.close_region('east')
        with self.assertRaises(NotOwner):
            post_resurvey(self.w, DAY, 'R1', 'east')
        moved = post_resurvey(self.w, DAY, 'R1', 'west')
        self.assertEqual((moved.sequence, moved.delta, moved.issued_region), (1, 6, 'west'))
        self.assertEqual(reconcile_day(self.w, DAY).current_litres, 13)

    def test_events_order_and_replay_silence(self):
        seen = []
        self.events.on('resurvey_prepared', lambda payload: seen.append(('prepared', payload.resurvey_id)))
        self.events.on('resurvey_committed', lambda payload: seen.append(('committed', payload.resurvey_id)))
        admit_resurvey(self.w, DAY, 'B1', 3, 'R1', replacement(litres=2))
        post_resurvey(self.w, DAY, 'R1', 'east')
        self.assertEqual(seen, [('prepared', 'R1'), ('committed', 'R1')])
        post_resurvey(self.w, DAY, 'R1', 'east')
        self.assertEqual(seen, [('prepared', 'R1'), ('committed', 'R1')])

    def test_reconcile_rejects_tampered_evidence(self):
        admit_resurvey(self.w, DAY, 'B1', 3, 'R1', replacement(litres=2))
        post_resurvey(self.w, DAY, 'R1', 'east')
        store = self.w.region('east')
        with store.write_transaction() as connection:
            body = connection.execute(
                'SELECT body FROM resurvey_postings WHERE resurvey_id = ?', ('R1',)).fetchone()['body']
            contents = decode(body)
            contents[2][0] = litre_text(5)
            connection.execute('UPDATE resurvey_postings SET body = ? WHERE resurvey_id = ?',
                               (encode(contents), 'R1'))
        with self.assertRaisesRegex(ReconciliationError, 'actual old image'):
            reconcile_day(self.w, DAY)
        with self.assertRaises(ReconciliationError):
            freeze(self.w, DAY)

    def test_frozen_legacy_body_remains_readable(self):
        batch = self.accepted.batch
        body = encode({
            'batch': batch_identity(batch),
            'region': 'east',
            'epoch': 1,
            'initial': litre_text(7),
            'current': litre_text(11),
            'postings': [[1, 'C1', posting_body(Posting(1, 'C1', 7, 11, 4, 'east', 1))]],
        })
        number = self.w.archive.save_frozen(body).number
        loaded = get_frozen(self.w, number)
        self.assertIsInstance(loaded, FrozenFacts)
        self.assertEqual((loaded.current_litres, loaded.source_litres, loaded.adjustment_litres),
                         (11, 7, 4))
        self.assertEqual(loaded.resurveys, ())
        self.assertEqual([(row.correction_id, row.kind) for row in loaded.postings], [('C1', 'correction')])
        self.assertEqual(loaded.batch, batch)


class ResurveyFreezeTests(WarehouseCase):
    def test_freezes_survive_closing_regions_directory_and_sources(self):
        _, opened = self.opened()
        day, batch_id = opened.day, opened.batch_id
        original = freeze(self.w, day)
        post(self.w, day, 'C1', 'east', 11)
        admit_resurvey(self.w, day, batch_id, 3, 'R1', replacement(litres=None, record='resurvey-a',
                                                                   locator='sheet-2/03'))
        post_resurvey(self.w, day, 'R1', 'east')
        move(self.w, day, 'M1', 'west')
        admit_resurvey(self.w, day, batch_id, 3, 'R2', replacement(litres=2))
        post_resurvey(self.w, day, 'R2', 'west')
        late = freeze(self.w, day)
        self.reopen()
        self.w.close_region('east')
        self.w.close_region('west')
        self.w.directory.close()
        self.w.sources.close()
        first = get_frozen(self.w, original.number)
        last = get_frozen(self.w, late.number)
        self.assertEqual((first.current_litres, first.source_litres, first.adjustment_litres,
                          first.resurveys), (7, 7, 0, ()))
        self.assertEqual((last.current_litres, last.source_litres, last.adjustment_litres), (9, 5, 4))
        self.assertEqual([(row.resurvey_id, row.sequence, row.before.litres, row.after.litres)
                          for row in last.resurveys], [('R1', 2, 4, None), ('R2', 3, None, 2)])
        self.assertEqual(last.batch, first.batch)
        self.assertEqual([row.source.locator for row in last.batch.measurements],
                         ['sheet-1/01', 'sheet-1/02', 'sheet-1/03'])


class ResurveyCaseScriptTests(WarehouseCase):
    """The documented case scripts exercise the same chain end to end."""

    def test_joint_case(self):
        from resurvey_cases import joint
        observed = joint()
        self.assertTrue(observed['replayed_receipt'] == observed['lost_receipt'])
        self.assertEqual(observed['late_east'], 'NotOwner')
        self.assertEqual((observed['move'].frontier, observed['move'].source_epoch), (2, 1))
        self.assertEqual((observed['before_second'].region, observed['before_second'].current_litres,
                          observed['before_second'].source_litres,
                          observed['before_second'].adjustment_litres), ('west', 7, 3, 4))
        self.assertEqual((observed['second_receipt'].sequence, observed['second_receipt'].delta,
                          observed['second_receipt'].before.litres), (3, 2, None))
        self.assertEqual((observed['original'].current_litres, observed['original'].resurveys), (7, ()))
        self.assertEqual((observed['pending'].current_litres, observed['pending'].resurveys), (11, ()))
        self.assertEqual((observed['late'].current_litres, observed['late'].source_litres,
                          observed['late'].adjustment_litres), (9, 5, 4))
        self.assertEqual([row.resurvey_id for row in observed['late'].resurveys], ['R1', 'R2'])

    def test_zero_case(self):
        from resurvey_cases import zero
        observed = zero()
        self.assertEqual(observed['changed_source'], 'Conflict')
        self.assertEqual((observed['receipt'].sequence, observed['receipt'].delta), (1, 0))
        self.assertEqual((observed['frozen'].current_litres, observed['frozen'].source_litres), (7, 7))
        self.assertIsNone(observed['frozen'].resurveys[0].before.litres)
        self.assertEqual(observed['frozen'].resurveys[0].after.litres, 0)


class ResurveyAdmissionAfterMoveTests(WarehouseCase):
    def test_new_region_accepts_and_posts_new_admission(self):
        _, opened = self.opened()
        post(self.w, opened.day, 'C1', 'east', 11)
        move(self.w, opened.day, 'M1', 'west')
        self.w.close_region('east')
        admitted = admit_resurvey(self.w, opened.day, opened.batch_id, 1, 'R1',
                                  replacement(litres=9, locator='sheet-2/01'))
        self.assertEqual(admitted.position, 1)
        receipt = post_resurvey(self.w, opened.day, 'R1', 'west')
        self.assertEqual((receipt.sequence, receipt.issued_region, receipt.issued_epoch,
                          receipt.delta), (2, 'west', 2, 6))
        report = reconcile_day(self.w, opened.day)
        self.assertEqual((report.current_litres, report.source_litres,
                          report.adjustment_litres), (17, 13, 4))

    def test_admission_is_bound_to_one_original_batch(self):
        self.opened(source_batch(OTHER, 'B2'))
        with self.assertRaises(InvalidInput):
            admit_resurvey(self.w, OTHER, 'B1', 1, 'R1', replacement(OTHER))
        row = replacement(OTHER)
        admit_resurvey(self.w, OTHER, 'B2', 1, 'R1', row)
        with self.assertRaises(Conflict):
            admit_resurvey(self.w, OTHER, 'B2', 2, 'R1', row)


class ResurveyNumberingTests(WarehouseCase):
    def setUp(self):
        super().setUp()
        self.opened()

    def test_number_is_global_but_post_is_per_day(self):
        self.opened()
        self.opened(source_batch(OTHER, 'B2'))
        admit_resurvey(self.w, DAY, 'B1', 1, 'R1', replacement(litres=5))
        with self.assertRaises(Conflict):
            admit_resurvey(self.w, OTHER, 'B2', 1, 'R1', replacement(OTHER, litres=5))
        with self.assertRaises(NotFound):
            post_resurvey(self.w, OTHER, 'R1', 'east')
        receipt = post_resurvey(self.w, DAY, 'R1', 'east')
        self.assertEqual((receipt.sequence, receipt.delta), (1, 2))
        self.assertEqual(reconcile_day(self.w, OTHER).postings, ())

    def test_correction_sequence_interleaves_with_resurveys(self):
        post(self.w, DAY, 'C1', 'east', 11)
        admit_resurvey(self.w, DAY, 'B1', 3, 'R1', replacement(litres=2))
        first = post_resurvey(self.w, DAY, 'R1', 'east')
        second = post(self.w, DAY, 'C2', 'east', 20)
        self.assertEqual((first.sequence, second.sequence), (2, 3))
        self.assertEqual((second.old_litres, second.delta), (9, 11))
        report = reconcile_day(self.w, DAY)
        self.assertEqual((report.current_litres, report.source_litres,
                          report.adjustment_litres), (20, 5, 15))
        self.assertEqual([(row.correction_id, row.kind) for row in report.postings],
                         [('C1', 'correction'), ('R1', 'resurvey'), ('C2', 'correction')])
