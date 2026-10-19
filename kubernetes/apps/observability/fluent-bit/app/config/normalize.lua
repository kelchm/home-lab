-- Maps Kubernetes container log records onto the OpenTelemetry log data model
-- for VictoriaLogs' OTLP endpoint: service.*/k8s.* resource, severity_number
-- and severity_text, a readable body, and semantic-convention attributes.
-- docs/evaluations/log-schema-and-collector.md records each rule's source.
--
-- Two passes around the JSON parser filter: `capture` stores the collector's
-- identity and raw line in record metadata, then `normalize` builds the OTLP
-- record. Application payload keys therefore never overwrite identity.

-- Source level spellings → SeverityNumber (OTel data model ranges; Python,
-- PostgreSQL and klog follow their SDK or documented meaning).
local LEVELS = {
  TRACE = 1, TRC = 1,
  DEBUG = 5, DBG = 5, VERBOSE = 5, DEBUG1 = 5, DEBUG2 = 5, DEBUG3 = 5, DEBUG4 = 5, DEBUG5 = 5,
  INFO = 9, INF = 9, INFORMATION = 9, LOG = 9, I = 9,
  NOTICE = 10,
  WARN = 13, WRN = 13, WARNING = 13, W = 13,
  ERROR = 17, ERR = 17, E = 17,
  CRITICAL = 21, CRIT = 21, FATAL = 21, FTL = 21, PANIC = 21, F = 21,
}
-- pino numeric levels.
local NUMERIC_LEVELS = { ['10'] = 1, ['20'] = 5, ['30'] = 9, ['40'] = 13, ['50'] = 17, ['60'] = 21 }

-- Plain-text formats with the level at a fixed position, by application.
local TEXT_LEVEL = {
  kanidm = { '^%x+%-%x+%-%x+%-%x+%-%x+%s+(%a+)%s' },
  mcphub = { '^%[[^%]]+%]%s+%[(%a+)%]' },
  jellyfin = { '^%[[^%]]+%]%s+%[(%a+)%]' },
  unpoller = { '^%d%d%d%d/%d%d/%d%d %S+ %[(%a+)%]' },
  -- uvicorn access lines, then Python logging lines.
  bambuddy = { '^(%a+):', '^%d%d%d%d%-%d%d%-%d%d %d%d:%d%d:%d%d,%d+ (%a+) %[' },
  broadsheet = { '^(%a+): ' },
  flaresolverr = { '^%d%d%d%d%-%d%d%-%d%d %S+%s+(%a+)%s' },
}
-- Fallbacks for other plain text, accepted only when the token is a known
-- level: bracketed as the first token, or directly after a leading ISO-8601
-- timestamp, bracketed or bare.
local TEXT_LEVEL_FALLBACK = {
  '^%[(%a+)%]',
  '^%d%d%d%d%-%d%d%-%d%dT%S+%s+%[(%a+)%]',
  '^%d%d%d%d%-%d%d%-%d%dT%S+%s+(%a+)%s',
}

-- Traefik access-log fields with an HTTP semantic-convention name.
local TRAEFIK_FIELDS = {
  ClientHost = 'client.address', ClientPort = 'client.port',
  RequestMethod = 'http.request.method', RequestHost = 'server.address',
  RequestPort = 'server.port', RequestScheme = 'url.scheme',
  DownstreamStatus = 'http.response.status_code',
  DownstreamContentSize = 'http.response.body.size',
  RequestContentSize = 'http.request.body.size',
  TLSVersion = 'tls.protocol.version', TLSCipher = 'tls.cipher',
  ['request_User-Agent'] = 'user_agent.original',
}
local NUMERIC_ATTRIBUTES = {
  'client.port', 'server.port', 'http.response.status_code',
  'http.response.body.size', 'http.request.body.size', 'code.line.number',
}

local function present(v)
  if v ~= nil and tostring(v) ~= '' then return v end
end

local function first(...)
  for i = 1, select('#', ...) do
    local v = present(select(i, ...))
    if v then return v end
  end
end

local function flatten(t, prefix, out)
  for k, v in pairs(t) do
    local key = prefix and (prefix .. '.' .. k) or k
    if type(v) == 'table' then flatten(v, key, out) else out[key] = v end
  end
  return out
end

