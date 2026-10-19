-- Maps UniFi syslog onto the OpenTelemetry log data model for VictoriaLogs'
-- OTLP endpoint: one service.*/host.* resource per device, severity_number and
-- severity_text, a readable body, and CEF fields under their parser-native
-- cef.* names. docs/evaluations/log-schema-and-collector.md records each
-- rule's source; the unifi_syslog parser has already split off the priority
-- and the sender's hostname.

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
local CEF_HEADER = { 'version', 'device_vendor', 'device_product', 'device_version',
  'device_event_class_id', 'name', 'severity' }

local function present(v)
  if v ~= nil and tostring(v) ~= '' then return v end
end

-- CEF severity 0–3/4–6/7–8/9–10 → INFO/WARN/ERROR/FATAL, as Bindplane's
-- collector maps it.
local function cef_severity(s)
  local n = tonumber(s)
  if not n or n % 1 ~= 0 or n < 0 or n > 10 then return nil end
  return n <= 3 and 9 or n <= 6 and 13 or n <= 8 and 17 or 21
end

-- CEF:Version|Vendor|Product|Version|Class|Name|Severity|Extension. The header
-- escapes \| and \\; extension values escape \=, \\, \n and \r, and each key
-- starts after a space.
local function cef(s, out)
  local fields, buf, i = {}, {}, 5
  while i <= #s and #fields < #CEF_HEADER do
    local c = s:sub(i, i)
    if c == '\\' and (s:sub(i + 1, i + 1) == '|' or s:sub(i + 1, i + 1) == '\\') then
      buf[#buf + 1] = s:sub(i + 1, i + 1)
      i = i + 2
    elseif c == '|' then
      fields[#fields + 1] = table.concat(buf)
      buf = {}
      i = i + 1
    else
      buf[#buf + 1] = c
      i = i + 1
    end
  end
  if #fields < #CEF_HEADER then return false end
  for n, key in ipairs(CEF_HEADER) do out['cef.' .. key] = fields[n] end

  local ext, keys, pos = s:sub(i), {}, 1
  while true do
    local first, last, key = ext:find('([%w_.]+)=', pos)
    if not first then break end
    if first == 1 or ext:sub(first - 1, first - 1) == ' ' then keys[#keys + 1] = { first, last, key } end
    pos = last + 1
  end
  for n, k in ipairs(keys) do
    local stop = keys[n + 1] and keys[n + 1][1] - 2 or #ext
    out['cef.extension.' .. k[3]] = ext:sub(k[2] + 1, stop):gsub('\\(.)', function(c)
      return ({ n = '\n', r = '\r' })[c] or c
    end)
  end
  return true
end

-- UNIFIutcTime as a {sec, nsec} timestamp (the pod runs with TZ=UTC).
local function utc(s)
  local y, mo, d, h, mi, se, frac = tostring(s):match('^(%d+)%-(%d+)%-(%d+)T(%d+):(%d+):(%d+)%.?(%d*)Z$')
  if not y then return nil end
  return { sec = os.time { year = tonumber(y), month = tonumber(mo), day = tonumber(d),
    hour = tonumber(h), min = tonumber(mi), sec = tonumber(se) }, nsec = tonumber((frac .. '000000000'):sub(1, 9)) }
end

local function build(record, ts)
  local message = (record.message or ''):gsub('%s+$', '')
  local host = present(record.host)
  local address, port = tostring(record.source_address or ''):match('^%a+://(.+):(%d+)$')
  local resource = { ['service.namespace'] = 'unifi', ['host.name'] = host,
    ['service.name'] = host or address or 'unknown_service' }
  local attributes = { ['network.peer.address'] = address, ['network.peer.port'] = tonumber(port) }
  local severity, level

  local pri = tonumber(record.pri)
  if pri then
    if pri <= 191 then
      attributes['syslog.facility'] = FACILITIES[math.floor(pri / 8)]
      severity, level = unpack(SYSLOG_SEVERITY[pri % 8])
    else
      level = record.pri
      attributes['pipeline.severity_unmapped'] = true
    end
  end

  -- Adopted devices tag each line `<mac>,<model>-<firmware>:`; the gateway
  -- repeats its hostname instead.
  local mac, model, firmware, rest = message:match('^(%x+),([^%s:]+)%-(%d[^%s:%-]*): (.*)$')
  if mac and #mac == 12 then
    resource['host.id'], resource['host.type'], resource['os.version'] = mac, model, firmware
    message = rest
  elseif host and message:sub(1, #host + 1) == host .. ' ' then
    message = message:sub(#host + 2)
  end

  local body = message
  if message:sub(1, 4) == 'CEF:' and cef(message, attributes) then
    body = present(attributes['cef.extension.msg']) or present(attributes['cef.name']) or message
    level = attributes['cef.severity']
    severity = cef_severity(level)
    attributes['pipeline.severity_unmapped'] = not severity or nil
    ts = utc(attributes['cef.extension.UNIFIutcTime']) or ts
  end

  attributes.otlp = { severity_number = severity or 0, severity_text = level and tostring(level) or nil }
  return ts, attributes, body, resource
end

-- A Lua error would leave the whole batch unnormalized, so a record the mapping
-- cannot handle keeps its sender and raw message and is flagged instead.
function normalize(tag, ts, group, metadata, record)
  local ok, new_ts, attributes, body, resource = pcall(build, record, ts)
  if not ok then
    local name = present(record.host) or tostring(record.source_address)
    attributes = { ['pipeline.normalize_error'] = tostring(new_ts), otlp = { severity_number = 0 } }
    body, resource, new_ts = record.message, { ['service.namespace'] = 'unifi', ['service.name'] = name }, ts
  end
  return 1, new_ts, attributes, { body = body, resource = { attributes = resource } }
end
