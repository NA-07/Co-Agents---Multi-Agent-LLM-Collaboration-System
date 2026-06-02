from openai import OpenAI, AsyncOpenAI
import asyncio as _asyncio
import logging
from langchain_openai import ChatOpenAI
from langchain_core.globals import get_llm_cache
from langchain_core.language_models.base import (
    BaseLanguageModel,
    LangSmithParams,
    LanguageModelInput,
)
import os
from langchain_core.load import dumpd, dumps
from langchain_core.messages import (
    AIMessage,
    SystemMessage,
    AnyMessage,
    BaseMessage,
    BaseMessageChunk,
    HumanMessage,
    convert_to_messages,
    message_chunk_to_message,
)
from langchain_core.outputs import (
    ChatGeneration,
    ChatGenerationChunk,
    ChatResult,
    LLMResult,
    RunInfo,
)
from langchain_ollama import ChatOllama
from langchain_core.output_parsers.base import OutputParserLike
from langchain_core.runnables import Runnable, RunnableConfig
from langchain_core.tools import BaseTool

from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
    Literal,
    Optional,
    Union,
    cast, List,
)
from langchain_anthropic import ChatAnthropic
from langchain_mistralai import ChatMistralAI
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_google_vertexai import ChatVertexAI
from langchain_ollama import ChatOllama
from langchain_openai import AzureChatOpenAI, ChatOpenAI
from langchain_ibm import ChatWatsonx
from langchain_aws import ChatBedrock
from pydantic import SecretStr

from src.utils import config


class DeepSeekR1ChatOpenAI(ChatOpenAI):

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._sync_client = OpenAI(
            base_url=kwargs.get("base_url"),
            api_key=kwargs.get("api_key")
        )
        self._async_client = AsyncOpenAI(
            base_url=kwargs.get("base_url"),
            api_key=kwargs.get("api_key")
        )

    async def ainvoke(
            self,
            input: LanguageModelInput,
            config: Optional[RunnableConfig] = None,
            *,
            stop: Optional[list[str]] = None,
            **kwargs: Any,
    ) -> AIMessage:
        message_history = []
        for input_ in input:
            if isinstance(input_, SystemMessage):
                message_history.append({"role": "system", "content": input_.content})
            elif isinstance(input_, AIMessage):
                message_history.append({"role": "assistant", "content": input_.content})
            else:
                message_history.append({"role": "user", "content": input_.content})

        # Use async client to avoid blocking the event loop
        response = await self._async_client.chat.completions.create(
            model=self.model_name,
            messages=message_history
        )

        reasoning_content = response.choices[0].message.reasoning_content
        content = response.choices[0].message.content
        return AIMessage(content=content, reasoning_content=reasoning_content)

    def invoke(
            self,
            input: LanguageModelInput,
            config: Optional[RunnableConfig] = None,
            *,
            stop: Optional[list[str]] = None,
            **kwargs: Any,
    ) -> AIMessage:
        message_history = []
        for input_ in input:
            if isinstance(input_, SystemMessage):
                message_history.append({"role": "system", "content": input_.content})
            elif isinstance(input_, AIMessage):
                message_history.append({"role": "assistant", "content": input_.content})
            else:
                message_history.append({"role": "user", "content": input_.content})

        response = self._sync_client.chat.completions.create(
            model=self.model_name,
            messages=message_history
        )

        reasoning_content = response.choices[0].message.reasoning_content
        content = response.choices[0].message.content
        return AIMessage(content=content, reasoning_content=reasoning_content)


def _split_think_content(org_content: str):
    """Safely split <think>...</think> content. Returns (reasoning, content)."""
    if "</think>" in org_content:
        parts = org_content.split("</think>", 1)
        reasoning = parts[0].replace("<think>", "").strip()
        content = parts[1].strip()
    elif "<think>" in org_content:
        # Has opening tag but no closing — treat everything after as content
        reasoning = org_content.replace("<think>", "").strip()
        content = reasoning
        reasoning = ""
    else:
        # No think tags at all — just return as content
        reasoning = ""
        content = org_content.strip()
    if "**JSON Response:**" in content:
        content = content.split("**JSON Response:**")[-1].strip()
    return reasoning, content


