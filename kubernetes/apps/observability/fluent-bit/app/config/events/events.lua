-- Maps Kubernetes Event objects from the kubernetes_events input onto the
-- OpenTelemetry log data model as the OpenTelemetry Collector's k8sevents
-- receiver does: message as body, type as severity, k8s.event.* and
-- k8s.object.* attributes. docs/evaluations/log-schema-and-collector.md
-- records each rule's source. The record keeps the input's timestamp.

-- The OTLP output sends one resource per chunk, so everything that varies per
-- event travels as a record attribute; VictoriaLogs stores both as fields.
local RESOURCE = { ['service.name'] = 'kubernetes-events', ['k8s.cluster.name'] = 'k8s-prod' }
local SEVERITY = { normal = 9, warning = 13, error = 17, critical = 21 }

local function text(v)
  if type(v) == 'string' and v ~= '' then return v end
end

function normalize(tag, ts, group, metadata, record)
  local object, meta, source = record.involvedObject or {}, record.metadata or {}, record.source or {}
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
  if type(record.count) == 'number' and record.count ~= 0 then attributes['k8s.event.count'] = record.count end

  local kind = text(record.type)
  local severity = kind and SEVERITY[kind:lower()]
  if kind and not severity then attributes['pipeline.severity_unmapped'] = true end
  attributes.otlp = { severity_number = severity or 0, severity_text = kind }
  return 2, ts, attributes, { body = text(record.message) or '', resource = { attributes = RESOURCE } }
end
