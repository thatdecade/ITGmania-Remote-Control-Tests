-- ScreenKiosk overlay
-- Minimal deterministic boot overlay for kiosk usage.

local function ScreenIsSupportedForSimplyLove()
	if type(StepManiaVersionIsSupported) == "function" and not StepManiaVersionIsSupported() then
		return false
	end
	if type(CurrentGameIsSupported) == "function" and not CurrentGameIsSupported() then
		return false
	end
	return true
end

local function InitializeThemeStateForKiosk()
	if type(InitializeSimplyLove) == "function" then
		InitializeSimplyLove()
	end

	-- Ensure P1 is joined and P2 is unjoined for deterministic single-side kiosk flow.
	if GAMESTATE:GetNumSidesJoined() == 0 then
		GAMESTATE:JoinPlayer(PLAYER_1)
	elseif not GAMESTATE:IsPlayerEnabled(PLAYER_1) then
		GAMESTATE:JoinPlayer(PLAYER_1)
	end

	if GAMESTATE:IsPlayerEnabled(PLAYER_2) then
		GAMESTATE:UnjoinPlayer(PLAYER_2)
	end

	-- ScreenSelectMusicCasual assumes a valid CurrentStyle exists.
	local currentGameName = GAMESTATE:GetCurrentGame():GetName()
	local styleName = "single"
	if currentGameName == "techno" then
		styleName = "single8"
	end

	GAMESTATE:SetCurrentStyle(styleName)

	-- Put Simply Love into Casual mode and apply its associated preferences.
	SL.Global.GameMode = "Casual"
	if type(SetGameModePreferences) == "function" then
		SetGameModePreferences()
	end
	THEME:ReloadMetrics()
end

local actorFrame = Def.ActorFrame{
	OnCommand=function(self)
		-- Run setup after a tiny delay to ensure the screen is fully constructed.
		self:sleep(0.01):queuecommand("KioskSetup")
	end,
	KioskSetupCommand=function(self)
		if not ScreenIsSupportedForSimplyLove() then
			return
		end
		InitializeThemeStateForKiosk()
	end,
}

-- Reuse ScreenInit compatibility checks.
actorFrame[#actorFrame+1] = LoadActor("../ScreenInit overlay/CompatibilityChecks.lua")

-- Solid black background.
actorFrame[#actorFrame+1] = Def.Quad{
	InitCommand=function(self)
		self:FullScreen():diffuse(0,0,0,1)
	end
}

return actorFrame
