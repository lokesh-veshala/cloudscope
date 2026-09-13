CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS cloud_accounts (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  provider text NOT NULL CHECK (provider IN ('aws','azure','gcp')),
  provider_account_id text NOT NULL,
  display_name text NOT NULL,
  region text NOT NULL DEFAULT 'us-east-1',
  credential_profile text NOT NULL DEFAULT 'cloudscope-test',
  role_arn text,
  roles_anywhere_profile_arn text,
  trust_anchor_arn text,
  sns_topic_arn text,
  connection_status text NOT NULL DEFAULT 'UNTESTED' CHECK (connection_status IN ('UNTESTED','CONNECTED','FAILED')),
  connection_checked_at timestamptz,
  last_collected_at timestamptz,
  last_error text,
  enabled boolean NOT NULL DEFAULT true,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(provider, provider_account_id)
);
ALTER TABLE cloud_accounts ADD COLUMN IF NOT EXISTS region text NOT NULL DEFAULT 'us-east-1';
ALTER TABLE cloud_accounts ADD COLUMN IF NOT EXISTS credential_profile text NOT NULL DEFAULT 'cloudscope-test';
ALTER TABLE cloud_accounts ADD COLUMN IF NOT EXISTS trust_anchor_arn text;
ALTER TABLE cloud_accounts ADD COLUMN IF NOT EXISTS connection_status text NOT NULL DEFAULT 'UNTESTED';
ALTER TABLE cloud_accounts ADD COLUMN IF NOT EXISTS connection_checked_at timestamptz;
ALTER TABLE cloud_accounts ADD COLUMN IF NOT EXISTS last_collected_at timestamptz;
ALTER TABLE cloud_accounts ADD COLUMN IF NOT EXISTS last_error text;

CREATE TABLE IF NOT EXISTS resources (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  cloud_account_id uuid NOT NULL REFERENCES cloud_accounts(id),
  provider text NOT NULL,
  region text NOT NULL,
  availability_zone text,
  resource_type text NOT NULL,
  provider_resource_type text NOT NULL,
  provider_resource_id text NOT NULL,
  resource_arn text,
  name text,
  state text NOT NULL,
  first_seen timestamptz NOT NULL,
  last_seen timestamptz NOT NULL,
  deleted_at timestamptz,
  metadata jsonb NOT NULL DEFAULT '{}',
  UNIQUE(cloud_account_id, provider_resource_type, provider_resource_id)
);
CREATE INDEX IF NOT EXISTS resources_query_idx ON resources(cloud_account_id, region, resource_type, state);
CREATE INDEX IF NOT EXISTS resources_metadata_gin ON resources USING gin(metadata);

CREATE TABLE IF NOT EXISTS resource_state_history (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  resource_id uuid NOT NULL REFERENCES resources(id),
  state text NOT NULL,
  valid_from timestamptz NOT NULL,
  valid_to timestamptz,
  CHECK (valid_to IS NULL OR valid_to > valid_from)
);
CREATE UNIQUE INDEX IF NOT EXISTS one_open_resource_state ON resource_state_history(resource_id) WHERE valid_to IS NULL;

CREATE TABLE IF NOT EXISTS resource_tag_history (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  resource_id uuid NOT NULL REFERENCES resources(id),
  tag_key text NOT NULL,
  tag_value text NOT NULL,
  valid_from timestamptz NOT NULL,
  valid_to timestamptz,
  CHECK (valid_to IS NULL OR valid_to > valid_from)
);
CREATE INDEX IF NOT EXISTS tag_history_assignment_idx ON resource_tag_history(tag_key, tag_value, valid_from, valid_to);
CREATE UNIQUE INDEX IF NOT EXISTS one_open_tag_value ON resource_tag_history(resource_id, tag_key) WHERE valid_to IS NULL;

