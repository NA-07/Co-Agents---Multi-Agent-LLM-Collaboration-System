import os
import pdb
from dataclasses import dataclass

import pytest
from dotenv import load_dotenv
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_ollama import ChatOllama

load_dotenv()

import sys

sys.path.append(".")


@dataclass
class LLMConfig:
    provider: str
    model_name: str
    temperature: float = 0.8
    base_url: str = None
    api_key: str = None


def create_message_content(text, image_path=None):
    content = [{"type": "text", "text": text}]
    image_format = "png" if image_path and image_path.endswith(".png") else "jpeg"
    if image_path:
        from src.utils import utils
        image_data = utils.encode_image(image_path)
        content.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/{image_format};base64,{image_data}"}
        })
    return content


def get_env_value(key, provider):
    env_mappings = {
        "openai": {"api_key": "OPENAI_API_KEY", "base_url": "OPENAI_ENDPOINT"},
        "azure_openai": {"api_key": "AZURE_OPENAI_API_KEY", "base_url": "AZURE_OPENAI_ENDPOINT"},
        "google": {"api_key": "GOOGLE_API_KEY"},
        "deepseek": {"api_key": "DEEPSEEK_API_KEY", "base_url": "DEEPSEEK_ENDPOINT"},
        "mistral": {"api_key": "MISTRAL_API_KEY", "base_url": "MISTRAL_ENDPOINT"},
        "alibaba": {"api_key": "ALIBABA_API_KEY", "base_url": "ALIBABA_ENDPOINT"},
        "moonshot": {"api_key": "MOONSHOT_API_KEY", "base_url": "MOONSHOT_ENDPOINT"},
        "ibm": {"api_key": "IBM_API_KEY", "base_url": "IBM_ENDPOINT"}
    }

    if provider in env_mappings and key in env_mappings[provider]:
        return os.getenv(env_mappings[provider][key], "")
    return ""


def _run_llm_test(config, query, image_path=None, system_message=None):
    from src.utils import utils, llm_provider

    # Special handling for Ollama-based models
    if config.provider == "ollama":
        if "deepseek-r1" in config.model_name:
            from src.utils.llm_provider import DeepSeekR1ChatOllama
            llm = DeepSeekR1ChatOllama(model=config.model_name)
        else:
            llm = ChatOllama(model=config.model_name)

        ai_msg = llm.invoke(query)
        print(ai_msg.content)
        if "deepseek-r1" in config.model_name:
            pdb.set_trace()
        return

    # For other providers, use the standard configuration
    llm = llm_provider.get_llm_model(
        provider=config.provider,
        model_name=config.model_name,
        temperature=config.temperature,
        base_url=config.base_url or get_env_value("base_url", config.provider),
        api_key=config.api_key or get_env_value("api_key", config.provider)
    )

    # Prepare messages for non-Ollama models
    messages = []
    if system_message:
        messages.append(SystemMessage(content=create_message_content(system_message)))
    messages.append(HumanMessage(content=create_message_content(query, image_path)))
    ai_msg = llm.invoke(messages)

    # Handle different response types
    if hasattr(ai_msg, "reasoning_content"):
        print(ai_msg.reasoning_content)
    print(ai_msg.content)

@pytest.mark.skipif(not os.getenv("OPENAI_API_KEY"), reason="OPENAI_API_KEY not set")
def test_openai_model():
    config = LLMConfig(provider="openai", model_name="gpt-4o")
    _run_llm_test(config, "Describe this image", "assets/examples/test.png")


@pytest.mark.skipif(not os.getenv("GOOGLE_API_KEY"), reason="GOOGLE_API_KEY not set")
def test_google_model():
    config = LLMConfig(provider="google", model_name="gemini-2.0-flash-exp")
    _run_llm_test(config, "Describe this image", "assets/examples/test.png")


@pytest.mark.skipif(not os.getenv("AZURE_OPENAI_API_KEY"), reason="AZURE_OPENAI_API_KEY not set")
def test_azure_openai_model():
    config = LLMConfig(provider="azure_openai", model_name="gpt-4o")
    _run_llm_test(config, "Describe this image", "assets/examples/test.png")


@pytest.mark.skipif(not os.getenv("DEEPSEEK_API_KEY"), reason="DEEPSEEK_API_KEY not set")
def test_deepseek_model():
    config = LLMConfig(provider="deepseek", model_name="deepseek-chat")
    _run_llm_test(config, "Who are you?")


@pytest.mark.skipif(not os.getenv("DEEPSEEK_API_KEY"), reason="DEEPSEEK_API_KEY not set")
def test_deepseek_r1_model():
    config = LLMConfig(provider="deepseek", model_name="deepseek-reasoner")
    _run_llm_test(config, "Which is greater, 9.11 or 9.8?", system_message="You are a helpful AI assistant.")


def test_ollama_model():
    pytest.importorskip("ollama", reason="ollama package not available")
    config = LLMConfig(provider="ollama", model_name="qwen2.5:7b")
    _run_llm_test(config, "Sing a ballad of LangChain.")


def test_deepseek_r1_ollama_model():
    pytest.importorskip("ollama", reason="ollama package not available")
    config = LLMConfig(provider="ollama", model_name="deepseek-r1:14b")
    _run_llm_test(config, "How many 'r's are in the word 'strawberry'?")


@pytest.mark.skipif(not os.getenv("MISTRAL_API_KEY"), reason="MISTRAL_API_KEY not set")
def test_mistral_model():
    config = LLMConfig(provider="mistral", model_name="pixtral-large-latest")
    _run_llm_test(config, "Describe this image", "assets/examples/test.png")


@pytest.mark.skipif(not os.getenv("MOONSHOT_API_KEY"), reason="MOONSHOT_API_KEY not set")
def test_moonshot_model():
    config = LLMConfig(provider="moonshot", model_name="moonshot-v1-32k-vision-preview")
    _run_llm_test(config, "Describe this image", "assets/examples/test.png")


@pytest.mark.skipif(not os.getenv("IBM_API_KEY"), reason="IBM_API_KEY not set")
def test_ibm_model():
    config = LLMConfig(provider="ibm", model_name="meta-llama/llama-4-maverick-17b-128e-instruct-fp8")
    _run_llm_test(config, "Describe this image", "assets/examples/test.png")


@pytest.mark.skipif(not os.getenv("ALIBABA_API_KEY"), reason="ALIBABA_API_KEY not set")
def test_qwen_model():
    config = LLMConfig(provider="alibaba", model_name="qwen-vl-max")
    _run_llm_test(config, "How many 'r's are in the word 'strawberry'?")


if __name__ == "__main__":
    # _run_llm_test(LLMConfig("openai", "gpt-4o"), "Hello")
    # _run_llm_test(LLMConfig("google", "gemini-2.0-flash-exp"), "Hello")
    _run_llm_test(LLMConfig("azure_openai", "gpt-4o"), "Describe this image", "assets/examples/test.png")
