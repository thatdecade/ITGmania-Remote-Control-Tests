-- RemoteWsHarness.lua
-- WebSocket remote-control harness for ITGmania.
-- Binary framing: uint16_be size | uint8 command | payload
-- Responses: JSON as null-terminated UTF-8.
-- Sends text HEARTBEAT and SCREEN for liveness.

local moduleTable = {}

local websocketUrl = "ws://127.0.0.1:8765"
local globalStateKey = "ITG_REMOTE_WS_HARNESS_STATE"

-- Command IDs (Python -> ITG)
local CMD_HELLO      = 0x01
local CMD_GET_STATUS = 0x10
local CMD_GET_GROUPS = 0x11
local CMD_GET_SONGS  = 0x12
local CMD_START_SONG = 0x20
local CMD_PAUSE      = 0x21
local CMD_STOP       = 0x22

-- Response IDs (ITG -> Python)
local RSP_HELLO      = 0x81
local RSP_GET_STATUS = 0x90
local RSP_GET_GROUPS = 0x91
local RSP_GET_SONGS  = 0x92
local RSP_START_SONG = 0xA0
local RSP_PAUSE      = 0xA1
local RSP_STOP       = 0xA2
local RSP_ERROR      = 0xFF

local function getState()
	if _G[globalStateKey] == nil then
		_G[globalStateKey] = {
			ws = nil,
			connected = false,
			rxBuffer = "",
			lastHeartbeatUptime = -1,
			lastScreen = "",
			pausedKnown = false,
			pausedValue = nil
		}
	end
	return _G[globalStateKey]
end

local function uint16_be_to_number(b1, b2)
	return b1 * 256 + b2
end

local function number_to_uint16_be(value)
	local high = math.floor(value / 256) % 256
	local low = value % 256
	return string.char(high, low)
end

local function encode_nt_string(text)
	return tostring(text or "") .. "\0"
end

local function decode_nt_string(buffer, startIndex)
	local index = startIndex
	local bufferLength = #buffer
	while index <= bufferLength do
		if string.byte(buffer, index) == 0 then
			local value = string.sub(buffer, startIndex, index - 1)
			return value, index + 1
		end
		index = index + 1
	end
	return nil, startIndex
end

local function json_escape_string(value)
	local s = tostring(value or "")
	s = s:gsub("\\", "\\\\")
	s = s:gsub("\"", "\\\"")
	s = s:gsub("\n", "\\n")
	s = s:gsub("\r", "\\r")
	s = s:gsub("\t", "\\t")
	return s
end

