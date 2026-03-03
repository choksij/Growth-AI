from __future__ import annotations

import json
import os
from typing import Any, Dict, Optional

import httpx

from growthpilot_shared.logging import get_logger

log = get_logger("orchestrator.airia")

AIRIA_BASE_URL = "https://api.airia.ai"
AIRIA_PIPELINE_ID = "6e88e303-94bc-4467-9564-0dce434e462e"


class AiriaClient:
    """
    Client for the GrowthPilot-ai Airia agent.

    Calls the published pipeline via:
    POST /v2/PipelineExecution/{pipeline_id}
    X-API-KEY: <key>
    Body: {"userInput": "...", "asyncOutput": false}
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        pipeline_id: Optional[str] = None,
        base_url: Optional[str] = None,
    ):
        self.api_key = api_key or os.getenv("AIRIA_API_KEY", "")
        self.pipeline_id = pipeline_id or os.getenv("AIRIA_PIPELINE_ID", AIRIA_PIPELINE_ID)
        self.base_url = (base_url or os.getenv("AIRIA_BASE_URL", AIRIA_BASE_URL)).rstrip("/")

        if not self.api_key:
            log.warning("AIRIA_API_KEY not set — Airia calls will fail")

    @property
    def _url(self) -> str:
        return f"{self.base_url}/v2/PipelineExecution/{self.pipeline_id}"

    @property
    def _headers(self) -> Dict[str, str]:
        return {
            "X-API-KEY": self.api_key,
            "Content-Type": "application/json",
        }

    async def diagnose(self, alert_context: Dict[str, Any]) -> Dict[str, Any]:
        """
        Send alert context to the Airia diagnosis agent.
        Returns parsed JSON with: diagnosis, recommended_action, confidence, reasoning.
        Falls back gracefully if Airia is unavailable.
        """
        user_input = self._build_prompt(alert_context)

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    self._url,
                    headers=self._headers,
                    json={"userInput": user_input, "asyncOutput": False},
                )
                resp.raise_for_status()
                data = resp.json()

            # Airia returns the model output in "result" or "output" field
            raw_output = (
                data.get("result")
                or data.get("output")
                or data.get("response")
                or ""
            )

            log.info(
                "Airia diagnosis received",
                extra={"campaign_id": alert_context.get("campaign_id"), "raw": raw_output[:200]},
            )

            return self._parse_output(raw_output, alert_context)

        except httpx.HTTPStatusError as e:
            log.error("Airia HTTP error: %s %s", e.response.status_code, e.response.text[:200])
            return self._fallback_diagnosis(alert_context, reason=f"HTTP {e.response.status_code}")

        except Exception as e:
            log.error("Airia call failed: %s", e)
            return self._fallback_diagnosis(alert_context, reason=str(e))

    def _build_prompt(self, ctx: Dict[str, Any]) -> str:
        """Build a structured prompt from the alert context."""
        return (
            f"Campaign alert received:\n"
            f"- campaign_id: {ctx.get('campaign_id')}\n"
            f"- alert_type: {ctx.get('alert_type')}\n"
            f"- severity: {ctx.get('severity')}\n"
            f"- current ROAS: {ctx.get('current_roas', 'N/A')}\n"
            f"- baseline ROAS: {ctx.get('baseline_roas', 'N/A')}\n"
            f"- current CPA: {ctx.get('current_cpa', 'N/A')}\n"
            f"- baseline CPA: {ctx.get('baseline_cpa', 'N/A')}\n"
            f"- anomaly_score: {ctx.get('anomaly_score', 'N/A')}\n"
            f"- window: {ctx.get('window', '15m')}\n"
            f"\nDiagnose root cause and recommend exactly one action from: "
            f"PAUSE_CREATIVE, REALLOC_BUDGET, ADJUST_BID, REFRESH_COPY.\n"
            f"Respond in JSON only."
        )

    def _parse_output(self, raw: str, ctx: Dict[str, Any]) -> Dict[str, Any]:
        """Parse Airia agent output into structured dict."""
        # Strip markdown code fences if present
        text = raw.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:-1]) if len(lines) > 2 else text

        try:
            parsed = json.loads(text)

            # Handle nested diagnosis object e.g. {"diagnosis": {"primary_issue": ...}}
            diagnosis_raw = parsed.get("diagnosis", "Unknown")
            if isinstance(diagnosis_raw, dict):
                diagnosis_str = (
                    diagnosis_raw.get("primary_issue")
                    or diagnosis_raw.get("root_cause")
                    or diagnosis_raw.get("summary")
                    or str(diagnosis_raw)
                )
            else:
                diagnosis_str = str(diagnosis_raw)

            # Handle nested recommended_action
            action_raw = parsed.get("recommended_action", "")
            if isinstance(action_raw, dict):
                action_raw = (
                    action_raw.get("action")
                    or action_raw.get("type")
                    or action_raw.get("name")
                    or ""
                )
            action = str(action_raw).upper().strip()

            valid_actions = {"PAUSE_CREATIVE", "REALLOC_BUDGET", "ADJUST_BID", "REFRESH_COPY"}

            # Search entire response for a valid action if top-level missing
            if action not in valid_actions:
                raw_upper = raw.upper()
                for va in valid_actions:
                    if va in raw_upper:
                        action = va
                        log.info("Extracted action '%s' from raw Airia response", action)
                        break

            if action not in valid_actions:
                log.warning("Airia returned invalid action '%s', using fallback", action)
                return self._fallback_diagnosis(ctx, reason=f"invalid action: {action}")

            # Handle nested reasoning
            reasoning_raw = parsed.get("reasoning", parsed.get("explanation", ""))
            if isinstance(reasoning_raw, dict):
                reasoning_str = reasoning_raw.get("summary") or str(reasoning_raw)
            else:
                reasoning_str = str(reasoning_raw)

            return {
                "diagnosis": diagnosis_str,
                "recommended_action": action,
                "confidence": float(parsed.get("confidence", 0.7)),
                "reasoning": reasoning_str,
                "source": "airia",
            }
        except (json.JSONDecodeError, ValueError) as e:
            log.warning("Could not parse Airia output: %s | raw: %s", e, raw[:200])
            return self._fallback_diagnosis(ctx, reason=f"parse error: {e}")

    def _fallback_diagnosis(
        self, ctx: Dict[str, Any], reason: str = "unavailable"
    ) -> Dict[str, Any]:
        """
        Fallback diagnosis when Airia is unavailable.
        Uses simple rule-based logic so the pipeline never stalls.
        """
        alert_type = ctx.get("alert_type", "")

        if alert_type == "ROAS_DROP":
            action = "REALLOC_BUDGET"
            diagnosis = "ROAS dropped below baseline — likely spend inefficiency or creative fatigue"
        elif alert_type == "CPA_SPIKE":
            action = "ADJUST_BID"
            diagnosis = "CPA spiked above baseline — likely overbidding or conversion drop"
        else:
            action = "REALLOC_BUDGET"
            diagnosis = "Anomaly detected — reallocating budget as default recovery action"

        log.info("Using fallback diagnosis (Airia: %s)", reason)

        return {
            "diagnosis": diagnosis,
            "recommended_action": action,
            "confidence": 0.6,
            "reasoning": f"Rule-based fallback (Airia {reason})",
            "source": "fallback",
        }