"""
voice.py  —  services/evaluator-service/app/voice.py
------------------------------------------------------
ElevenLabs voice CMO briefing generator.

After every policy update, generates a spoken briefing like:
  "Alert on campaign cmp-001. ROAS dropped. Agent applied REALLOC BUDGET.
   Result: ROAS improved 54 percent. Reward score 1.2. Win rate now 58 percent."

Usage:
    from .voice import VoiceBriefing
    briefing = VoiceBriefing()
    await briefing.generate(ev, reward, bandit_update)
"""

from __future__ import annotations

import os
import asyncio
import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import httpx

from growthpilot_shared.logging import get_logger

log = get_logger("evaluator.voice")

ELEVENLABS_API_KEY  = os.getenv("ELEVENLABS_API_KEY", "")
ELEVENLABS_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM")  # Rachel — professional, calm
ELEVENLABS_MODEL    = os.getenv("ELEVENLABS_MODEL", "eleven_turbo_v2")           # fastest, lowest latency
BRIEFING_DIR        = Path(os.getenv("BRIEFING_DIR", "/tmp/briefings"))

ELEVENLABS_TTS_URL  = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"


@dataclass
class BriefingResult:
    text: str
    audio_path: Optional[Path]
    campaign_id: str
    action_type: str
    success: bool
    score_S: float
    generated_at: datetime


def _build_briefing_text(
    campaign_id: str,
    action_type: str,
    alert_type: str,
    score_S: float,
    success: bool,
    roas_before: float,
    roas_after: float,
    cpa_before: float,
    cpa_after: float,
    win_rate: float,
    alpha: int,
    beta: int,
    notes: str,
) -> str:
    """
    Build natural-sounding CMO briefing text.
    Designed to sound like an AI chief of staff giving a 20-second update.
    """
    # Clean up campaign ID for speech
    campaign_name = campaign_id.replace("_", " ").replace("cmp", "campaign")

    # Action type to natural language
    action_map = {
        "REALLOC_BUDGET": "reallocate budget",
        "PAUSE_CREATIVE": "pause underperforming creatives",
        "ADJUST_BID":     "adjust bid strategy",
        "REFRESH_COPY":   "refresh ad copy",
    }
    action_spoken = action_map.get(action_type, action_type.lower().replace("_", " "))

    # Alert type to natural language
    alert_map = {
        "ROAS_DROP":  "ROAS dropped below baseline",
        "CPA_SPIKE":  "cost per acquisition spiked",
        "CTR_DROP":   "click-through rate declined",
        "CVR_DROP":   "conversion rate dropped",
    }
    alert_spoken = alert_map.get(alert_type, "performance anomaly detected")

    # Compute deltas for speech
    roas_delta_pct = abs((roas_after - roas_before) / max(roas_before, 0.01)) * 100
    cpa_delta_pct  = abs((cpa_after  - cpa_before)  / max(cpa_before,  0.01)) * 100
    win_rate_pct   = round(win_rate * 100)

    # Result framing
    if success:
        result_line = (
            f"The action succeeded. "
            f"ROAS improved by {roas_delta_pct:.0f} percent, "
            f"and cost per acquisition improved by {cpa_delta_pct:.0f} percent. "
            f"Reward score: {score_S:.2f}."
        )
        outlook = (
            f"The agent is learning. "
            f"This action has a {win_rate_pct} percent win rate "
            f"across {alpha + beta - 2} trials, "
            f"and will be selected more frequently going forward."
        )
    else:
        result_line = (
            f"The action did not improve performance. "
            f"ROAS moved {roas_delta_pct:.0f} percent in the wrong direction. "
            f"Reward score: {score_S:.2f}."
        )
        outlook = (
            f"The agent is adjusting. "
            f"This action's win rate is {win_rate_pct} percent. "
            f"Thompson Sampling will reduce its selection probability for this campaign context."
        )

    text = (
        f"GrowthPilot agent briefing. "
        f"Alert on {campaign_name}: {alert_spoken}. "
        f"The agent autonomously chose to {action_spoken}. "
        f"{result_line} "
        f"{outlook}"
    )

    return text


