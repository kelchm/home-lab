-- Maps a host's journal onto the OpenTelemetry log data model for VictoriaLogs'
-- OTLP endpoint, as the cluster's collector maps Talos and UniFi logs:
-- service.name, severity_number and severity_text, the message as the body,
-- and the few journal fields worth querying. host.name and service.namespace
-- are not set here: the ingest endpoint stamps them from the host's token.
-- Kept identical in proxmox/monitoring/logs and sparks/monitoring/logs.

-- Syslog severities as the OpenTelemetry Collector's syslog parser maps them.
local SYSLOG_SEVERITY = {
  [0] = { 21, 'emerg' }, [1] = { 19, 'alert' }, [2] = { 18, 'crit' }, [3] = { 17, 'err' },
  [4] = { 13, 'warning' }, [5] = { 10, 'notice' }, [6] = { 9, 'info' }, [7] = { 5, 'debug' },
}
-- RFC 5424 facility codes.
local FACILITIES = {
  [0] = 'kern', 'user', 'mail', 'daemon', 'auth', 'syslog', 'lpr', 'news', 'uucp', 'cron',
  'authpriv', 'ftp', 'ntp', 'security', 'console', 'solaris-cron',
  'local0', 'local1', 'local2', 'local3', 'local4', 'local5', 'local6', 'local7',
}

-- The systemd input gives a field the entry repeats as an array.
local function first(v)
  if type(v) == 'table' then return v[1] end
  return v
end

local function present(v)
  v = first(v)
  if v ~= nil and tostring(v) ~= '' then return tostring(v) end
  return nil
end

-- A repeated MESSAGE keeps every line. OTLP strings end at a NUL byte, so one
-- inside a binary message is written as \0.
local function message(v)
  if type(v) == 'table' then
    local parts = {}
    for i, x in ipairs(v) do parts[i] = tostring(x) end
    v = table.concat(parts, '\n')
  end
  return (present(v) or ''):gsub('%z', '\\0'):gsub('%s+$', '')
end

-- A container's name under Docker's journald driver, the kernel, a service's
-- unit without `.service`, else the program that wrote the line.
local function service_name(r)
  if present(r.CONTAINER_NAME) then return present(r.CONTAINER_NAME) end
  if present(r._TRANSPORT) == 'kernel' then return 'kernel' end
  local unit = present(r._SYSTEMD_UNIT)
  if unit and unit:sub(-8) == '.service' then return unit:sub(1, -9) end
  return present(r.SYSLOG_IDENTIFIER) or present(r._COMM) or unit or 'unknown_service'
end

local function build(r)
  local attributes = {
    ['service.name'] = service_name(r),
    ['systemd.unit'] = present(r._SYSTEMD_UNIT),
    ['process.pid'] = tonumber(present(r._PID)),
    ['process.executable.name'] = present(r._COMM),
    ['container.name'] = present(r.CONTAINER_NAME),
    ['container.id'] = present(r.CONTAINER_ID_FULL),
    ['container.image.name'] = present(r.IMAGE_NAME),
  }
  local severity, level
  local pri = tonumber(present(r.PRIORITY))
  -- Docker's journald driver writes stdout at info and stderr at err, which
  -- says which stream a line came from, not how severe it is.
  if attributes['container.name'] then
    attributes['log.iostream'] = pri == 3 and 'stderr' or 'stdout'
  elseif pri and SYSLOG_SEVERITY[pri] then
    severity, level = unpack(SYSLOG_SEVERITY[pri])
  elseif present(r.PRIORITY) then
    level = present(r.PRIORITY)
    attributes['pipeline.severity_unmapped'] = true
  end
  attributes['syslog.facility'] = FACILITIES[tonumber(present(r.SYSLOG_FACILITY))]
  attributes.otlp = { severity_number = severity or 0, severity_text = level }
  return attributes, message(r.MESSAGE)
end

-- A Lua error would leave the whole batch unnormalized, so a record the mapping
-- cannot handle keeps its raw message and is flagged instead.
function normalize(tag, ts, group, metadata, record)
  local ok, attributes, body = pcall(build, record)
  if not ok then
    attributes = { ['service.name'] = 'unknown_service', ['pipeline.normalize_error'] = tostring(attributes),
      otlp = { severity_number = 0 } }
    body = tostring(first(record.MESSAGE))
  end
  return 1, ts, attributes, { body = body, resource = { attributes = {} } }
end
