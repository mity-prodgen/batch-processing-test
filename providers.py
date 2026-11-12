"""Sync + batch clients for Anthropic, OpenAI and Gemini, behind one small interface.

Each provider exposes:
    sync(ticket)              -> record          (one synchronous request, timed)
    submit(tickets)           -> {"batch_id", "submitted_at"}
    poll(batch_id)            -> {"done", "status", "counts", "completed_at"}
    fetch(batch_id, tickets)  -> [record, ...]   (one per request that came back)

A record is a flat dict (see `record()`); `raw` holds the provider's own response
so token counts can be audited.
"""
import json
import os
import time
from datetime import datetime, timezone

from dataset import CATEGORIES

SYSTEM_PROMPT = (
    "You are a support-ticket classifier. Classify the ticket into exactly one of "
    "these categories: billing, technical, account, shipping, general. "
    "Reply with only the category name in lowercase and nothing else."
)


def user_prompt(text: str) -> str:
    return f"Ticket: {text}"


def parse_label(text: str | None) -> str | None:
    t = (text or "").strip().lower()
    hits = [(t.find(c), c) for c in CATEGORIES if c in t]
    return min(hits)[1] if hits else None


def iso(ts) -> str | None:
    """datetime | unix seconds | None -> ISO-8601 UTC string."""
    if ts is None:
        return None
    if isinstance(ts, (int, float)):
        ts = datetime.fromtimestamp(ts, tz=timezone.utc)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc).isoformat(timespec="seconds")


def now() -> str:
    return iso(datetime.now(timezone.utc))


def record(provider, model, mode, id, status, text="", input_tokens=0, output_tokens=0,
           reasoning_tokens=0, latency_s=None, error=None, raw=None) -> dict:
    """status: succeeded | errored | expired | canceled | missing.
    output_tokens is the billed output count (includes reasoning/thinking tokens);
    reasoning_tokens is the part of it that was reasoning."""
    return {
        "id": id, "provider": provider, "model": model, "mode": mode, "status": status,
        "output_text": text, "label": parse_label(text),
        "input_tokens": input_tokens or 0, "output_tokens": output_tokens or 0,
        "reasoning_tokens": reasoning_tokens or 0,
        "latency_s": latency_s, "error": error, "raw": raw,
    }


class Provider:
    name = ""
    env_keys: tuple[str, ...] = ()
    max_output_tokens = 16      # hard cap sent with every request (worst-case cost bound)
    est_output_tokens = 6       # typical billed output per request (for the estimate)

    def __init__(self, model: str):
        self.model = model

    @classmethod
    def api_key(cls) -> str | None:
        return next((os.environ[k] for k in cls.env_keys if os.environ.get(k)), None)

    def _timed_sync(self, ticket, call) -> dict:
        t0 = time.perf_counter()
        try:
            fields = call()
        except Exception as e:  # keep going; one failed sync call shouldn't kill the run
            return record(self.name, self.model, "sync", ticket["id"], "errored",
                          latency_s=round(time.perf_counter() - t0, 3), error=repr(e))
        return record(self.name, self.model, "sync", ticket["id"], "succeeded",
                      latency_s=round(time.perf_counter() - t0, 3), **fields)


# --------------------------------------------------------------------------- Anthropic
class AnthropicProvider(Provider):
    """Message Batches API: https://platform.claude.com/docs/en/build-with-claude/batch-processing"""
    name = "anthropic"
    env_keys = ("ANTHROPIC_API_KEY",)
    max_output_tokens = 16

    def __init__(self, model):
        super().__init__(model)
        import anthropic
        self.client = anthropic.Anthropic(api_key=self.api_key())

    def _params(self, ticket) -> dict:
        return {
            "model": self.model,
            "max_tokens": self.max_output_tokens,
            # No temperature: current SDK versions no longer expose it, and leaving
            # sampling at the default matches the other two providers.
            "system": SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": user_prompt(ticket["text"])}],
        }

    @staticmethod
    def _fields(msg) -> dict:
        return {
            "text": "".join(b.text for b in msg.content if b.type == "text"),
            "input_tokens": msg.usage.input_tokens,
            "output_tokens": msg.usage.output_tokens,
            "raw": msg.model_dump(mode="json"),
        }

    def sync(self, ticket):
        return self._timed_sync(
            ticket, lambda: self._fields(self.client.messages.create(**self._params(ticket))))

    def submit(self, tickets):
        batch = self.client.messages.batches.create(
            requests=[{"custom_id": t["id"], "params": self._params(t)} for t in tickets])
        return {"batch_id": batch.id, "submitted_at": iso(batch.created_at)}

    def poll(self, batch_id):
        b = self.client.messages.batches.retrieve(batch_id)
        return {
            "done": b.processing_status == "ended",
            "status": b.processing_status,
            "counts": b.request_counts.model_dump(),
            "completed_at": iso(b.ended_at),
        }

    def fetch(self, batch_id, tickets):
        out = []
        for r in self.client.messages.batches.results(batch_id):
            raw = r.model_dump(mode="json")
            kind = r.result.type  # succeeded | errored | canceled | expired
            if kind == "succeeded":
                out.append(record(self.name, self.model, "batch", r.custom_id, "succeeded",
                                  **self._fields(r.result.message)))
            else:
                err = raw["result"].get("error") if kind == "errored" else None
                out.append(record(self.name, self.model, "batch", r.custom_id, kind,
                                  error=err, raw=raw))
        return out


