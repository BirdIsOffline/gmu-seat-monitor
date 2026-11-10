-- Direct durable ingestion: immutable samples, chronological neighbour projection.
-- Apply after 0001, with historical writers paused. No remote execution here.
-- statement-breakpoint
ALTER TABLE checks ADD COLUMN received_at_utc TEXT;
-- statement-breakpoint
CREATE INDEX idx_checks_received_time ON checks(received_at_utc);
-- statement-breakpoint
ALTER TABLE observations ADD COLUMN observation_uuid TEXT;
-- Existing samples predate UUIDs. Assign UUID4 once; preserve them in backups.
-- New samples use deterministic UUID5(check_id, "term:crn") in the Worker.
-- statement-breakpoint
UPDATE observations SET observation_uuid =
    lower(hex(randomblob(4))) || '-' || lower(hex(randomblob(2))) || '-4' ||
    substr(lower(hex(randomblob(2))), 2) || '-' || substr('89ab', (random() & 3) + 1, 1) ||
    substr(lower(hex(randomblob(2))), 2) || '-' || lower(hex(randomblob(6)));
-- statement-breakpoint
CREATE UNIQUE INDEX idx_observations_uuid ON observations(observation_uuid);
-- 0001 already rejected ties. This index also bounds neighbour/identity lookups.
-- statement-breakpoint
CREATE UNIQUE INDEX idx_observations_timeline ON observations(term, crn, observed_at_utc);
-- statement-breakpoint
DROP INDEX idx_observations_course_time;
-- statement-breakpoint
DROP TRIGGER observation_order;
-- statement-breakpoint
DROP TRIGGER detect_opening;
-- statement-breakpoint
CREATE TRIGGER observation_identity BEFORE INSERT ON observations
BEGIN
    SELECT RAISE(ABORT, 'OBSERVATION_UUID')
        WHERE NEW.observation_uuid IS NULL OR length(NEW.observation_uuid) != 36;
    SELECT RAISE(ABORT, 'OBSERVATION_TIME_CONFLICT') WHERE EXISTS (
        SELECT 1 FROM observations WHERE term = NEW.term AND crn = NEW.crn
            AND observed_at_utc = NEW.observed_at_utc
    );
    SELECT RAISE(ABORT, 'COURSE_IDENTITY') WHERE EXISTS (
        SELECT 1 FROM (
            SELECT subject, course_number, section FROM observations
            WHERE term = NEW.term AND crn = NEW.crn ORDER BY observed_at_utc LIMIT 1
        ) p WHERE p.subject != NEW.subject OR p.course_number != NEW.course_number OR p.section != NEW.section
    );
END;
-- statement-breakpoint
CREATE TRIGGER immutable_observation BEFORE UPDATE ON observations
BEGIN
    SELECT RAISE(ABORT, 'IMMUTABLE_OBSERVATION');
END;
-- check_retry from 0001 still rejects a changed request hash on retry.
-- statement-breakpoint
CREATE TRIGGER immutable_check BEFORE UPDATE ON checks
WHEN OLD.check_id IS NOT NEW.check_id OR OLD.executed_at_utc IS NOT NEW.executed_at_utc
    OR OLD.status IS NOT NEW.status OR OLD.duration_seconds IS NOT NEW.duration_seconds
    OR OLD.error_code IS NOT NEW.error_code OR OLD.received_at_utc IS NOT NEW.received_at_utc
BEGIN
    SELECT RAISE(ABORT, 'IMMUTABLE_CHECK');
END;
-- There are only two affected chronological edges on insertion: predecessor ->
-- NEW and NEW -> successor. Never scan/rebuild a course or use insertion IDs as
-- time. D1 executes this trigger within the same write transaction as receipt.
-- statement-breakpoint
CREATE TRIGGER project_openings AFTER INSERT ON observations
BEGIN
    DELETE FROM opening_events WHERE observation_id = (
        SELECT observation_id FROM observations
        WHERE term = NEW.term AND crn = NEW.crn AND observed_at_utc > NEW.observed_at_utc
        ORDER BY observed_at_utc LIMIT 1
    );

    INSERT INTO opening_events
        (observation_id, previous_observation_id, previous_available,
         new_available, detected_at_utc, classification)
    SELECT NEW.observation_id, p.observation_id, p.potential_available,
        NEW.potential_available, NEW.observed_at_utc,
        CASE WHEN NEW.classification = 'unreserved' THEN 'unreserved_increase' ELSE 'possible_increase' END
    FROM observations p
    WHERE p.observation_id = (
        SELECT observation_id FROM observations
        WHERE term = NEW.term AND crn = NEW.crn AND observed_at_utc < NEW.observed_at_utc
        ORDER BY observed_at_utc DESC LIMIT 1
    ) AND p.total_available IS NOT NULL AND NEW.total_available IS NOT NULL
      AND NEW.potential_available > p.potential_available;

    INSERT INTO opening_events
        (observation_id, previous_observation_id, previous_available,
         new_available, detected_at_utc, classification)
    SELECT n.observation_id, NEW.observation_id, NEW.potential_available,
        n.potential_available, n.observed_at_utc,
        CASE WHEN n.classification = 'unreserved' THEN 'unreserved_increase' ELSE 'possible_increase' END
    FROM observations n
    WHERE n.observation_id = (
        SELECT observation_id FROM observations
        WHERE term = NEW.term AND crn = NEW.crn AND observed_at_utc > NEW.observed_at_utc
        ORDER BY observed_at_utc LIMIT 1
    ) AND NEW.total_available IS NOT NULL AND n.total_available IS NOT NULL
      AND n.potential_available > NEW.potential_available;
END;
