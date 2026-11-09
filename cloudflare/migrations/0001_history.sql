-- D1 migration 0001. Apply locally only during this prototype.
-- statement-breakpoint
CREATE TABLE checks (
    check_id TEXT PRIMARY KEY NOT NULL,
    executed_at_utc TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('success', 'failed')),
    duration_seconds REAL NOT NULL CHECK(duration_seconds BETWEEN 0 AND 86400),
    error_code TEXT,
    request_hash TEXT NOT NULL CHECK(length(request_hash) = 64),
    CHECK((status = 'success' AND error_code IS NULL)
       OR (status = 'failed' AND error_code IS NOT NULL AND error_code IN
           ('TIMEOUT', 'LOOKUP_TIMEOUT', 'COURSE_LOOKUP_FAILED', 'CHECK_FAILED')))
);
-- statement-breakpoint
CREATE TABLE observations (
    observation_id INTEGER PRIMARY KEY,
    check_id TEXT NOT NULL REFERENCES checks(check_id),
    term TEXT NOT NULL,
    subject TEXT NOT NULL,
    course_number TEXT NOT NULL,
    section TEXT NOT NULL,
    crn TEXT NOT NULL,
    total_available INTEGER CHECK(total_available IS NULL OR
        (typeof(total_available) = 'integer' AND total_available BETWEEN 0 AND 1000000)),
    unreserved_available INTEGER CHECK(unreserved_available IS NULL OR
        (typeof(unreserved_available) = 'integer' AND unreserved_available BETWEEN 0 AND 1000000)),
    reserved_summary_json TEXT NOT NULL CHECK(json_valid(reserved_summary_json)),
    observed_at_utc TEXT NOT NULL,
    classification TEXT NOT NULL CHECK(classification IN
        ('full', 'unreserved', 'possible', 'reserved_only', 'unknown')),
    potential_available INTEGER NOT NULL CHECK(typeof(potential_available) = 'integer'
        AND potential_available BETWEEN 0 AND 1000000),
    UNIQUE(check_id, term, crn),
    CHECK(total_available IS NULL OR potential_available <= total_available),
    CHECK(total_available IS NOT NULL OR potential_available = 0)
);
-- statement-breakpoint
CREATE TABLE opening_events (
    event_id INTEGER PRIMARY KEY,
    observation_id INTEGER NOT NULL UNIQUE REFERENCES observations(observation_id),
    previous_observation_id INTEGER NOT NULL REFERENCES observations(observation_id),
    previous_available INTEGER NOT NULL CHECK(previous_available >= 0),
    new_available INTEGER NOT NULL CHECK(new_available > previous_available),
    detected_at_utc TEXT NOT NULL,
    classification TEXT NOT NULL CHECK(classification IN
        ('unreserved_increase', 'possible_increase'))
);
-- statement-breakpoint
CREATE INDEX idx_checks_time ON checks(executed_at_utc);
-- statement-breakpoint
CREATE INDEX idx_observations_course_time ON observations(term, crn, observed_at_utc DESC);
-- statement-breakpoint
CREATE INDEX idx_events_time ON opening_events(detected_at_utc);
-- statement-breakpoint
CREATE TRIGGER check_retry BEFORE UPDATE ON checks
WHEN OLD.request_hash != NEW.request_hash
BEGIN
    SELECT RAISE(ABORT, 'IDEMPOTENCY_CONFLICT');
END;
-- statement-breakpoint
CREATE TRIGGER observation_order BEFORE INSERT ON observations
BEGIN
    SELECT RAISE(ABORT, 'OBSERVATION_ORDER') WHERE EXISTS (
        SELECT 1 FROM observations WHERE term = NEW.term AND crn = NEW.crn
            AND observed_at_utc >= NEW.observed_at_utc
    );
    SELECT RAISE(ABORT, 'COURSE_IDENTITY') WHERE EXISTS (
        SELECT 1 FROM observations WHERE term = NEW.term AND crn = NEW.crn
            AND (subject != NEW.subject OR course_number != NEW.course_number OR section != NEW.section)
    );
END;
-- statement-breakpoint
CREATE TRIGGER detect_opening AFTER INSERT ON observations
BEGIN
    INSERT INTO opening_events
        (observation_id, previous_observation_id, previous_available,
         new_available, detected_at_utc, classification)
    SELECT NEW.observation_id, p.observation_id, p.potential_available,
        NEW.potential_available, NEW.observed_at_utc,
        CASE WHEN NEW.classification = 'unreserved' THEN 'unreserved_increase' ELSE 'possible_increase' END
    FROM observations p
    WHERE p.observation_id = (
        SELECT observation_id FROM observations
        WHERE term = NEW.term AND crn = NEW.crn AND observation_id != NEW.observation_id
        ORDER BY observed_at_utc DESC LIMIT 1
    ) AND p.total_available IS NOT NULL AND NEW.total_available IS NOT NULL
      AND NEW.potential_available > p.potential_available;
END;