class DeepSeekR1ChatOllama(ChatOllama):

    async def ainvoke(
            self,
            input: LanguageModelInput,
            config: Optional[RunnableConfig] = None,
            *,
            stop: Optional[list[str]] = None,
            **kwargs: Any,
    ) -> AIMessage:
        org_ai_message = await super().ainvoke(input=input)
        reasoning, content = _split_think_content(org_ai_message.content)
        return AIMessage(content=content, reasoning_content=reasoning)

    def invoke(
            self,
            input: LanguageModelInput,
            config: Optional[RunnableConfig] = None,
            *,
            stop: Optional[list[str]] = None,
            **kwargs: Any,
    ) -> AIMessage:
        org_ai_message = super().invoke(input=input)
        reasoning, content = _split_think_content(org_ai_message.content)
        return AIMessage(content=content, reasoning_content=reasoning)


import urllib.request
import urllib.error


logger = logging.getLogger(__name__)

_GOOGLE_VERTEX_TOKEN_ENV_VAR = "GOOGLE_VERTEX_ACCESS_TOKEN"
_GOOGLE_VERTEX_BASE_URL_ENV_VAR = "GOOGLE_VERTEX_BASE_URL"
_GOOGLE_CLOUD_PROJECT_ENV_VAR = "GOOGLE_CLOUD_PROJECT"
_GOOGLE_CLOUD_LOCATION_ENV_VAR = "GOOGLE_CLOUD_LOCATION"
_GOOGLE_GENAI_USE_VERTEXAI_ENV_VAR = "GOOGLE_GENAI_USE_VERTEXAI"
_GOOGLE_APPLICATION_CREDENTIALS_ENV_VAR = "GOOGLE_APPLICATION_CREDENTIALS"
_GOOGLE_CLOUD_PLATFORM_SCOPE = "https://www.googleapis.com/auth/cloud-platform"
_GOOGLE_VERTEX_MODEL_ALIASES = {
    "gemini-2.0-flash": "google/gemini-2.0-flash-001",
    "gemini-2.0-flash-lite": "google/gemini-2.0-flash-lite-001",
}


def _is_truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "t", "yes", "y", "on"}


def _is_vertex_base_url(base_url: Optional[str]) -> bool:
    return bool(base_url and "aiplatform.googleapis.com" in base_url.lower())


def _should_use_google_vertex(kwargs: dict[str, Any]) -> bool:
    return any(
        [
            _is_truthy(kwargs.get("use_vertexai")),
            _is_truthy(os.getenv(_GOOGLE_GENAI_USE_VERTEXAI_ENV_VAR)),
            bool(kwargs.get("google_cloud_project") or os.getenv(_GOOGLE_CLOUD_PROJECT_ENV_VAR)),
            bool(
                kwargs.get("google_application_credentials")
                or os.getenv(_GOOGLE_APPLICATION_CREDENTIALS_ENV_VAR)
            ),
            bool(kwargs.get("vertex_access_token") or os.getenv(_GOOGLE_VERTEX_TOKEN_ENV_VAR)),
            bool(kwargs.get("vertex_base_url") or os.getenv(_GOOGLE_VERTEX_BASE_URL_ENV_VAR)),
            _is_vertex_base_url(kwargs.get("base_url")),
        ]
    )


def _normalize_google_vertex_model_name(model_name: str) -> str:
    if model_name.startswith("google/"):
        return model_name
    return _GOOGLE_VERTEX_MODEL_ALIASES.get(model_name, f"google/{model_name}")


def _build_google_vertex_base_url(
        project_id: Optional[str],
        location: Optional[str],
        explicit_base_url: Optional[str] = None,
) -> str:
    if explicit_base_url:
        return explicit_base_url.rstrip("/")

    if not project_id:
        raise ValueError(
            "Google Vertex AI backend requires GOOGLE_CLOUD_PROJECT (or a Vertex base URL)."
        )

    normalized_location = (location or "global").strip()
    if normalized_location == "global":
        return (
            "https://aiplatform.googleapis.com/"
            f"v1/projects/{project_id}/locations/{normalized_location}/endpoints/openapi"
        )

    return (
        f"https://{normalized_location}-aiplatform.googleapis.com/"
        f"v1/projects/{project_id}/locations/{normalized_location}/endpoints/openapi"
    )


def _looks_like_google_api_key(value: Optional[str]) -> bool:
    return bool(value and value.startswith("AIza"))


def _looks_like_non_oauth_token(value: Optional[str]) -> bool:
    """Return True if the value is clearly NOT a Google OAuth2 access token.
    Vertex AI Express keys (AQ.*) and Gemini Developer API keys (AIza*) cannot
    be used as bearer tokens on the OpenAI-compatible Vertex endpoint."""
    if not value:
        return True
    return value.startswith("AIza") or value.startswith("AQ.")


