-- Open a labeled review tab running an agent, then wait until the agent reports
-- itself ready (an idle or done program status) or gives up.
--
-- Args: label, cwd, command (program and arguments), timeout (seconds).
-- Prints "block <id>" once the tab exists. Exits 0 when the agent is ready,
-- 3 if it exits first, and 4 if it is not ready within the timeout.

local args = rex.args
local block

rex.on("terminal.program_status_changed", function(ctx, ev)
  if ctx.block_id ~= block or (ev.id or "") ~= "" or ev.record == nil then return end
  if ev.record.state == "idle" or ev.record.state == "done" then rex.stop(0) end
end)

rex.on("terminal.child_exited", function(ctx)
  if ctx.block_id == block then rex.stop(3) end
end)

local window, err = rex.session.new_window{
  window_label = args.label,
  layout = rex.layout.block{
    flavor = "com.superlogical.terminal.shell",
    options = {
      command = args.command,
      cwd = args.cwd,
      exit = { on_completion = false },
    },
  },
}
if err ~= nil then error(err, 0) end
block = window.block_ids[1]
print("block " .. block)
io.stdout:flush()

rex.after(args.timeout, function() rex.stop(4) end)
