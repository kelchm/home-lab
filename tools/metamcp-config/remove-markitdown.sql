-- Run only after the MetaMCP Deployment has zero replicas and no remaining pods.
-- The registry is not GitOps-managed. Foreign keys cascade deletion to this
-- server's tools, namespace mappings, and upstream OAuth sessions only.
BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '15s';

DELETE FROM mcp_servers
WHERE uuid = 'c6585be3-32b7-4760-8ba4-a9fcceef2fe4'
  AND name = 'markitdown-mcp'
  AND url = 'http://markitdown-mcp.ai.svc.cluster.local:3001/mcp'
RETURNING uuid, name;

-- Re-running after successful removal is safe; unexpected registry drift fails
-- the transaction instead of silently leaving a retired server registered.
DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM mcp_servers
    WHERE name ILIKE '%markitdown%' OR url ILIKE '%markitdown%'
  ) THEN
    RAISE EXCEPTION 'Unexpected MarkItDown registry entry remains; inspect before retrying';
  END IF;
END;
$$;
COMMIT;
