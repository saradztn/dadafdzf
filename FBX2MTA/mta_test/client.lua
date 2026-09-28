-- Automatic MTA:SA DFF load test (FBX2MTA)
local ok, err = engineLoadDFF(1, "model.dff")
if ok then
    engineReplaceModel(206, "model.dff", "model.txd") -- 206 = adder (test slot)
    outputChatBox("FBX2MTA: DFF loaded + model replaced OK")
else
    outputChatBox("FBX2MTA: DFF LOAD FAILED: " .. tostring(err))
end
