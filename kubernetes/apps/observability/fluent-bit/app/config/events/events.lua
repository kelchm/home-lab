-- Maps Kubernetes Event objects from the kubernetes_events input onto the
-- OpenTelemetry log data model as the OpenTelemetry Collector's k8sevents
-- receiver does: type as severity, k8s.event.* and k8s.object.* attributes.
-- The body takes the reason, object and message columns of `kubectl events`,
-- so a line-oriented view names what happened to which object; the receiver
-- stores the message alone. An event series (events.k8s.io reporters)
-- takes its count and time from `series`.
-- docs/evaluations/log-schema-and-collector.md records each rule's source.

-- The OTLP output sends one resource per chunk, so everything that varies per
-- event travels as a record attribute; VictoriaLogs stores both as fields.
local RESOURCE = { ['service.name'] = 'kubernetes-events', ['k8s.cluster.name'] = 'k8s-prod' }
local SEVERITY = { normal = 9, warning = 13, error = 17, critical = 21 }
local floor = math.floor

local function text(v)
  if type(v) == 'string' and v ~= '' then return v end
end

-- Days from 1970-01-01 to a civil date (Howard Hinnant's days_from_civil), so
-- the time needs no timezone.
local function days(y, m, d)
  if m <= 2 then y = y - 1 end
  local era = floor(y / 400)
  local yoe = y - era * 400
  local doy = floor((153 * ((m + 9) % 12) + 2) / 5) + d - 1
  return era * 146097 + yoe * 365 + floor(yoe / 4) - floor(yoe / 100) + doy - 719468
end

-- `{sec, nsec}` for the API server's UTC MicroTime, e.g. 2026-10-22T03:04:05.123456Z.
local function microtime(v)
  local y, mo, d, h, mi, s, frac = (text(v) or ''):match('^(%d%d%d%d)%-(%d%d)%-(%d%d)T(%d%d):(%d%d):(%d%d)%.?(%d*)Z$')
  if not y then return nil end
  return { sec = days(tonumber(y), tonumber(mo), tonumber(d)) * 86400 + tonumber(h) * 3600 + tonumber(mi) * 60 + tonumber(s),
    nsec = tonumber((frac .. '000000000'):sub(1, 9)) }
end

function normalize(tag, ts, group, metadata, record)
  local object, meta, source = record.involvedObject or {}, record.metadata or {}, record.source or {}
  local series = type(record.series) == 'table' and record.series or {}
  local attributes = {
    ['k8s.namespace.name'] = text(object.namespace),
    ['k8s.node.name'] = text(source.host),
    ['k8s.object.kind'] = text(object.kind),
    ['k8s.object.name'] = text(object.name),
    ['k8s.object.uid'] = text(object.uid),
    ['k8s.object.fieldpath'] = text(object.fieldPath),
    ['k8s.object.api_version'] = text(object.apiVersion),
    ['k8s.object.resource_version'] = text(object.resourceVersion),
    ['k8s.event.reason'] = text(record.reason),
    ['k8s.event.action'] = text(record.action),
    ['k8s.event.start_time'] = text(meta.creationTimestamp),
    ['k8s.event.name'] = text(meta.name),
    ['k8s.event.uid'] = text(meta.uid),
    -- Some reporters set only the older source.component.
    ['k8s.event.reporting_controller'] = text(record.reportingComponent) or text(source.component),
    ['k8s.event.reporting_instance'] = text(record.reportingInstance),
  }
  local count = series.count or record.count
  if type(count) == 'number' and count ~= 0 then attributes['k8s.event.count'] = count end

  local kind = text(record.type)
  local severity = kind and SEVERITY[kind:lower()]
  if kind and not severity then attributes['pipeline.severity_unmapped'] = true end
  attributes.otlp = { severity_number = severity or 0, severity_text = kind }
  local body = string.format('%s %s/%s: %s', text(record.reason) or '', text(object.kind) or '',
    text(object.name) or '', text(record.message) or '')
  local out = { body = body, resource = { attributes = RESOURCE } }
  -- The input dates a series by its creation; its last occurrence is when this write happened.
  local last = microtime(series.lastObservedTime)
  if last then return 1, last, attributes, out end
  return 2, ts, attributes, out
end