def _get_google_vertex_access_token(explicit_token: Optional[str] = None) -> tuple[str, Optional[str]]:
    if explicit_token:
        return explicit_token, None

    try:
        import google.auth
        import google.auth.transport.requests
    except ImportError as exc:
        raise ValueError(
            "Google Vertex AI authentication requires google-auth. "
            "Install dependencies and configure Application Default Credentials."
        ) from exc

    credentials, detected_project = google.auth.default(scopes=[_GOOGLE_CLOUD_PLATFORM_SCOPE])
    request = google.auth.transport.requests.Request()
    if not credentials.valid or not getattr(credentials, "token", None):
        credentials.refresh(request)

    token = getattr(credentials, "token", None)
    if not token:
        raise ValueError(
            "Failed to obtain a Google Cloud access token. Run `gcloud auth application-default login` "
            "or set GOOGLE_APPLICATION_CREDENTIALS to a service account JSON file."
        )

    return token, detected_project


def _create_google_vertex_chat_model(**kwargs: Any) -> ChatVertexAI:
    """Create a native ChatVertexAI instance using service account / ADC auth.

    Uses the langchain-google-vertexai package which provides full support
    for function calling and structured output with Gemini models.
    """
    import warnings
    warnings.filterwarnings("ignore", message=".*ChatVertexAI.*deprecated.*")

    project_id = kwargs.get("google_cloud_project") or os.getenv(_GOOGLE_CLOUD_PROJECT_ENV_VAR)
    location = kwargs.get("google_cloud_location") or os.getenv(_GOOGLE_CLOUD_LOCATION_ENV_VAR) or "us-central1"
    model_name = kwargs.get("model_name", "gemini-2.0-flash")

    if not project_id:
        raise ValueError(
            "Google Vertex AI requires GOOGLE_CLOUD_PROJECT. "
            "Set it in .env or pass google_cloud_project."
        )

    logger.info(
        "Using Google Vertex AI (ChatVertexAI) for model %s (%s/%s).",
        model_name, project_id, location,
    )

    return ChatVertexAI(
        model_name=model_name,
        project=project_id,
        location=location,
        temperature=kwargs.get("temperature", 0.0),
        max_retries=kwargs.get("max_retries", 3),
    )


class RateLimitAwareChatGoogle(ChatGoogleGenerativeAI):
    """
    Drop-in replacement for ChatGoogleGenerativeAI that handles Gemini's
    free-tier 429 Resource Exhausted error with automatic exponential back-off
    (waits 30 s, 60 s, 90 s, 120 s, 150 s) before giving up.
    This prevents the agent from dying mid-task just because of rate limits.
    """

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
        max_retries = 5
        last_exception = None
        for attempt in range(max_retries):
            try:
                return await super()._agenerate(
                    messages, stop=stop, run_manager=run_manager, **kwargs
                )
            except Exception as exc:
                last_exception = exc
                err_str = str(exc).lower()
                is_rate_limit = (
                    "429" in err_str
                    or "resource exhausted" in err_str
                    or "quota" in err_str
                )
                if is_rate_limit and attempt < max_retries - 1:
                    wait_secs = 30 * (attempt + 1)  # 30s, 60s, 90s, 120s
                    logger.warning(
                        f"⏳ Gemini rate limit (429). Waiting {wait_secs}s "
                        f"then retrying (attempt {attempt + 1}/{max_retries - 1})..."
                    )
                    await _asyncio.sleep(wait_secs)
                    continue
                if is_rate_limit:
                    raise RuntimeError(
                        "Gemini Developer API quota was exhausted. This repo's default Google path uses the "
                        "Gemini Developer API, not Vertex AI billing. If you intended to use Vertex AI, "
                        "configure GOOGLE_CLOUD_PROJECT/GOOGLE_CLOUD_LOCATION and authenticate with "
                        "`gcloud auth application-default login` or GOOGLE_APPLICATION_CREDENTIALS."
                    ) from exc
                raise
        # Final attempt — let any exception propagate naturally
        if last_exception is not None:
            raise RuntimeError(
                "Gemini request failed after retrying the Google Developer API backend."
            ) from last_exception
        return await super()._agenerate(messages, stop=stop, run_manager=run_manager, **kwargs)


