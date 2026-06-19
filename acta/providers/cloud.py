"""Cloud and local model providers.

Each provider imports its SDK lazily so ACTA stays installable and runnable with
no optional dependencies. Install extras as needed, e.g. ``pip install acta[openai]``.
"""

from __future__ import annotations

import base64
from pathlib import Path

from acta.providers.base import LLMProvider, LLMResponse


class OpenAIProvider(LLMProvider):
    name = "openai"

    def __init__(self, api_key: str, model: str = "gpt-4o-mini") -> None:
        super().__init__(model)
        self._api_key = api_key
        self._client = None

    def _ensure_client(self):
        if self._client is None:
            from openai import OpenAI  # type: ignore

            self._client = OpenAI(api_key=self._api_key)
        return self._client

    def is_available(self) -> bool:
        return bool(self._api_key)

    def complete(self, messages, *, temperature=0.2, max_tokens=1024) -> LLMResponse:
        client = self._ensure_client()
        resp = client.chat.completions.create(
            model=self.model,
            messages=[{"role": m.role, "content": m.content} for m in messages],
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return LLMResponse(
            text=resp.choices[0].message.content or "",
            provider=self.name,
            model=self.model,
        )

    def describe_image(
        self,
        image_source: str | Path,
        *,
        mime_type: str | None = None,
        prompt: str = "",
        max_tokens: int = 256,
    ) -> str | None:
        if not self.is_available():
            return None
        source = str(image_source)
        if source.startswith("http://") or source.startswith("https://"):
            image_url = source
        else:
            path = Path(source)
            if not path.exists():
                return None
            image_bytes = path.read_bytes()
            guessed = mime_type or "image/jpeg"
            encoded = base64.b64encode(image_bytes).decode("ascii")
            image_url = f"data:{guessed};base64,{encoded}"
        client = self._ensure_client()
        message_text = prompt or "Describe this image."
        resp = client.chat.completions.create(
            model=self.model,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": message_text},
                        {"type": "image_url", "image_url": {"url": image_url}},
                    ],
                }
            ],
            max_tokens=max_tokens,
        )
        return (resp.choices[0].message.content or "").strip() or None


class AnthropicProvider(LLMProvider):
    name = "anthropic"

    def __init__(self, api_key: str, model: str = "claude-3-5-sonnet-latest") -> None:
        super().__init__(model)
        self._api_key = api_key
        self._client = None

    def _ensure_client(self):
        if self._client is None:
            import anthropic  # type: ignore

            self._client = anthropic.Anthropic(api_key=self._api_key)
        return self._client

    def is_available(self) -> bool:
        return bool(self._api_key)

    def complete(self, messages, *, temperature=0.2, max_tokens=1024) -> LLMResponse:
        client = self._ensure_client()
        system = "\n".join(m.content for m in messages if m.role == "system")
        convo = [
            {"role": m.role, "content": m.content}
            for m in messages
            if m.role in ("user", "assistant")
        ]
        resp = client.messages.create(
            model=self.model,
            system=system or None,
            messages=convo,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        text = "".join(block.text for block in resp.content if block.type == "text")
        return LLMResponse(text=text, provider=self.name, model=self.model)


class GeminiProvider(LLMProvider):
    name = "gemini"

    def __init__(self, api_key: str, model: str = "gemini-1.5-flash") -> None:
        super().__init__(model)
        self._api_key = api_key
        self._configured = False

    def _ensure(self):
        if not self._configured:
            import google.generativeai as genai  # type: ignore

            genai.configure(api_key=self._api_key)
            self._genai = genai
            self._configured = True
        return self._genai

    def is_available(self) -> bool:
        return bool(self._api_key)

    def complete(self, messages, *, temperature=0.2, max_tokens=1024) -> LLMResponse:
        genai = self._ensure()
        system = "\n".join(m.content for m in messages if m.role == "system")
        prompt = "\n".join(m.content for m in messages if m.role == "user")
        model = genai.GenerativeModel(self.model, system_instruction=system or None)
        resp = model.generate_content(
            prompt,
            generation_config={"temperature": temperature, "max_output_tokens": max_tokens},
        )
        return LLMResponse(text=resp.text or "", provider=self.name, model=self.model)

    def describe_image(
        self,
        image_source: str | Path,
        *,
        mime_type: str | None = None,
        prompt: str = "",
        max_tokens: int = 256,
    ) -> str | None:
        if not self.is_available():
            return None
        source = str(image_source)
        parts: list[object] = [prompt or "Describe this image."]
        if source.startswith("http://") or source.startswith("https://"):
            parts.append({"file_data": {"file_uri": source}})
        else:
            path = Path(source)
            if not path.exists():
                return None
            image_bytes = path.read_bytes()
            parts.append({"mime_type": mime_type or "image/jpeg", "data": image_bytes})
        genai = self._ensure()
        model = genai.GenerativeModel(self.model)
        resp = model.generate_content(
            parts,
            generation_config={"temperature": 0.2, "max_output_tokens": max_tokens},
        )
        return (resp.text or "").strip() or None


class OllamaProvider(LLMProvider):
    """Local model provider via the Ollama HTTP API."""

    name = "ollama"

    def __init__(self, host: str = "http://localhost:11434", model: str = "llama3.1") -> None:
        super().__init__(model)
        self.host = host.rstrip("/")

    def is_available(self) -> bool:
        import httpx

        try:
            httpx.get(f"{self.host}/api/tags", timeout=1.5)
            return True
        except Exception:
            return False

    def complete(self, messages, *, temperature=0.2, max_tokens=1024) -> LLMResponse:
        import httpx

        payload = {
            "model": self.model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "stream": False,
            "options": {"temperature": temperature, "num_predict": max_tokens},
        }
        resp = httpx.post(f"{self.host}/api/chat", json=payload, timeout=120)
        resp.raise_for_status()
        data = resp.json()
        return LLMResponse(
            text=data.get("message", {}).get("content", ""),
            provider=self.name,
            model=self.model,
            raw=data,
        )
