CREATE TABLE IF NOT EXISTS worker_worlds.world_capabilities (
    world_version text PRIMARY KEY,
    capabilities jsonb NOT NULL
);

INSERT INTO worker_worlds.world_capabilities(world_version, capabilities)
VALUES ('1.0', '["controlled_time","scheduled_injections","messy_state","adversarial_content"]')
ON CONFLICT (world_version) DO UPDATE SET capabilities=EXCLUDED.capabilities;