def check_ollama_connection(base_url: str = "http://localhost:11434") -> tuple:
    """
    Verify Ollama is reachable before creating the LLM client.
    Returns (is_ok: bool, message: str)
    """
    import json as _json
    try:
        url = base_url.rstrip("/") + "/api/tags"
        req = urllib.request.urlopen(url, timeout=5)
        data = _json.loads(req.read())
        models = [m["name"] for m in data.get("models", [])]
        if not models:
            return False, (
                f"Ollama is running at {base_url} but NO models are pulled yet.\n"
                f"  Fix: Run  ollama pull qwen2.5:7b  in a terminal."
            )
        return True, f"Connected — {len(models)} model(s): {', '.join(models[:3])}"
    except urllib.error.URLError as e:
        return False, (
            f"Cannot reach Ollama at {base_url}.\n"
            f"  Reason : {e.reason}\n"
            f"  Fix    : 1) Open the Ollama desktop app\n"
            f"           2) Or run  ollama serve  in a terminal\n"
            f"           3) Or change Base URL in Agent Settings"
        )
    except Exception as e:
        return False, f"Ollama check error: {e}"


def get_llm_model(provider: str, **kwargs):
    """
    Get LLM model
    :param provider: LLM provider
    :param kwargs:
    :return:
    """
    is_google_vertex = provider == "google" and _should_use_google_vertex(kwargs)

    if provider not in ["ollama", "bedrock"] and not is_google_vertex:
        env_var = f"{provider.upper()}_API_KEY"
        api_key = kwargs.get("api_key", "") or os.getenv(env_var, "")
        if not api_key:
            provider_display = config.PROVIDER_DISPLAY_NAMES.get(provider, provider.upper())
            error_msg = f"💥 {provider_display} API key not found! 🔑 Please set the `{env_var}` environment variable or provide it in the UI."
            raise ValueError(error_msg)
        kwargs["api_key"] = api_key
    else:
        api_key = kwargs.get("api_key", "")

    if provider == "anthropic":
        if not kwargs.get("base_url", ""):
            base_url = "https://api.anthropic.com"
        else:
            base_url = kwargs.get("base_url")

        return ChatAnthropic(
            model=kwargs.get("model_name", "claude-3-5-sonnet-20241022"),
            temperature=kwargs.get("temperature", 0.0),
            base_url=base_url,
            api_key=api_key,
        )
    elif provider == 'mistral':
        if not kwargs.get("base_url", ""):
            base_url = os.getenv("MISTRAL_ENDPOINT", "https://api.mistral.ai/v1")
        else:
            base_url = kwargs.get("base_url")
        if not kwargs.get("api_key", ""):
            api_key = os.getenv("MISTRAL_API_KEY", "")
        else:
            api_key = kwargs.get("api_key")

        return ChatMistralAI(
            model=kwargs.get("model_name", "mistral-large-latest"),
            temperature=kwargs.get("temperature", 0.0),
            base_url=base_url,
            api_key=api_key,
        )
    elif provider == "openai":
        if not kwargs.get("base_url", ""):
            base_url = os.getenv("OPENAI_ENDPOINT", "https://api.openai.com/v1")
        else:
            base_url = kwargs.get("base_url")

        return ChatOpenAI(
            model=kwargs.get("model_name", "gpt-4o"),
            temperature=kwargs.get("temperature", 0.0),
            base_url=base_url,
            api_key=api_key,
        )
    elif provider == "grok":
        if not kwargs.get("base_url", ""):
            base_url = os.getenv("GROK_ENDPOINT", "https://api.x.ai/v1")
        else:
            base_url = kwargs.get("base_url")

        return ChatOpenAI(
            model=kwargs.get("model_name", "grok-3"),
            temperature=kwargs.get("temperature", 0.0),
            base_url=base_url,
            api_key=api_key,
        )
    elif provider == "deepseek":
        if not kwargs.get("base_url", ""):
            base_url = os.getenv("DEEPSEEK_ENDPOINT", "")
        else:
            base_url = kwargs.get("base_url")

        if kwargs.get("model_name", "deepseek-chat") == "deepseek-reasoner":
            return DeepSeekR1ChatOpenAI(
                model=kwargs.get("model_name", "deepseek-reasoner"),
                temperature=kwargs.get("temperature", 0.0),
                base_url=base_url,
                api_key=api_key,
            )
        else:
            return ChatOpenAI(
                model=kwargs.get("model_name", "deepseek-chat"),
                temperature=kwargs.get("temperature", 0.0),
                base_url=base_url,
                api_key=api_key,
            )
    elif provider == "google":
        if is_google_vertex:
            return _create_google_vertex_chat_model(**kwargs)
        return RateLimitAwareChatGoogle(
            model=kwargs.get("model_name", "gemini-2.0-flash"),
            temperature=kwargs.get("temperature", 0.0),
            api_key=api_key,
            max_retries=3,
        )
    elif provider == "ollama":
        if not kwargs.get("base_url", ""):
            base_url = os.getenv("OLLAMA_ENDPOINT", "http://localhost:11434")
        else:
            base_url = kwargs.get("base_url")

        # Validate Ollama is reachable before creating the client
        is_ok, msg = check_ollama_connection(base_url)
        if not is_ok:
            raise ConnectionError(
                f"🦙 Ollama connection failed!\n{msg}"
            )

        if "deepseek-r1" in kwargs.get("model_name", "qwen2.5:7b"):
            return DeepSeekR1ChatOllama(
                model=kwargs.get("model_name", "deepseek-r1:14b"),
                temperature=kwargs.get("temperature", 0.0),
                num_ctx=kwargs.get("num_ctx", 32000),
                base_url=base_url,
            )
        else:
            return ChatOllama(
                model=kwargs.get("model_name", "qwen2.5:7b"),
                temperature=kwargs.get("temperature", 0.0),
                num_ctx=kwargs.get("num_ctx", 32000),
                num_predict=kwargs.get("num_predict", 1024),
                base_url=base_url,
            )
    elif provider == "azure_openai":
        if not kwargs.get("base_url", ""):
            base_url = os.getenv("AZURE_OPENAI_ENDPOINT", "")
        else:
            base_url = kwargs.get("base_url")
        api_version = kwargs.get("api_version", "") or os.getenv("AZURE_OPENAI_API_VERSION", "2025-01-01-preview")
        return AzureChatOpenAI(
            model=kwargs.get("model_name", "gpt-4o"),
            temperature=kwargs.get("temperature", 0.0),
            api_version=api_version,
            azure_endpoint=base_url,
            api_key=api_key,
        )
    elif provider == "alibaba":
        if not kwargs.get("base_url", ""):
            base_url = os.getenv("ALIBABA_ENDPOINT", "https://dashscope.aliyuncs.com/compatible-mode/v1")
        else:
            base_url = kwargs.get("base_url")

        return ChatOpenAI(
            model=kwargs.get("model_name", "qwen-plus"),
            temperature=kwargs.get("temperature", 0.0),
            base_url=base_url,
            api_key=api_key,
        )
    elif provider == "ibm":
        parameters = {
            "temperature": kwargs.get("temperature", 0.0),
            "max_tokens": kwargs.get("num_ctx", 32000)
        }
        if not kwargs.get("base_url", ""):
            base_url = os.getenv("IBM_ENDPOINT", "https://us-south.ml.cloud.ibm.com")
        else:
            base_url = kwargs.get("base_url")

        return ChatWatsonx(
            model_id=kwargs.get("model_name", "ibm/granite-vision-3.1-2b-preview"),
            url=base_url,
            project_id=os.getenv("IBM_PROJECT_ID"),
            apikey=os.getenv("IBM_API_KEY"),
            params=parameters
        )
    elif provider == "moonshot":
        return ChatOpenAI(
            model=kwargs.get("model_name", "moonshot-v1-32k-vision-preview"),
            temperature=kwargs.get("temperature", 0.0),
            base_url=os.getenv("MOONSHOT_ENDPOINT"),
            api_key=os.getenv("MOONSHOT_API_KEY"),
        )
    elif provider == "unbound":
        return ChatOpenAI(
            model=kwargs.get("model_name", "gpt-4o-mini"),
            temperature=kwargs.get("temperature", 0.0),
            base_url=os.getenv("UNBOUND_ENDPOINT", "https://api.getunbound.ai"),
            api_key=api_key,
        )
    elif provider == "siliconflow":
        if not kwargs.get("api_key", ""):
            api_key = os.getenv("SiliconFLOW_API_KEY", "")
        else:
            api_key = kwargs.get("api_key")
        if not kwargs.get("base_url", ""):
            base_url = os.getenv("SiliconFLOW_ENDPOINT", "")
        else:
            base_url = kwargs.get("base_url")
        return ChatOpenAI(
            api_key=api_key,
            base_url=base_url,
            model_name=kwargs.get("model_name", "Qwen/QwQ-32B"),
            temperature=kwargs.get("temperature", 0.0),
        )
    elif provider == "modelscope":
        if not kwargs.get("api_key", ""):
            api_key = os.getenv("MODELSCOPE_API_KEY", "")
        else:
            api_key = kwargs.get("api_key")
        if not kwargs.get("base_url", ""):
            base_url = os.getenv("MODELSCOPE_ENDPOINT", "")
        else:
            base_url = kwargs.get("base_url")
        return ChatOpenAI(
            api_key=api_key,
            base_url=base_url,
            model_name=kwargs.get("model_name", "Qwen/QwQ-32B"),
            temperature=kwargs.get("temperature", 0.0),
            extra_body = {"enable_thinking": False}
        )
    else:
        raise ValueError(f"Unsupported provider: {provider}")
