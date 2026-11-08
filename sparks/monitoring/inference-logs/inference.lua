-- Maps a line of a Spark's inference server the way host.lua maps a journal
-- entry, then reads the request lines every TensorFold engine prints into one
-- set of fields. An engine that doesn't print a value leaves its field out;
-- nothing is estimated. host.name is not set here: the ingest endpoint stamps
-- it from the host's token.
dofile('/fluent-bit/etc/host.lua')

-- The Zig engine prints seconds as `0.920`, the Python one as `0.92s`, and -1
-- for a time it didn't measure.
local function seconds(v)
  local n = tonumber((v:gsub('s$', '')))
  if n and n >= 0 then return n end
end

-- Fields of a `done` line by the key the engines print, and how to read the
-- ones that need it. VictoriaLogs stores every value as text, so the rest are
-- kept as printed.
local DONE = {
  prompt = { 'inference.tokens.prompt' },
  cached = { 'inference.tokens.cached' },
  tokens = { 'inference.tokens.output' },
  thinking = { 'inference.thinking', string.lower },
  effort = { 'inference.effort' },
  finish = { 'inference.finish' },
  rounds = { 'inference.spec.rounds' },
  preemptions = { 'inference.preemptions' },
  queued = { 'inference.queue_s', seconds },
  ttft = { 'inference.ttft_s', seconds },
  prefill = { 'inference.prefill_s', seconds },
  decode = { 'inference.decode_s', seconds },
  elapsed = { 'inference.total_s', seconds },
  ['tok/s'] = { 'inference.output_tps' },
}

-- Requests that poll the server: a successful one is dropped, because metrics
-- already show the polling.
local POLLED = { ['/metrics'] = true, ['/health'] = true, ['/stats'] = true }

local function done(attributes, rest)
  -- Whole keys only: `draft_tokens=` is not `tokens=`.
  for key, value in rest:gmatch(' ([^%s=]+)=(%S+)') do
    local field = DONE[key]
    if field and field[2] then
      attributes[field[1]] = field[2](value)
    elseif field then
      attributes[field[1]] = value
    end
  end
  attributes['inference.spec.accepted'], attributes['inference.spec.drafted'] = rest:match(' accepted=(%d+)/(%d+)')
end

-- `[tensorfold] done <id> ... prompt=...`, `ended <id> reason=client-left ...`
-- or `reason=error (Kind) ...`, `refused <id>: <message>`, and the Zig
-- server's access line. Lines without the engine's prefix, or that only start
-- with one of those words, keep just the common fields. Returns false for a
-- line to drop.
local function request(attributes, line)
  local tagged
  line, tagged = line:gsub('^%[tensorfold%] ', '')
  if tagged == 0 then return true end
  local verb, id, rest = line:match('^(%a+) ([%w_.-]+)(.*)$')
  if verb == 'done' and rest:find(' prompt=%d') then
    attributes['event.name'] = 'inference.request.done'
    done(attributes, rest)
  elseif verb == 'ended' and rest:find('^ reason=') then
    attributes['event.name'] = 'inference.request.ended'
    attributes['inference.finish'] = rest:match('^ reason=(%S+)')
    attributes['error.type'] = rest:match('^ reason=%S+ %((.-)%)')
    attributes['inference.tokens.prompt'] = rest:match(' prompt=(%d+)')
    attributes['inference.tokens.output'] = rest:match(' tokens=(%d+)')
    local after = rest:match(' after=(%S+)')
    attributes['inference.total_s'] = after and seconds(after)
  elseif verb == 'refused' and rest:find('^: ') then
    attributes['event.name'] = 'inference.request.refused'
    attributes['inference.finish'] = 'refused'
  else
    local method, path, status = line:match('^%S+ "(%u+) ([^%s?"]*)[^"]*" (%d+)')
    if not method then return true end
    if POLLED[path] and status:sub(1, 1) == '2' then return false end
    attributes['http.request.method'] = method
    attributes['url.path'] = path
    attributes['http.response.status_code'] = status
    return true
  end
  attributes['inference.request.id'] = id
  return true
end

-- nil for an absent or empty label.
local function label(record, key)
  local v = record[key]
  if v ~= nil and tostring(v) ~= '' then return tostring(v) end
  return nil
end

-- A record from Docker's fluentd driver: the line as `log`, `source` stdout or
-- stderr, the container, and the labels SparkRun's adapter asks Docker to add.
local function build(tag, ts, group, metadata, record)
  local line = tostring(record.log or '')
  local code, time, attributes, body = normalize(tag, ts, group, metadata, {
    MESSAGE = line,
    PRIORITY = record.source == 'stderr' and 3 or 6,
    CONTAINER_NAME = label(record, 'container_name') and label(record, 'container_name'):gsub('^/', ''),
    CONTAINER_ID_FULL = label(record, 'container_id'),
  })
  attributes['service.name'] = 'inference'
  -- The same values as the inference metrics' model, runtime and recipe labels.
  attributes['inference.model'] = label(record, 'sparkrun.served_model_name')
  attributes['inference.runtime'] = label(record, 'sparkrun.runtime')
  local recipe = label(record, 'sparkrun.recipe')
  attributes['inference.recipe'] = recipe and recipe:gsub('^.*/', ''):gsub('%.ya?ml$', '')
  attributes['inference.rank'] = label(record, 'sparkrun.rank')
  if not request(attributes, line) then return -1, ts, metadata, record end
  return code, time, attributes, body
end

-- As in host.lua, a line the mapping cannot handle keeps its message and is
-- flagged rather than failing the batch.
function normalize_inference(tag, ts, group, metadata, record)
  local ok, code, time, attributes, body = pcall(build, tag, ts, group, metadata, record)
  if ok then return code, time, attributes, body end
  return 1, ts, { ['service.name'] = 'inference', ['pipeline.normalize_error'] = tostring(code),
    otlp = { severity_number = 0 } }, { body = tostring(record.log), resource = { attributes = {} } }
end