local function json_encode(value)
	local valueType = type(value)

	if valueType == "nil" then
		return "null"
	end
	if valueType == "boolean" then
		return value and "true" or "false"
	end
	if valueType == "number" then
		if value ~= value then
			return "null"
		end
		if value == math.huge or value == -math.huge then
			return "null"
		end
		return tostring(value)
	end
	if valueType == "string" then
		return "\"" .. json_escape_string(value) .. "\""
	end
	if valueType == "table" then
		local isArray = true
		local maxIndex = 0
		for key, _ in pairs(value) do
			if type(key) ~= "number" then
				isArray = false
				break
			end
			if key > maxIndex then
				maxIndex = key
			end
		end

		if isArray then
			local parts = {}
			for i = 1, maxIndex do
				parts[#parts + 1] = json_encode(value[i])
			end
			return "[" .. table.concat(parts, ",") .. "]"
		end

		local parts = {}
		for key, innerValue in pairs(value) do
			parts[#parts + 1] = "\"" .. json_escape_string(key) .. "\":" .. json_encode(innerValue)
		end
		return "{" .. table.concat(parts, ",") .. "}"
	end

	return "\"unsupported\""
end

local function build_packet(commandByte, payloadString)
	local payload = payloadString or ""
	local size = 1 + #payload
	return number_to_uint16_be(size) .. string.char(commandByte) .. payload
end

local function sendJsonResponse(responseCommand, tableValue)
	local state = getState()
	if state.ws == nil or not state.connected then
		return
	end
	local jsonText = json_encode(tableValue)
	local packet = build_packet(responseCommand, encode_nt_string(jsonText))
	state.ws:Send(packet, true)
end

local function sendTextInfo(textLine)
	local state = getState()
	if state.ws == nil or not state.connected then
		return
	end
	state.ws:Send(textLine, false)
end

local function getTopScreenName()
	local topScreen = SCREENMAN:GetTopScreen()
	if topScreen == nil then
		return "NoTopScreen"
	end
	local name = topScreen:GetName()
	if name == nil then
		return "UnknownScreen"
	end
	return name
end

local function isSelectMusicScreen(screenName)
	return screenName == "ScreenSelectMusic" or screenName == "ScreenSelectMusicCasual"
end

local function isGameplayScreen(screenName)
	return string.find(screenName or "", "^ScreenGameplay") ~= nil
end

local function getCapabilities()
	local topScreen = SCREENMAN:GetTopScreen()
	return {
		top_screen_has_pausegame = (topScreen ~= nil and topScreen.PauseGame ~= nil) and true or false,
		screenman_has_pausegame = (SCREENMAN ~= nil and SCREENMAN.PauseGame ~= nil) and true or false,
		screenman_has_setpaused = (SCREENMAN ~= nil and SCREENMAN.SetPaused ~= nil) and true or false,
		gamestate_has_setpaused = (GAMESTATE ~= nil and GAMESTATE.SetPaused ~= nil) and true or false,
		screenman_has_ispaused = (SCREENMAN ~= nil and SCREENMAN.IsPaused ~= nil) and true or false,
		top_screen_has_ispaused = (topScreen ~= nil and topScreen.IsPaused ~= nil) and true or false
	}
end

local function refreshPausedState()
	local state = getState()
	state.pausedKnown = false
	state.pausedValue = nil

	if SCREENMAN ~= nil and SCREENMAN.IsPaused ~= nil then
		state.pausedKnown = true
		state.pausedValue = SCREENMAN:IsPaused()
		return
	end

	local topScreen = SCREENMAN:GetTopScreen()
	if topScreen ~= nil and topScreen.IsPaused ~= nil then
		state.pausedKnown = true
		state.pausedValue = topScreen:IsPaused()
		return
	end
end

local function safe_call_number(fn)
	local ok, value = pcall(fn)
	if ok and type(value) == "number" then
		return value
	end
	return nil
end

local function safe_call_bool(fn)
	local ok, value = pcall(fn)
	if ok and type(value) == "boolean" then
		return value
	end
	return nil
end

local function safe_call_string(fn)
	local ok, value = pcall(fn)
	if ok and value ~= nil then
		return tostring(value)
	end
	return nil
end

local function collectJudgments(playerStageStats)
	local result = {}

	if playerStageStats == nil then
		return result
	end

	if playerStageStats.GetTapNoteScores ~= nil then
		local tapKeys = {
			"TapNoteScore_W1",
			"TapNoteScore_W2",
			"TapNoteScore_W3",
			"TapNoteScore_W4",
			"TapNoteScore_W5",
			"TapNoteScore_Miss"
		}
		for _, key in ipairs(tapKeys) do
			local value = safe_call_number(function()
				return playerStageStats:GetTapNoteScores(key)
			end)
			if value ~= nil then
				result[key] = value
			end
		end
	end

	if playerStageStats.GetHoldNoteScores ~= nil then
		local holdKeys = {
			"HoldNoteScore_Held",
			"HoldNoteScore_LetGo",
			"HoldNoteScore_MissedHold"
		}
		for _, key in ipairs(holdKeys) do
			local value = safe_call_number(function()
				return playerStageStats:GetHoldNoteScores(key)
			end)
			if value ~= nil then
				result[key] = value
			end
		end
	end

	return result
end

local function getStatusTable()
	local state = getState()
	refreshPausedState()

	local screenName = getTopScreenName()
	local currentSong = GAMESTATE:GetCurrentSong()
	local currentStepsP1 = GAMESTATE:GetCurrentSteps(PLAYER_1)

	local status = {
		screen = screenName,
		is_playing = isGameplayScreen(screenName),
		paused_known = state.pausedKnown,
		paused = state.pausedValue,
		uptime_seconds = GetTimeSinceStart(),
		current_song_dir = currentSong and currentSong:GetSongDir() or "",
		current_group = currentSong and currentSong:GetGroupName() or "",
		current_title = currentSong and currentSong:GetDisplayFullTitle() or "",
		current_difficulty_p1 = currentStepsP1 and tostring(currentStepsP1:GetDifficulty()) or "",
		capabilities = getCapabilities()
	}

	-- Life / health (best effort, varies by build)
	local playerState = GAMESTATE:GetPlayerState(PLAYER_1)
	if playerState ~= nil then
		if playerState.GetHealthState ~= nil then
			status.health_state_p1 = safe_call_string(function()
				return playerState:GetHealthState()
			end)
		end
		if playerState.GetLifeMeter ~= nil then
			local lifeMeter = safe_call_string(function()
				return playerState:GetLifeMeter()
			end)
			if lifeMeter ~= nil then
				status.life_meter_p1 = lifeMeter
			end
		end
	end

	local stageStats = STATSMAN:GetCurStageStats()
	if stageStats ~= nil then
		local p1Stats = stageStats:GetPlayerStageStats(PLAYER_1)
		if p1Stats ~= nil then
			status.score_p1 = safe_call_number(function() return p1Stats:GetScore() end) or 0
			status.percent_dp_p1 = safe_call_number(function() return p1Stats:GetPercentDancePoints() end) or 0
			status.current_combo_p1 = safe_call_number(function() return p1Stats:GetCurrentCombo() end) or 0
			status.failed_p1 = safe_call_bool(function() return p1Stats:GetFailed() end) or false
			status.grade_p1 = safe_call_string(function() return p1Stats:GetGrade() end) or ""

			status.judgments_p1 = collectJudgments(p1Stats)
		end
	end

	return status
end

local function getGroupNames()
	local groups = SONGMAN:GetSongGroupNames() or {}
	table.sort(groups)
	return groups
end

local function getPreferredStepsType()
	local style = GAMESTATE:GetCurrentStyle()
	if style ~= nil and style.GetStepsType ~= nil then
		return style:GetStepsType()
	end
	if StepsType_dance_single ~= nil then
		return StepsType_dance_single
	end
	return nil
end

local function collectDifficulties(song, stepsType)
	local difficulties = {}
	local seen = {}

	local function addDifficulty(difficultyValue)
		local difficultyString = tostring(difficultyValue or "")
		if difficultyString ~= "" and not seen[difficultyString] then
			seen[difficultyString] = true
			difficulties[#difficulties + 1] = difficultyString
		end
	end

	if stepsType ~= nil then
		local okSteps, stepsList = pcall(function()
			return song:GetStepsByStepsType(stepsType) or {}
		end)
		if okSteps and stepsList ~= nil then
			for _, steps in ipairs(stepsList) do
				if steps ~= nil and steps.GetDifficulty ~= nil then
					addDifficulty(steps:GetDifficulty())
				end
			end
		end
	end

	if #difficulties == 0 then
		local okAll, allSteps = pcall(function()
			return song:GetAllSteps() or {}
		end)
		if okAll and allSteps ~= nil then
			for _, steps in ipairs(allSteps) do
				if steps ~= nil and steps.GetDifficulty ~= nil then
					addDifficulty(steps:GetDifficulty())
				end
			end
		end
	end

	table.sort(difficulties)
	return difficulties
end

local function collectSongs(maxCount, groupName)
	local stepsType = getPreferredStepsType()
	local results = {}

	local songs = nil
	if groupName ~= nil and groupName ~= "" and SONGMAN:DoesSongGroupExist(groupName) then
		songs = SONGMAN:GetSongsInGroup(groupName) or {}
	else
		songs = SONGMAN:GetAllSongs() or {}
	end

	local count = 0
	for _, song in ipairs(songs) do
		count = count + 1
		if count > maxCount then
			break
		end

		results[#results + 1] = {
			song_dir = song:GetSongDir(),
			group = song:GetGroupName(),
			title = song:GetDisplayFullTitle(),
			difficulties = collectDifficulties(song, stepsType)
		}
	end

	return results
end

local function findSongByDir(songDir)
	if songDir == nil or songDir == "" then
		return nil
	end
	local allSongs = SONGMAN:GetAllSongs() or {}
	for _, song in ipairs(allSongs) do
		if song:GetSongDir() == songDir then
			return song
		end
	end
	return nil
end

local function normalizeDifficultyString(difficultyString)
	if difficultyString == nil then
		return ""
	end
	if string.sub(difficultyString, 1, 11) ~= "Difficulty_" then
		return "Difficulty_" .. difficultyString
	end
	return difficultyString
end

local function findSteps(song, difficultyString)
	if song == nil then
		return nil
	end

	local normalized = normalizeDifficultyString(difficultyString)
	local stepsType = getPreferredStepsType()

	if stepsType ~= nil then
		local okSteps, stepsList = pcall(function()
			return song:GetStepsByStepsType(stepsType) or {}
		end)
		if okSteps and stepsList ~= nil then
			for _, steps in ipairs(stepsList) do
				if steps ~= nil and steps.GetDifficulty ~= nil then
					if tostring(steps:GetDifficulty()) == normalized then
						return steps
					end
				end
			end
		end
	end

	local okAll, allSteps = pcall(function()
		return song:GetAllSteps() or {}
	end)
	if okAll and allSteps ~= nil then
		for _, steps in ipairs(allSteps) do
			if steps ~= nil and steps.GetDifficulty ~= nil then
				if tostring(steps:GetDifficulty()) == normalized then
					return steps
				end
			end
		end
	end

	return nil
end

local function isP1Enabled()
	local enabledPlayers = GAMESTATE:GetEnabledPlayers() or {}
	for _, player in ipairs(enabledPlayers) do
		if player == PLAYER_1 then
			return true
		end
	end
	return false
end

local function startSong(songDir, difficultyString)
	local topScreen = SCREENMAN:GetTopScreen()
	if topScreen == nil then
		return false, "no_top_screen"
	end

	local screenName = topScreen:GetName() or ""
	if not isSelectMusicScreen(screenName) then
		return false, "must_be_on_select_music_screen"
	end

	if not isP1Enabled() then
		return false, "p1_not_joined"
	end

	local song = findSongByDir(songDir)
	if song == nil then
		return false, "song_not_found"
	end

	local steps = findSteps(song, difficultyString)
	if steps == nil then
		return false, "steps_not_found"
	end

	GAMESTATE:SetCurrentSong(song)
	GAMESTATE:SetPreferredSong(song)
	GAMESTATE:SetCurrentSteps(PLAYER_1, steps)
	GAMESTATE:SetPreferredDifficulty(PLAYER_1, steps:GetDifficulty())

	if topScreen.StartTransitioningScreen ~= nil then
		topScreen:StartTransitioningScreen("SM_GoToNextScreen")
		return true, "ok"
	end

	return false, "no_transition_api"
end

local function setPause(pauseValue)
	local topScreen = SCREENMAN:GetTopScreen()
	if topScreen ~= nil and topScreen.PauseGame ~= nil then
		topScreen:PauseGame(pauseValue and true or false)
		return true, "ok_top_screen"
	end

	if SCREENMAN ~= nil and SCREENMAN.PauseGame ~= nil then
		SCREENMAN:PauseGame(pauseValue and true or false)
		return true, "ok_screenman"
	end

	if SCREENMAN ~= nil and SCREENMAN.SetPaused ~= nil then
		SCREENMAN:SetPaused(pauseValue and true or false)
		return true, "ok_setpaused"
	end

	if GAMESTATE ~= nil and GAMESTATE.SetPaused ~= nil then
		GAMESTATE:SetPaused(pauseValue and true or false)
		return true, "ok_gamestate"
	end

	return false, "no_pause_api"
end

local function stopGame()
	local topScreen = SCREENMAN:GetTopScreen()
	if topScreen == nil then
		return false, "no_top_screen"
	end
	if topScreen.Cancel ~= nil then
		topScreen:Cancel()
		return true, "ok"
	end
	return false, "no_stop_api"
end

local function handleCommand(commandByte, payload)
	if commandByte == CMD_HELLO then
		sendJsonResponse(RSP_HELLO, {
			ok = true,
			server = "itgmania",
			uptime_seconds = GetTimeSinceStart(),
			capabilities = getCapabilities()
		})
		return
	end

	if commandByte == CMD_GET_STATUS then
		sendJsonResponse(RSP_GET_STATUS, { ok = true, status = getStatusTable() })
		return
	end

	if commandByte == CMD_GET_GROUPS then
		sendJsonResponse(RSP_GET_GROUPS, { ok = true, groups = getGroupNames() })
		return
	end

	if commandByte == CMD_GET_SONGS then
		local maxCount = 50
		local groupName = ""

		if #payload >= 2 then
			maxCount = uint16_be_to_number(string.byte(payload, 1), string.byte(payload, 2))
			if maxCount < 1 then maxCount = 1 end
			if maxCount > 500 then maxCount = 500 end
			local decodedGroup, _ = decode_nt_string(payload, 3)
			if decodedGroup ~= nil then
				groupName = decodedGroup
			end
		end

		local songs = collectSongs(maxCount, groupName)
		sendJsonResponse(RSP_GET_SONGS, {
			ok = true,
			group = groupName,
			count = #songs,
			songs = songs
		})
		return
	end

	if commandByte == CMD_START_SONG then
		local songDir, nextIndex = decode_nt_string(payload, 1)
		local difficultyString, _ = decode_nt_string(payload, nextIndex)

		if songDir == nil or difficultyString == nil then
			sendJsonResponse(RSP_START_SONG, { ok = false, reason = "bad_payload" })
			return
		end

		local success, reason = startSong(songDir, difficultyString)
		sendJsonResponse(RSP_START_SONG, {
			ok = success,
			reason = reason,
			song_dir = songDir,
			difficulty = difficultyString
		})
		return
	end

	if commandByte == CMD_PAUSE then
		local pauseValue = 0
		if #payload >= 1 then
			pauseValue = string.byte(payload, 1)
		end

		local success, reason = setPause(pauseValue ~= 0)
		refreshPausedState()
		local state = getState()
		sendJsonResponse(RSP_PAUSE, {
			ok = success,
			reason = reason,
			paused_known = state.pausedKnown,
			paused = state.pausedValue,
			capabilities = getCapabilities()
		})
		return
	end

	if commandByte == CMD_STOP then
		local success, reason = stopGame()
		sendJsonResponse(RSP_STOP, { ok = success, reason = reason })
		return
	end

	sendJsonResponse(RSP_ERROR, { ok = false, reason = "unknown_command", command = commandByte })
end

local function processIncomingBinary(binaryData)
	local state = getState()
	state.rxBuffer = state.rxBuffer .. (binaryData or "")

	while true do
		if #state.rxBuffer < 3 then
			return
		end

		local sizeHigh = string.byte(state.rxBuffer, 1)
		local sizeLow = string.byte(state.rxBuffer, 2)
		local size = uint16_be_to_number(sizeHigh, sizeLow)

		local totalPacketSize = 2 + size
		if #state.rxBuffer < totalPacketSize then
			return
		end

		local commandByte = string.byte(state.rxBuffer, 3)
		local payload = ""
		if size > 1 then
			payload = string.sub(state.rxBuffer, 4, totalPacketSize)
		end

		state.rxBuffer = string.sub(state.rxBuffer, totalPacketSize + 1)

		local success, errorMessage = pcall(function()
			handleCommand(commandByte, payload)
		end)
		if not success then
			sendJsonResponse(RSP_ERROR, { ok = false, reason = "lua_error", command = commandByte, detail = tostring(errorMessage) })
		end
	end
end

local function connectIfNeeded()
	local state = getState()
	if state.ws ~= nil then
		return
	end

	state.ws = NETWORK:WebSocket{
		url = websocketUrl,
		pingInterval = 15,
		automaticReconnect = true,
		onMessage = function(message)
			local messageType = tostring(message.type or "")

			if messageType == "WebSocketMessageType_Open" then
				state.connected = true
				sendTextInfo("HEARTBEAT|uptime_seconds=" .. string.format("%.3f", GetTimeSinceStart()) .. "|screen=" .. getTopScreenName())
				return
			end

			if messageType == "WebSocketMessageType_Close" or messageType == "WebSocketMessageType_Error" then
				state.connected = false
				return
			end

			if messageType ~= "WebSocketMessageType_Message" then
				return
			end

			if message.binary then
				processIncomingBinary(message.data or "")
			end
		end
	}
end

local function updateHeartbeatAndScreen()
	local state = getState()
	if not state.connected then
		return
	end

	local nowUptime = GetTimeSinceStart()
	local screenName = getTopScreenName()

	if screenName ~= state.lastScreen then
		state.lastScreen = screenName
		sendTextInfo("SCREEN|" .. screenName .. "|uptime_seconds=" .. string.format("%.3f", nowUptime))
	end

	if state.lastHeartbeatUptime < 0 or (nowUptime - state.lastHeartbeatUptime) >= 2.0 then
		state.lastHeartbeatUptime = nowUptime
		sendTextInfo("HEARTBEAT|uptime_seconds=" .. string.format("%.3f", nowUptime) .. "|screen=" .. screenName)
	end
end

local function makeActor()
	return Def.ActorFrame{
		ModuleCommand = function(self)
			connectIfNeeded()
			self:queuecommand("HarnessUpdate")
		end,
		HarnessUpdateCommand = function(self)
			connectIfNeeded()
			updateHeartbeatAndScreen()
			self:sleep(0.1)
			self:queuecommand("HarnessUpdate")
		end
	}
end

local actor = makeActor()
moduleTable.ScreenTitleMenu = actor
moduleTable.ScreenSelectMusic = actor
moduleTable.ScreenSelectMusicCasual = actor
moduleTable.ScreenGameplay = actor
moduleTable.ScreenEvaluationStage = actor
moduleTable.ScreenEvaluationSummary = actor

return moduleTable