CREATE TABLE IF NOT EXISTS pricing_catalog (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  provider text NOT NULL,
  service text NOT NULL,
  region text NOT NULL,
  lookup_dimensions jsonb NOT NULL,
  sku text NOT NULL,
  rate_code text NOT NULL,
  unit text NOT NULL,
  currency char(3) NOT NULL CHECK (currency = 'USD'),
  price_per_unit numeric(38,18) NOT NULL CHECK (price_per_unit >= 0),
  effective_from timestamptz NOT NULL,
  effective_to timestamptz,
  fetched_at timestamptz NOT NULL,
  source_checksum text NOT NULL,
  UNIQUE(provider, rate_code, effective_from)
);
CREATE INDEX IF NOT EXISTS pricing_lookup_idx ON pricing_catalog(provider, service, region, effective_from, effective_to);
CREATE INDEX IF NOT EXISTS pricing_dimensions_gin ON pricing_catalog USING gin(lookup_dimensions);

CREATE TABLE IF NOT EXISTS cost_intervals (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  resource_id uuid NOT NULL REFERENCES resources(id),
  usage_start timestamptz NOT NULL,
  usage_end timestamptz NOT NULL,
  amount_usd numeric(38,18),
  covered_seconds numeric(20,6) NOT NULL,
  requested_seconds numeric(20,6) NOT NULL,
  pricing_status text NOT NULL CHECK (pricing_status IN ('COMPLETE','PARTIAL','UNRESOLVED')),
  calculation_version integer NOT NULL,
  calculated_at timestamptz NOT NULL,
  CHECK (usage_end > usage_start),
  CHECK (covered_seconds >= 0 AND covered_seconds <= requested_seconds),
  UNIQUE(resource_id, usage_start, usage_end, calculation_version)
);
CREATE INDEX IF NOT EXISTS cost_range_idx ON cost_intervals(resource_id, usage_start, usage_end);

CREATE TABLE IF NOT EXISTS dashboards (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  cloud_account_id uuid NOT NULL REFERENCES cloud_accounts(id),
  name text NOT NULL,
  filter_expression jsonb NOT NULL DEFAULT '{}',
  created_by text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS dashboard_limits (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  dashboard_id uuid NOT NULL REFERENCES dashboards(id),
  configuration_version integer NOT NULL,
  limit_amount numeric(20,6) NOT NULL CHECK (limit_amount > 0),
  currency char(3) NOT NULL DEFAULT 'USD' CHECK (currency = 'USD'),
  period_type text NOT NULL DEFAULT 'MONTHLY' CHECK (period_type = 'MONTHLY'),
  thresholds numeric(8,4)[] NOT NULL DEFAULT ARRAY[80,100],
  enabled boolean NOT NULL DEFAULT true,
  effective_from timestamptz NOT NULL,
  effective_to timestamptz,
  created_by text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(dashboard_id, configuration_version)
);

CREATE TABLE IF NOT EXISTS threshold_events (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  dashboard_id uuid NOT NULL REFERENCES dashboards(id),
  limit_id uuid NOT NULL REFERENCES dashboard_limits(id),
  limit_version integer NOT NULL,
  period_start timestamptz NOT NULL,
  threshold_percent numeric(8,4) NOT NULL,
  estimated_cost numeric(20,6) NOT NULL,
  limit_amount numeric(20,6) NOT NULL,
  pricing_coverage numeric(8,7) NOT NULL CHECK (pricing_coverage = 1),
  status text NOT NULL CHECK (status IN ('PENDING','PUBLISHED','FAILED')),
  sns_message_id text,
  created_at timestamptz NOT NULL DEFAULT now(),
  published_at timestamptz,
  UNIQUE(dashboard_id, period_start, limit_version, threshold_percent)
);

CREATE TABLE IF NOT EXISTS collection_jobs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  cloud_account_id uuid NOT NULL REFERENCES cloud_accounts(id),
  collector text NOT NULL,
  scheduled_for timestamptz NOT NULL,
  started_at timestamptz,
  finished_at timestamptz,
  status text NOT NULL CHECK (status IN ('QUEUED','RUNNING','SUCCEEDED','FAILED')),
  error_code text,
  details jsonb NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS collection_queue_idx ON collection_jobs(status, scheduled_for);
