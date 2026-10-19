-- Maps Kubernetes container log records, and Talos service and kernel logs,
-- onto the OpenTelemetry log data model for VictoriaLogs' OTLP endpoint:
-- service.*/k8s.* resource, severity_number and severity_text, a readable
-- body, and semantic-convention attributes.
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
  CRITICAL = 21, CRIT = 21, FATAL = 21, FTL = 21, PANIC = 21, DPANIC = 21, F = 21,
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
  -- Level right-aligned in five columns: `[ warn]`, `[error]`.
  ['fluent-bit'] = { '^%[%d%d%d%d/%d%d/%d%d %d%d:%d%d:%d%d%.%d+%] %[ *(%a+)%]' },
}
-- Fallbacks for other plain text, accepted only when the token is a known
-- level: bracketed as the first token, directly after a leading ISO-8601
-- timestamp (bracketed or bare), or gunicorn's `[time] [pid] [LEVEL]`.
local TEXT_LEVEL_FALLBACK = {
  '^%[(%a+)%]',
  '^%d%d%d%d%-%d%d%-%d%dT%S+%s+%[(%a+)%]',
  '^%d%d%d%d%-%d%d%-%d%dT%S+%s+(%a+)%s',
  '^%[[^%]]+%]%s+%[%d+%]%s+%[(%a+)%]',
}
-- ANSI SGR/CSI sequences (terminal colours) that some applications print.
local ANSI = '\27%[[0-?]*[ -/]*[@-~]'

