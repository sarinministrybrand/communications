-- HGS Church Communications — schema
-- Scale target: 1e9 messages/day. The only table that sees that volume is
-- `delivery`; it is range-partitioned by day so a day can be detached and
-- archived in O(1) instead of DELETEd. Everything else is small.
--
-- Shard/partition key everywhere is church_id. Keep it in every index prefix.

CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ---------------------------------------------------------------- tenancy
CREATE TABLE church (
  id             bigserial PRIMARY KEY,
  name           text        NOT NULL,
  -- Esendex licence key resolution mirrors PrimeMessageSender.vb:
  --   church.license_key -> parent.license_key -> platform default
  license_key    uuid,
  -- dedicated long code; empty => send from the shared short code
  long_code      text        NOT NULL DEFAULT '',
  parent_id      bigint      REFERENCES church(id),
  created_at     timestamptz NOT NULL DEFAULT now()
);

-- Amplify/Unity -> MyCast tenant bridge. The real system keys these in
-- `unity_org_customer` (UnityOrgId, CustomerId, PlatformName), per
-- HGS-PWA Hgs.Amplify.Integration/Services/UnityService.cs. A host token names
-- a Unity org; this is the only place that turns one into a church_id, so an
-- unmapped org gets 403 rather than defaulting to any tenant.
CREATE TABLE unity_org_customer (
  unity_org_id  text   PRIMARY KEY,
  church_id     bigint NOT NULL REFERENCES church(id),
  platform_name text   NOT NULL DEFAULT 'amplify'
);
CREATE INDEX unity_org_church ON unity_org_customer (church_id);