class VoiceBriefing:
    """
    Generates ElevenLabs voice briefings after policy updates.
    Falls back silently if API key missing or quota exceeded.
    """

    def __init__(self):
        self._enabled = bool(ELEVENLABS_API_KEY)
        self._latest: Optional[BriefingResult] = None
        BRIEFING_DIR.mkdir(parents=True, exist_ok=True)

        if not self._enabled:
            log.warning("ElevenLabs API key not set — voice briefings disabled")
        else:
            log.info(
                "ElevenLabs voice briefing enabled",
                extra={"voice_id": ELEVENLABS_VOICE_ID, "model": ELEVENLABS_MODEL},
            )

    @property
    def latest(self) -> Optional[BriefingResult]:
        return self._latest

    async def generate(
        self,
        *,
        campaign_id: str,
        action_type: str,
        alert_type: str,
        score_S: float,
        success: bool,
        roas_before: float,
        roas_after: float,
        cpa_before: float,
        cpa_after: float,
        win_rate: float,
        alpha_after: int,
        beta_after: int,
        notes: str,
    ) -> Optional[BriefingResult]:
        """
        Generate a voice briefing. Returns BriefingResult or None if disabled/failed.
        Non-blocking — runs in background, never raises.
        """
        if not self._enabled:
            return None

        # Only generate for significant events (avoid spam)
        if abs(score_S) < 0.05:
            log.debug("Skipping voice briefing — score_S too small", extra={"score_S": score_S})
            return None

        try:
            text = _build_briefing_text(
                campaign_id=campaign_id,
                action_type=action_type,
                alert_type=alert_type,
                score_S=score_S,
                success=success,
                roas_before=roas_before,
                roas_after=roas_after,
                cpa_before=cpa_before,
                cpa_after=cpa_after,
                win_rate=win_rate,
                alpha=alpha_after,
                beta=beta_after,
                notes=notes,
            )

            log.info(
                "Generating voice briefing",
                extra={"campaign_id": campaign_id, "action_type": action_type, "text_len": len(text)},
            )

            audio_path = await self._call_elevenlabs(text, campaign_id, action_type)

            result = BriefingResult(
                text=text,
                audio_path=audio_path,
                campaign_id=campaign_id,
                action_type=action_type,
                success=success,
                score_S=score_S,
                generated_at=datetime.now(tz=timezone.utc),
            )
            self._latest = result

            log.info(
                "Voice briefing generated",
                extra={
                    "campaign_id": campaign_id,
                    "audio_path": str(audio_path) if audio_path else None,
                    "success": success,
                    "score_S": score_S,
                },
            )

            return result

        except Exception as e:
            log.warning("Voice briefing failed (non-fatal): %s", e)
            return None

    async def _call_elevenlabs(
        self, text: str, campaign_id: str, action_type: str
    ) -> Optional[Path]:
        """Call ElevenLabs TTS API and save MP3."""
        url = ELEVENLABS_TTS_URL.format(voice_id=ELEVENLABS_VOICE_ID)

        headers = {
            "xi-api-key": ELEVENLABS_API_KEY,
            "Content-Type": "application/json",
        }

        payload = {
            "text": text,
            "model_id": ELEVENLABS_MODEL,
            "voice_settings": {
                "stability": 0.5,
                "similarity_boost": 0.75,
                "style": 0.0,
                "use_speaker_boost": True,
            },
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, headers=headers, json=payload)

            if resp.status_code == 200:
                # Save as latest.mp3 (overwrite each time for easy demo access)
                latest_path = BRIEFING_DIR / "latest.mp3"
                latest_path.write_bytes(resp.content)

                # Also save timestamped copy
                ts = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%S")
                archive_path = BRIEFING_DIR / f"{ts}_{campaign_id}_{action_type}.mp3"
                archive_path.write_bytes(resp.content)

                return latest_path

            elif resp.status_code == 401:
                log.error("ElevenLabs: invalid API key")
            elif resp.status_code == 429:
                log.warning("ElevenLabs: rate limited / quota exceeded")
            else:
                log.warning(
                    "ElevenLabs: unexpected status %d: %s",
                    resp.status_code,
                    resp.text[:200],
                )

            return None