# --------------------------------------------------------------------------- OpenAI
class OpenAIProvider(Provider):
    """Batch API (JSONL file upload): https://developers.openai.com/api/docs/guides/batch"""
    name = "openai"
    env_keys = ("OPENAI_API_KEY",)
    max_output_tokens = 32

    def __init__(self, model):
        super().__init__(model)
        import openai
        self.client = openai.OpenAI(api_key=self.api_key())

    def _body(self, ticket) -> dict:
        # No temperature: GPT-5-family reasoning models reject non-default values.
        # reasoning_effort "none" is the model default; set explicitly so no hidden
        # reasoning tokens inflate the output count.
        return {
            "model": self.model,
            "max_completion_tokens": self.max_output_tokens,
            "reasoning_effort": "none",
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt(ticket["text"])},
            ],
        }

    @staticmethod
    def _fields(body: dict) -> dict:
        usage = body.get("usage") or {}
        details = usage.get("completion_tokens_details") or {}
        choices = body.get("choices") or [{}]
        return {
            "text": (choices[0].get("message") or {}).get("content") or "",
            "input_tokens": usage.get("prompt_tokens"),
            "output_tokens": usage.get("completion_tokens"),
            "reasoning_tokens": details.get("reasoning_tokens"),
            "raw": body,
        }

    def sync(self, ticket):
        return self._timed_sync(ticket, lambda: self._fields(
            self.client.chat.completions.create(**self._body(ticket)).model_dump(mode="json")))

    def submit(self, tickets):
        lines = [json.dumps({"custom_id": t["id"], "method": "POST",
                             "url": "/v1/chat/completions", "body": self._body(t)})
                 for t in tickets]
        payload = ("\n".join(lines) + "\n").encode()
        f = self.client.files.create(file=("batch_input.jsonl", payload), purpose="batch")
        b = self.client.batches.create(input_file_id=f.id, endpoint="/v1/chat/completions",
                                       completion_window="24h")
        return {"batch_id": b.id, "submitted_at": iso(b.created_at)}

    def poll(self, batch_id):
        b = self.client.batches.retrieve(batch_id)
        status = b.status
        if b.errors and b.errors.data:  # e.g. input file failed validation
            status += " (" + "; ".join(e.message or e.code or "?" for e in b.errors.data[:3]) + ")"
        return {
            "done": b.status in ("completed", "failed", "expired", "cancelled"),
            "status": status,
            "counts": b.request_counts.model_dump() if b.request_counts else {},
            "completed_at": iso(b.completed_at or b.failed_at or b.expired_at or b.cancelled_at),
        }

    def fetch(self, batch_id, tickets):
        b = self.client.batches.retrieve(batch_id)
        out = []
        for file_id in (b.output_file_id, b.error_file_id):
            if not file_id:
                continue
            for line in self.client.files.content(file_id).text.splitlines():
                if not line.strip():
                    continue
                obj = json.loads(line)
                resp = obj.get("response") or {}
                if resp.get("status_code") == 200:
                    out.append(record(self.name, self.model, "batch", obj["custom_id"],
                                      "succeeded", **self._fields(resp["body"])))
                else:
                    err = obj.get("error") or (resp.get("body") or {}).get("error") or resp
                    code = err.get("code") if isinstance(err, dict) else None
                    status = "expired" if code == "batch_expired" else "errored"
                    out.append(record(self.name, self.model, "batch", obj["custom_id"],
                                      status, error=err, raw=obj))
        return out