CREATE TABLE person (
  id          bigserial PRIMARY KEY,
  church_id   bigint      NOT NULL REFERENCES church(id),
  first_name  text        NOT NULL DEFAULT '',
  last_name   text        NOT NULL DEFAULT '',
  phone       text,                      -- E.164
  email       text,
  created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX person_church_phone ON person (church_id, phone);
CREATE UNIQUE INDEX person_church_phone_uniq ON person (church_id, phone) WHERE phone IS NOT NULL;

CREATE TABLE grp (
  id         bigserial PRIMARY KEY,
  church_id  bigint NOT NULL REFERENCES church(id),
  name       text   NOT NULL
);

CREATE TABLE grp_member (
  church_id bigint NOT NULL,
  group_id  bigint NOT NULL REFERENCES grp(id) ON DELETE CASCADE,
  person_id bigint NOT NULL REFERENCES person(id) ON DELETE CASCADE,
  PRIMARY KEY (group_id, person_id)
);
CREATE INDEX grp_member_person ON grp_member (church_id, person_id);

-- Opt-out is a hard send-time gate. Keyed by number, not by person: the same
-- handset can appear on several person rows and STOP must silence all of them.
CREATE TABLE optout (
  church_id  bigint      NOT NULL REFERENCES church(id),
  phone      text        NOT NULL,
  reason     text        NOT NULL DEFAULT 'STOP',
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (church_id, phone)
);

-- ------------------------------------------------------------- broadcasts
CREATE TYPE broadcast_state AS ENUM ('draft','queued','fanning','sending','sent','failed','canceled');

CREATE TABLE broadcast (
  id            bigserial PRIMARY KEY,
  church_id     bigint          NOT NULL REFERENCES church(id),
  body          text            NOT NULL,
  state         broadcast_state NOT NULL DEFAULT 'draft',
  -- idempotency: a retried POST with the same key returns the first broadcast
  idem_key      text,
  audience      jsonb           NOT NULL DEFAULT '[]',   -- [{kind:group|person|all, id}]
  reach         integer         NOT NULL DEFAULT 0,      -- recipients after gating
  scheduled_at  timestamptz,
  created_by    bigint,
  created_at    timestamptz     NOT NULL DEFAULT now(),
  sent_at       timestamptz
);
CREATE UNIQUE INDEX broadcast_idem ON broadcast (church_id, idem_key) WHERE idem_key IS NOT NULL;
CREATE INDEX broadcast_church_created ON broadcast (church_id, created_at DESC);
-- the scheduler's hot query: due, not yet picked up
CREATE INDEX broadcast_due ON broadcast (scheduled_at) WHERE state = 'queued';

-- ---------------------------------------------------------------- delivery
-- One row per recipient per broadcast. This is the 1e9/day table.
-- PARTITION BY RANGE (created_at) with one partition per day.
CREATE TABLE delivery (
  id           bigserial,
  church_id    bigint      NOT NULL,
  broadcast_id bigint      NOT NULL,
  person_id    bigint,
  phone        text        NOT NULL,
  -- provider_msg_id is Esendex's MessageID for the OUTBOUND message. Inbound
  -- replies arrive carrying it as MatchedMessageID -- see app/esendex.py.
  provider_msg_id text,
  status       text        NOT NULL DEFAULT 'queued',  -- queued|sent|delivered|failed|undeliverable
  status_at    timestamptz,
  error        text,
  created_at   timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (id, created_at)
) PARTITION BY RANGE (created_at);

-- Lookup by provider id is how every DLR lands. Partition-local, so it stays
-- a small index per day rather than one 30-billion-row index.
CREATE INDEX delivery_provider ON delivery (provider_msg_id);
CREATE INDEX delivery_broadcast ON delivery (church_id, broadcast_id);

-- Partition management. Run daily (the worker calls ensure_delivery_partitions).
CREATE OR REPLACE FUNCTION ensure_delivery_partitions(days_ahead int DEFAULT 3)
RETURNS void LANGUAGE plpgsql AS $$
DECLARE d date; part text;
BEGIN
  FOR i IN 0..days_ahead LOOP
    d := (current_date + i);
    part := format('delivery_%s', to_char(d, 'YYYYMMDD'));
    IF NOT EXISTS (SELECT 1 FROM pg_class WHERE relname = part) THEN
      EXECUTE format(
        'CREATE TABLE %I PARTITION OF delivery FOR VALUES FROM (%L) TO (%L)',
        part, d, d + 1);
    END IF;
  END LOOP;
END $$;

-- ---------------------------------------------------------------- postback
-- Raw provider postbacks, append-only. The webhook commits a row here BEFORE
-- it answers 200, so a STOP cannot be lost between the provider and us.
CREATE TABLE postback (
  id           bigserial PRIMARY KEY,
  kind         text        NOT NULL,           -- inbound|dlr|accepted|unknown
  params       jsonb       NOT NULL,
  received_at  timestamptz NOT NULL DEFAULT now(),
  processed_at timestamptz
);
CREATE INDEX postback_pending ON postback (id) WHERE processed_at IS NULL;

-- ----------------------------------------------------------------- inbound
-- Every inbound message. Mirrors the live `sms_inbound` table.
CREATE TABLE inbound (
  id          bigserial PRIMARY KEY,
  church_id   bigint      NOT NULL REFERENCES church(id),
  person_id   bigint,
  from_phone  text        NOT NULL,
  to_phone    text        NOT NULL,
  body        text        NOT NULL,
  -- NOT a unique key: the live handler overwrites it with MatchedMessageID,
  -- so this is the id of the OUTBOUND message being answered when present.
  matched_msg_id text,
  reply_type  text        NOT NULL DEFAULT 'reply',  -- reply|stop|start|help|keyword
  received_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX inbound_church_received ON inbound (church_id, received_at DESC);
CREATE INDEX inbound_from ON inbound (church_id, from_phone);

-- The thing that does not exist in production today: a readable thread with
-- an assignee and a state. This is the only genuinely new concept here.
CREATE TYPE convo_state AS ENUM ('open','assigned','closed');

CREATE TABLE conversation (
  id          bigserial PRIMARY KEY,
  church_id   bigint      NOT NULL REFERENCES church(id),
  person_id   bigint,
  phone       text        NOT NULL,
  state       convo_state NOT NULL DEFAULT 'open',
  assignee_id bigint,
  last_at     timestamptz NOT NULL DEFAULT now(),
  unread      integer     NOT NULL DEFAULT 0,
  UNIQUE (church_id, phone)
);
CREATE INDEX conversation_inbox ON conversation (church_id, state, last_at DESC);

CREATE TABLE keyword (
  church_id bigint NOT NULL REFERENCES church(id),
  word      text   NOT NULL,
  reply     text   NOT NULL,
  group_id  bigint REFERENCES grp(id),
  PRIMARY KEY (church_id, word)
);

SELECT ensure_delivery_partitions(3);