-- Traefik access-log fields with an HTTP semantic-convention name. Client
-- fields are mapped separately: ClientHost can be a forwarded address list.
local TRAEFIK_FIELDS = {
  RequestMethod = 'http.request.method', RequestHost = 'server.address',
  RequestPort = 'server.port', RequestScheme = 'url.scheme',
  DownstreamStatus = 'http.response.status_code',
  DownstreamContentSize = 'http.response.body.size',
  RequestContentSize = 'http.request.body.size',
  TLSVersion = 'tls.protocol.version', TLSCipher = 'tls.cipher',
  ['request_User-Agent'] = 'user_agent.original',
}
local NUMERIC_ATTRIBUTES = {
  'client.port', 'network.peer.port', 'server.port', 'http.response.status_code',
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

-- Attribute names are schema, never data (VictoriaLogs FAQ: dynamically keyed
-- objects must not become fields). Arrays stay whole; an object whose keys are
-- data is stored as one JSON value.
-- Fluent Bit tags tables converted from msgpack: type 1 array, 2 map.
local function is_array(t)
  local mt = getmetatable(t)
  return mt ~= nil and mt.type == 1
end

local IDENTIFIER = '^[%a_][%w_%-]*$'
-- Data-keyed maps whose keys happen to look like identifiers.
local DATA_MAPS = { ['victoria-metrics-operator.spec_diff'] = true }

local function data_keyed(t, path)
  if DATA_MAPS[path] then return true end
  local keys = false
  for k in pairs(t) do
    if type(k) == 'string' and k:find(IDENTIFIER) then return false end
    keys = true
  end
  return keys
end

local ESCAPES = { ['"'] = '\\"', ['\\'] = '\\\\', ['\n'] = '\\n', ['\r'] = '\\r', ['\t'] = '\\t' }

-- Deterministic JSON: sorted keys, integers without a fraction, nulls kept.
local function json(v)
  local kind = type(v)
  if kind == 'string' then
    return '"' .. v:gsub('[%c"\\]', function(c) return ESCAPES[c] or string.format('\\u%04x', c:byte()) end) .. '"'
  elseif kind == 'number' then
    if v == math.floor(v) and math.abs(v) < 2^53 then return string.format('%d', v) end
    return string.format('%.17g', v)
  elseif kind == 'boolean' then
    return tostring(v)
  elseif kind == 'table' then
    local parts = {}
    if is_array(v) then
      local n = 0
      for k in pairs(v) do if type(k) == 'number' and k > n then n = k end end
      for i = 1, n do parts[i] = json(v[i]) end
      return '[' .. table.concat(parts, ',') .. ']'
    end
    local keys = {}
    for k in pairs(v) do keys[#keys + 1] = tostring(k) end
    table.sort(keys)
    for i, k in ipairs(keys) do parts[i] = json(k) .. ':' .. json(v[k]) end
    return '{' .. table.concat(parts, ',') .. '}'
  end
  return 'null'
end

-- `encoded` collects the keys whose values json() produced. A JSON null
-- (flb_null, Enable_Flb_Null) is kept inside arrays and encoded objects and
-- leaves a field absent.
local function flatten(t, prefix, out, app, encoded)
  for k, v in pairs(t) do
    local key = prefix and (prefix .. '.' .. k) or k
    if v == flb_null then
      -- absent
    elseif type(v) ~= 'table' or is_array(v) then
      out[key] = v
    elseif data_keyed(v, app .. '.' .. key) then
      out[key] = json(v)
      encoded[key] = true
    else
      flatten(v, key, out, app, encoded)
    end
  end
  return out
end

-- `host:port` or `[ipv6]:port`.
local function host_port(s)
  if type(s) ~= 'string' then return end
  local host, port = s:match('^%[(.*)%]:(%d+)$')
  if not host then host, port = s:match('^([^:]+):(%d+)$') end
  return host, tonumber(port)
end

-- Source locations: Go slog `source`, zap/logr `caller`, logrus `file`.
local LOCATION_KEYS = { 'caller', 'file', 'source' }
local SOURCE_EXTENSIONS = { go = true, py = true, rs = true, java = true, js = true, ts = true,
  rb = true, c = true, cc = true, cpp = true, cs = true }

-- Only `<file>.<source extension>:<line>`, so host:port and data stay; a
-- location already read from klog wins.
local function location(payload, attributes)
  for _, key in ipairs(LOCATION_KEYS) do
    local v = payload[key]
    if not attributes['code.file.path'] and type(v) == 'string' then
      local path, ext, n = v:match('^(.+%.(%a+)):(%d+)$')
      if path and SOURCE_EXTENSIONS[ext] then
        attributes['code.file.path'], attributes['code.line.number'] = path, tonumber(n)
        payload[key] = nil
      end
    end
  end
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
  local raw = (record.log or ''):gsub(ANSI, '')
  -- A blank line carries nothing; VictoriaLogs would store a placeholder message.
  if not raw:find('%S') then return -1, ts, metadata, record end
  metadata.ingress = { kubernetes = record.kubernetes or {}, raw = raw, stream = record.stream }
  return 1, ts, metadata, { log = raw }
end

local function build(ingress, record)
  local raw = ingress.raw
  local resource, app = identity(ingress.kubernetes)
  local attributes = { ['log.iostream'] = ingress.stream }

  -- Payload fields: JSON from the parser filter, else logfmt (optionally after
  -- a bracketed prefix, as Longhorn instance-manager writes). A record keyed
  -- entirely by data stays whole in the message.
  local encoded = {}
  local payload = data_keyed(record, app) and {} or flatten(record, nil, {}, app, encoded)
  if payload.log == raw then payload.log = nil end
  local structured = next(payload) ~= nil
  if not structured then
    local kv = raw:match('^[%w_./%-]+=.*') or raw:match('^%[[^%]]+%]%s+([%w_./%-]+=.*)')
    if kv then
      payload = logfmt(kv)
      structured = next(payload) ~= nil
    end
  end

  -- The first non-empty scalar message is the body; empty ones carry nothing.
  local body
  for _, key in ipairs({ 'record.message', 'msg', 'message' }) do
    local v = payload[key]
    if not encoded[key] and (type(v) == 'string' or type(v) == 'number') then
      if not body and present(v) then body = v; payload[key] = nil
      elseif not present(v) then payload[key] = nil end
    end
  end
  body = body or raw

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

  if app == 'traefik' and payload.RequestMethod and payload.DownstreamStatus then
    body = traefik_common(payload)
    for source, target in pairs(TRAEFIK_FIELDS) do
      if payload[source] ~= nil then
        attributes[target] = payload[source]
        payload[source] = nil
      end
    end
    -- ClientAddr is the connection peer. Traefik replaces ClientHost with the
    -- raw X-Forwarded-For when present, so client.* takes one address only and
    -- a port only when that address is the peer's.
    local peer, peer_port = host_port(payload.ClientAddr)
    if peer then
      attributes['network.peer.address'], attributes['network.peer.port'] = peer, peer_port
      payload.ClientAddr, payload.ClientPort = nil, nil
    end
    local client = type(payload.ClientHost) == 'string' and payload.ClientHost:match('^%s*([^,%s]+)%s*$')
    if client then
      attributes['client.address'] = client
      if client == peer then attributes['client.port'] = peer_port end
      payload.ClientHost = nil
    end
    -- Exact repeats: StartLocal of StartUTC, RequestAddr of server.*, and `time`
    -- (the log write, to the second) of the record time.
    if payload.StartUTC then payload.StartLocal = nil end
    if attributes['server.address'] then payload.RequestAddr = nil end
    payload.time = nil
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

  location(payload, attributes)

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

-- Talos service and kernel logs: machined's json_lines (msg, talos-time,
-- talos-level, talos-service, plus the fields Talos split from the line) and
-- its kernel log (msg, talos-time, facility, priority, seq, clock).
-- docs/runbooks/logging.md#talos-node-logs lists what each service writes.
local NODE = os.getenv('NODE_NAME')
-- Kernel priorities as the OpenTelemetry Collector's syslog parser maps them.
local KERNEL_SEVERITY = { emerg = 21, alert = 19, crit = 18, err = 17, warning = 13, notice = 10, info = 9, debug = 5 }
-- Services whose lines state a level, which Talos reports as talos-level. For
-- every other line talos-level is Talos's default, not the source's.
local TALOS_LEVELED = { containerd = true, cri = true, etcd = true }
local TALOS_KEYS = { 'msg', 'talos-time', 'talos-level', 'talos-service' }
local TALOS_RESOURCE = { ['service.namespace'] = 'talos', ['host.name'] = NODE, ['k8s.node.name'] = NODE,
  ['k8s.cluster.name'] = 'k8s-prod' }
-- Leading write times: zap's console encoder (`2006-01-02T15:04:05.000Z LEVEL`,
-- machined's controllers), Go's log package (`2006/01/02 15:04:05.000000`,
-- machined, apid, trustd) and CNI plugins in cri (`2006-01-02T15:04:05Z [level]`).
local ZAP_PREFIX = '^(%d%d%d%d)%-(%d%d)%-(%d%d)T(%d%d):(%d%d):(%d%d)%.?(%d*)Z (%u+) (.*)$'
local GO_PREFIX = '^(%d%d%d%d)/(%d%d)/(%d%d) (%d%d):(%d%d):(%d%d)%.?(%d*) (.*)$'
local BRACKET_PREFIX = '^(%d%d%d%d)%-(%d%d)%-(%d%d)T(%d%d):(%d%d):(%d%d)%.?(%d*)Z %[(%a+)%] (.*)$'
local KLOG = '^([IWEF])%d%d%d%d %d%d:%d%d:%d%d%.%d+%s+(%d+) ([^:]+):(%d+)%]%s+(.*)$'
local RFC3339 = '^(%d+)%-(%d+)%-(%d+)T(%d+):(%d+):(%d+)%.?(%d*)([Z+-])(%d*):?(%d*)$'
-- logrus text always opens with its timestamp.
local LOGRUS = '^time="?%d%d%d%d%-%d%d%-%d%dT'

local function stamp(y, mo, d, h, mi, se, frac)
  return { sec = os.time { year = tonumber(y), month = tonumber(mo), day = tonumber(d),
    hour = tonumber(h), min = tonumber(mi), sec = tonumber(se) }, nsec = tonumber((frac .. '000000000'):sub(1, 9)) }
end

-- `{sec, nsec}` for an RFC 3339 timestamp, applying its offset.
local function rfc3339(v)
  local y, mo, d, h, mi, se, frac, zone, oh, om = tostring(v):match(RFC3339)
  if not y or (zone ~= 'Z' and (#oh ~= 2 or #om ~= 2)) then return nil end
  local t = stamp(y, mo, d, h, mi, se, frac)
  if zone ~= 'Z' then t.sec = t.sec - (zone == '+' and 1 or -1) * (tonumber(oh) * 3600 + tonumber(om) * 60) end
  return t
end

local function talos_source(record)
  if type(record['talos-service']) == 'string' then return record['talos-service'] end
  if record.facility ~= nil and record.seq ~= nil then return 'kernel' end
  return 'unknown'
end

-- auditd repeats the kernel's SELinux AVC records (gigabytes a day here);
-- machined logs every successful local API call, nearly all from the console
-- dashboard, which apid also logs when the call is remote; dns-resolve-cache
-- debug lines dump every DNS query.
local function talos_dropped(source, msg)
  if source == 'auditd' then return true end
  if source == 'machined' then
    local rest = select(8, msg:match(GO_PREFIX)) or msg
    return rest:find('^machined/authz/%S+ authorized ') or rest:find('^machined OK %[')
  end
  return source == 'dns-resolve-cache' and select(8, (msg:gsub(ANSI, '')):match(ZAP_PREFIX)) == 'DEBUG'
end

local function talos_build(source, record, ts)
  -- service.name travels as a record attribute: the OTLP output sends one
  -- resource per chunk, and VictoriaLogs stores both as the same fields.
  local attributes = { ['service.name'] = source }
  local body = (type(record.msg) == 'string' and record.msg or ''):gsub(ANSI, '')
  ts = rfc3339(record['talos-time']) or ts

  local payload = {}
  for k, v in pairs(record) do payload[k] = v end
  for _, k in ipairs(TALOS_KEYS) do payload[k] = nil end

  local level, severity
  if source == 'kernel' then
    level = payload.priority
    severity = KERNEL_SEVERITY[level]
    attributes['syslog.facility'] = payload.facility
    payload.priority, payload.facility = nil, nil
  else
    -- Talos records the time it read the line; a writer's timestamp at the
    -- start of the line is when it was written, which differs for lines
    -- delivered from Talos's buffer.
    local y, mo, d, h, mi, se, frac, token, rest = body:match(ZAP_PREFIX)
    local gy, gmo, gd, gh, gmi, gse, gfrac, grest = body:match(GO_PREFIX)
    if y and LEVELS[token] then
      ts, level, body = stamp(y, mo, d, h, mi, se, frac), token, rest
    elseif gy then
      ts, body = stamp(gy, gmo, gd, gh, gmi, gse, gfrac), grest
      -- grpc-go: `WARNING: [core] …`.
      token, rest = body:match('^(%u+): (.*)$')
      if token and LEVELS[token] then level, body = token, rest end
    else
      -- Plain text in the formats these services write: CNI plugins'
      -- `<time> [level]`, klog and logrus text. Talos keeps any JSON that
      -- followed the text as fields.
      y, mo, d, h, mi, se, frac, token, rest = body:match(BRACKET_PREFIX)
      local kl, thread, file, line, message = body:match(KLOG)
      if y then
        ts, level, body = stamp(y, mo, d, h, mi, se, frac), token, rest
      elseif kl then
        level, body = kl, message
        attributes['thread.id'], attributes['code.file.path'], attributes['code.line.number'] = tonumber(thread), file, tonumber(line)
        if message:sub(1, 1) == '"' then
          local kv = logfmt('msg=' .. message)
          body, kv.msg = kv.msg or message, nil
          for k, v in pairs(kv) do payload[k] = v end
        end
      elseif body:find(LOGRUS) then
        local kv = logfmt(body)
        ts = rfc3339(kv.time) or ts
        level, body = kv.level, present(kv.msg) or body
        kv.time, kv.level, kv.msg = nil, nil, nil
        for k, v in pairs(kv) do payload[k] = v end
      elseif TALOS_LEVELED[source] then
        level = record['talos-level']
      elseif source == 'kubelet' and payload.caller then
        -- Kubernetes JSON logs give info records a verbosity `v` and error
        -- records none; Talos has moved `err` into the message.
        severity = payload.v ~= nil and 9 or 17
      end
    end
    if payload.component == source then payload.component = nil end
    severity = severity or (level and LEVELS[tostring(level):upper()])
    if level and not severity then attributes['pipeline.severity_unmapped'] = true end
  end

  local encoded = {}
  payload = flatten(payload, nil, {}, source, encoded)
  location(payload, attributes)
  for key, value in pairs(payload) do attributes[source .. '.' .. key] = value end

  attributes.otlp = { severity_number = severity or 0, severity_text = level and tostring(level) or nil }
  return ts, attributes, body
end

function talos_normalize(tag, ts, group, metadata, record)
  local source = talos_source(record)
  if talos_dropped(source, type(record.msg) == 'string' and record.msg or '') then return -1, ts, metadata, record end
  local ok, new_ts, attributes, body = pcall(talos_build, source, record, ts)
  if not ok then
    attributes = { ['service.name'] = source, ['pipeline.normalize_error'] = tostring(new_ts), otlp = { severity_number = 0 } }
    body, new_ts = record.msg, ts
  end
  return 1, new_ts, attributes, { body = body, resource = { attributes = TALOS_RESOURCE } }
end
