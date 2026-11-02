-- Render {project} and {instance} from the validated deployment configuration.
-- The sink writes econmind_docker; usage_backfill preserves pre-sink counters.
CREATE OR REPLACE VIEW `{project}.econmind_cost_history.usage_events` AS
SELECT event_time, received_at, payload
FROM `{project}.econmind_cost_history.usage_backfill`
UNION ALL
SELECT timestamp AS event_time, receiveTimestamp AS received_at,
       TO_JSON(jsonPayload) AS payload
FROM `{project}.econmind_cost_history.econmind_docker`
WHERE resource.type = 'gce_instance'
  AND resource.labels.instance_id = '{instance}'
  AND JSON_VALUE(TO_JSON(jsonPayload), '$.event_type') = 'econmind_usage';