-- logfmt with quoted values and backslash escapes. Keys are matched only at
-- the start of a run of key characters, which keeps long lines linear.
local function logfmt(s)
  local out, pos = {}, 1
  while pos and pos <= #s do
    local _, last, key = s:find('^([%w_./%-]+)=', pos)
    if not last then
      local gap = s:find('[^%w_./%-]', pos)
      pos = gap and s:find('[%w_./%-]', gap)
    else
      local i, value = last + 1, {}
      if s:sub(i, i) == '"' then
        i = i + 1
        while i <= #s do
          local c = s:sub(i, i)
          if c == '"' then i = i + 1 break end
          if c == '\\' and i < #s then
            i = i + 1
            c = s:sub(i, i)
            c = ({ n = '\n', r = '\r', t = '\t' })[c] or c
          end
          value[#value + 1] = c
          i = i + 1
        end
      else
        local stop = s:find('%s', i) or (#s + 1)
        value[1] = s:sub(i, stop - 1)
        i = stop
      end
      out[key] = table.concat(value)
      pos = i
    end
  end
  return out
end

-- Seconds since the epoch for an RFC 3339 timestamp (the pod runs with TZ=UTC).
local function epoch(s)
  local y, mo, d, h, mi, se = tostring(s):match('^(%d+)%-(%d+)%-(%d+)T(%d+):(%d+):(%d+)')
  if not y then return nil end
  local t = os.time { year = tonumber(y), month = tonumber(mo), day = tonumber(d),
    hour = tonumber(h), min = tonumber(mi), sec = tonumber(se) }
  local sign, oh, om = tostring(s):match('([+-])(%d%d):(%d%d)$')
  if sign then t = t - (sign == '+' and 1 or -1) * (tonumber(oh) * 3600 + tonumber(om) * 60) end
  return t
end

-- Traefik's own `common` access-log line (v3.7 CommonLogFormatter).
local function traefik_common(a)
  -- toLog: strings may be quoted, other values print as-is, absent → default.
  local function field(key, quote, default)
    local v = a[key]
    if type(v) ~= 'string' then return v ~= nil and tostring(v) or (default or '-') end
    if v == '' then return default or '-' end
    return quote and ('"' .. v:gsub('"', '\\"') .. '"') or v
  end
  local start = epoch(a.StartUTC or a.StartLocal)
  local date = start and os.date('!%d/%b/%Y:%H:%M:%S +0000', start) or '-'
  return string.format('%s - %s [%s] "%s %s %s" %s %s %s %s %s %s %s %dms\n',
    field('ClientHost'), field('ClientUsername'), date,
    field('RequestMethod'), field('RequestPath'), field('RequestProtocol'),
    field('DownstreamStatus', true), field('DownstreamContentSize', true),
    field('request_Referer', true, '"-"'), field('request_User-Agent', true, '"-"'),
    field('RequestCount', true), field('RouterName', true, '"-"'), field('ServiceURL', true, '"-"'),
    math.floor((tonumber(a.Duration) or 0) / 1000000))
end

-- Resource attributes per the OTel Kubernetes conventions; also returns the
-- application name that prefixes non-semantic attributes.
local WORKLOAD_KINDS = {
  Deployment = 'deployment', ReplicaSet = 'replicaset', StatefulSet = 'statefulset',
  DaemonSet = 'daemonset', CronJob = 'cronjob', Job = 'job',
}
local function identity(k)
  local labels, annotations = k.labels or {}, k.annotations or {}
  local r = {
    ['k8s.cluster.name'] = 'k8s-prod',
    ['k8s.namespace.name'] = k.namespace_name,
    ['k8s.pod.name'] = k.pod_name,
    ['k8s.pod.uid'] = k.pod_id,
    ['k8s.container.name'] = k.container_name,
    ['k8s.node.name'] = k.host,
  }
  local workload
  for _, owner in ipairs(k.ownerReferences or {}) do
    local kind, name = owner.kind, owner.name
    -- A Deployment's ReplicaSet is named <deployment>-<pod-template-hash>.
    local hash = labels['pod-template-hash']
    if kind == 'ReplicaSet' and hash and name:sub(-#hash - 1) == '-' .. hash then
      kind, name = 'Deployment', name:sub(1, -#hash - 2)
    end
    if WORKLOAD_KINDS[kind] then
      r['k8s.' .. WORKLOAD_KINDS[kind] .. '.name'] = name
      workload = name
      break
    end
  end
  r['service.name'] = first(annotations['resource.opentelemetry.io/service.name'],
    labels['app.kubernetes.io/instance'], labels['app.kubernetes.io/name'],
    workload, k.pod_name, k.container_name)
  r['service.namespace'] = first(annotations['resource.opentelemetry.io/service.namespace'], k.namespace_name)
  if k.namespace_name and k.pod_name and k.container_name then
    r['service.instance.id'] = k.namespace_name .. '.' .. k.pod_name .. '.' .. k.container_name
  end
  local app = first(labels['app.kubernetes.io/name'], labels.app, workload, k.container_name, 'unknown')
  return r, app
end

function capture(tag, ts, group, metadata, record)
  metadata.ingress = { kubernetes = record.kubernetes or {}, raw = record.log or '', stream = record.stream }
  return 1, ts, metadata, { log = record.log or '' }
end

local function build(ingress, record)
  local raw = ingress.raw
  local resource, app = identity(ingress.kubernetes)
  local attributes = { ['log.iostream'] = ingress.stream }

  -- Payload fields: JSON from the parser filter, else logfmt (optionally after
  -- a bracketed prefix, as Longhorn instance-manager writes).
  local payload = flatten(record, nil, {})
  if payload.log == raw then payload.log = nil end
  local structured = next(payload) ~= nil
  if not structured then
    local kv = raw:match('^[%w_./%-]+=.*') or raw:match('^%[[^%]]+%]%s+([%w_./%-]+=.*)')
    if kv then
      payload = logfmt(kv)
      structured = next(payload) ~= nil
    end
  end

  local body = first(payload['record.message'], payload.msg, payload.message) or raw
  payload['record.message'], payload.msg, payload.message = nil, nil, nil

  local level, fixed_position
  for key, value in pairs(payload) do
    local lower = key:lower()
    if lower == 'level' or lower == 'lvl' or lower == 'severity' or lower == 'severitytext' then
      level = value
      payload[key] = nil
    end
  end
  level = first(payload['record.error_severity'], level)

  -- klog: I/W/E/F, MMDD, time, thread, file:line] message.
  local kl, thread, file, line, message =
    raw:match('^([IWEF])%d%d%d%d %d%d:%d%d:%d%d%.%d+%s+(%d+) ([^:]+):(%d+)%]%s+(.*)$')
  if kl then
    level = kl
    attributes['thread.id'] = tonumber(thread)
    attributes['code.file.path'] = file
    attributes['code.line.number'] = line
    if message:sub(1, 1) == '"' then
      local kv = logfmt('msg=' .. message)
      body = kv.msg or message
      kv.msg = nil
      for k, v in pairs(kv) do payload[k] = v end
    else
      body = message
    end
  end

  if not level and not structured then
    if TEXT_LEVEL[app] then
      fixed_position = true
      for _, pattern in ipairs(TEXT_LEVEL[app]) do
        level = raw:match(pattern)
        if level then break end
      end
    else
      for _, pattern in ipairs(TEXT_LEVEL_FALLBACK) do
        local token = raw:match(pattern)
        if token and LEVELS[token:upper()] then level = token break end
      end
    end
  end

  local severity
  if level then
    severity = LEVELS[tostring(level):upper()] or NUMERIC_LEVELS[tostring(level)]
    -- A level the source states but this table cannot map is a mapping bug.
    if not severity and (structured or fixed_position or kl) then
      attributes['pipeline.severity_unmapped'] = true
    end
  end

  if payload.RequestMethod and payload.DownstreamStatus then
    body = traefik_common(payload)
    for source, target in pairs(TRAEFIK_FIELDS) do
      if payload[source] ~= nil then
        attributes[target] = payload[source]
        payload[source] = nil
      end
    end
    if payload.RequestPath then
      attributes['url.path'] = payload.RequestPath:match('^[^?]*')
      attributes['url.query'] = payload.RequestPath:match('%?(.*)$')
      payload.RequestPath = nil
    end
    if payload.RequestProtocol then
      attributes['network.protocol.name'] = (payload.RequestProtocol:match('^[^/]+') or ''):lower()
      attributes['network.protocol.version'] = payload.RequestProtocol:match('/(.*)$')
      payload.RequestProtocol = nil
    end
  end

  -- Go slog `source=file:line`.
  if type(payload.source) == 'string' then
    local f, n = payload.source:match('^(.-):(%d+)$')
    if f then
      attributes['code.file.path'], attributes['code.line.number'] = f, n
      payload.source = nil
    end
  end

  for _, key in ipairs(NUMERIC_ATTRIBUTES) do
    if attributes[key] ~= nil then attributes[key] = tonumber(attributes[key]) end
  end
  -- HTTP semantic conventions: only 5xx marks a server request as failed.
  local status = attributes['http.response.status_code']
  if status and status >= 500 and status <= 599 then attributes['error.type'] = tostring(status) end

  for key, value in pairs(payload) do attributes[app .. '.' .. key] = value end

  attributes.otlp = { severity_number = severity or 0, severity_text = level and tostring(level) or nil }
  return attributes, body, resource
end

-- A Lua error would leave the whole batch unnormalized, so a record the mapping
-- cannot handle keeps its identity and raw line and is flagged instead.
function normalize(tag, ts, group, metadata, record)
  local ingress = metadata.ingress
  local ok, attributes, body, resource = pcall(build, ingress, record)
  if not ok then
    local found, r = pcall(identity, ingress.kubernetes)
    attributes = { ['log.iostream'] = ingress.stream, ['pipeline.normalize_error'] = tostring(attributes),
      otlp = { severity_number = 0 } }
    body, resource = ingress.raw, found and r or {}
  end
  return 1, ts, attributes, { body = body, resource = { attributes = resource } }
end
