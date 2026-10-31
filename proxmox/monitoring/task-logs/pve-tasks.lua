-- Maps a line of a Proxmox task log the way host.lua maps a journal entry, then
-- adds the task it belongs to. host.name is not set here, not even from the
-- UPID: the ingest endpoint stamps it from the host's token.
dofile('/fluent-bit/etc/host.lua')

-- Journal priorities of the levels Proxmox puts first on a line, after the
-- timestamp migrations start with. Words elsewhere in a line never set severity.
local PRIORITY = {
  ['TASK ERROR'] = 3, ['TASK WARNINGS'] = 4, ERROR = 3, WARN = 4, WARNING = 4, INFO = 6,
}

local function priority(line)
  if line == 'TASK OK' then return 6 end
  return PRIORITY[line:gsub('^%d%d%d%d%-%d%d%-%d%d %d%d:%d%d:%d%d ', ''):match('^([%u ]+):')]
end

function normalize_task(tag, ts, group, metadata, record)
  local line = tostring(record.log)
  local code, time, attributes, body = normalize(tag, ts, group, metadata,
    { MESSAGE = line, PRIORITY = priority(line), SYSLOG_IDENTIFIER = 'pve-task' })
  -- UPID:node:pid:pstart:starttime:type:id:user:
  local upid, kind, id = tostring(record.task_file):match('(UPID:[^:]*:%x+:%x+:%x+:([^:]*):([^:]*):.*)$')
  attributes['pve.task.upid'] = upid
  attributes['pve.task.type'] = kind
  attributes['pve.task.id'] = id
  return code, time, attributes, body
end
