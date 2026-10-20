-- Paste a prompt into the agent in the target block (rex do -b BLOCK) as one
-- bracketed paste, press Return, and optionally wait for the turn to end.
--
-- Args: text, wait (boolean).
-- With wait, the turn is over when the status goes working or blocked and then
-- settles on idle or done; handlers are registered before the paste, so no
-- change is missed. Exits 0 when the turn ends, 2 when the agent reports an
-- error (printing "error <message>"), and 3 if it exits mid-turn.

local args = rex.args
local block = rex.block_id
local started = false

if args.wait then
  rex.on("terminal.program_status_changed", function(ctx, ev)
    if ctx.block_id ~= block or (ev.id or "") ~= "" or ev.record == nil then return end
    local state = ev.record.state
    if state == "error" then
      print("error " .. tostring(ev.record.msg or ""))
      io.stdout:flush()
      rex.stop(2)
    elseif state == "working" or state == "blocked" then
      started = true
    elseif (state == "idle" or state == "done") and started then
      rex.stop(0)
    end
  end)

  rex.on("terminal.child_exited", function(ctx)
    if ctx.block_id == block then rex.stop(3) end
  end)
end

local function send(event)
  local _, err = rex.call("block.event", {
    session_id = rex.session_id, block_id = block, event = event,
  })
  if err ~= nil then error(err, 0) end
end

send{ type = "paste", payload = { text = args.text } }
rex.sleep(0.3)
send{ type = "key", payload = { key = "enter" } }