# --------------------------------------------------------------------------- Gemini
class GeminiProvider(Provider):
    """Batch Mode with inline requests: https://aistudio.google.com/docs/batch-api"""
    name = "gemini"
    env_keys = ("GEMINI_API_KEY", "GOOGLE_API_KEY")
    # Thinking can't be switched off on current Flash models and thinking tokens count
    # against max_output_tokens (and are billed as output), so the cap must leave room.
    max_output_tokens = 1024
    est_output_tokens = 150
    DONE = {"JOB_STATE_SUCCEEDED", "JOB_STATE_PARTIALLY_SUCCEEDED", "JOB_STATE_FAILED",
            "JOB_STATE_CANCELLED", "JOB_STATE_EXPIRED"}

    def __init__(self, model):
        super().__init__(model)
        from google import genai
        self.client = genai.Client(api_key=self.api_key())

    def _config(self) -> dict:
        # No temperature: Google recommends leaving Gemini 3 at its default.
        return {
            "system_instruction": SYSTEM_PROMPT,
            "max_output_tokens": self.max_output_tokens,
            "thinking_config": {"thinking_level": "LOW"},
        }

    @staticmethod
    def _text(resp: dict) -> str:
        parts = ((resp.get("candidates") or [{}])[0].get("content") or {}).get("parts") or []
        return "".join(p.get("text") or "" for p in parts if not p.get("thought"))

    def _fields(self, resp: dict) -> dict:
        """resp is a GenerateContentResponse as a dict (snake_case from the SDK,
        camelCase if read from a results file)."""
        u = resp.get("usage_metadata") or resp.get("usageMetadata") or {}
        get = lambda snake, camel: u.get(snake) or u.get(camel) or 0
        thoughts = get("thoughts_token_count", "thoughtsTokenCount")
        return {
            "text": self._text(resp),
            "input_tokens": get("prompt_token_count", "promptTokenCount"),
            "output_tokens": get("candidates_token_count", "candidatesTokenCount") + thoughts,
            "reasoning_tokens": thoughts,
            "raw": resp,
        }

    def sync(self, ticket):
        def call():
            resp = self.client.models.generate_content(
                model=self.model, contents=user_prompt(ticket["text"]), config=self._config())
            return self._fields(resp.model_dump(mode="json", exclude_none=True))
        return self._timed_sync(ticket, call)

    def submit(self, tickets):
        src = [{
            "contents": [{"role": "user", "parts": [{"text": user_prompt(t["text"])}]}],
            "metadata": {"key": t["id"]},
            "config": self._config(),
        } for t in tickets]
        job = self.client.batches.create(model=self.model, src=src,
                                         config={"display_name": "batch-cost-harness"})
        return {"batch_id": job.name, "submitted_at": iso(job.create_time) or now()}

    def poll(self, batch_id):
        job = self.client.batches.get(name=batch_id)
        state = job.state.name if job.state else "JOB_STATE_UNSPECIFIED"
        status = state
        if job.error and job.error.message:
            status += f" ({job.error.message})"
        return {
            "done": state in self.DONE,
            "status": status,
            "counts": job.completion_stats.model_dump(exclude_none=True)
                      if job.completion_stats else {},
            "completed_at": iso(job.end_time) if state in self.DONE else None,
        }

    def fetch(self, batch_id, tickets):
        job = self.client.batches.get(name=batch_id)
        dest = job.dest
        out = []
        if dest and dest.inlined_responses:
            # Inline responses come back in request order; prefer the metadata key if echoed.
            for i, r in enumerate(dest.inlined_responses):
                key = (r.metadata or {}).get("key") or (tickets[i]["id"] if i < len(tickets) else f"idx-{i}")
                if r.error or r.response is None:
                    err = r.error.model_dump(exclude_none=True) if r.error else "no response"
                    out.append(record(self.name, self.model, "batch", key, "errored", error=err))
                else:
                    out.append(self._batch_record(
                        key, r.response.model_dump(mode="json", exclude_none=True)))
        elif dest and dest.file_name:  # the API may hand back a results file instead
            data = self.client.files.download(file=dest.file_name)
            for i, line in enumerate(bytes(data).decode().splitlines()):
                if not line.strip():
                    continue
                obj = json.loads(line)
                key = obj.get("key") or (obj.get("metadata") or {}).get("key") \
                    or (tickets[i]["id"] if i < len(tickets) else f"idx-{i}")
                if obj.get("response"):
                    out.append(self._batch_record(key, obj["response"]))
                else:
                    out.append(record(self.name, self.model, "batch", key, "errored",
                                      error=obj.get("error") or obj, raw=obj))
        return out

    def _batch_record(self, key, resp: dict) -> dict:
        fields = self._fields(resp)
        if not resp.get("candidates"):  # blocked / empty response: nothing usable came back
            return record(self.name, self.model, "batch", key, "errored",
                          error="no candidates in response", **fields)
        return record(self.name, self.model, "batch", key, "succeeded", **fields)


PROVIDERS = {p.name: p for p in (AnthropicProvider, OpenAIProvider, GeminiProvider)}
