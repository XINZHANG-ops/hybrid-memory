from abc import ABC, abstractmethod
from loguru import logger


class LLMClient(ABC):
    @abstractmethod
    def generate(self, prompt: str) -> str:
        pass


class OllamaClient(LLMClient):
    def __init__(self, model: str = "qwen2.5:7b", base_url: str = "http://localhost:11434", timeout: float = 300.0, keep_alive: str = "60m"):
        self.model = model
        self.base_url = base_url
        self.timeout = timeout
        # 处理 keep_alive 格式：-1 表示永久，需要转换为秒数
        if keep_alive == "-1":
            self.keep_alive = -1  # 传整数 -1 表示永久
        else:
            self.keep_alive = keep_alive
        logger.info(f"Initialized OllamaClient: model={model}, base_url={base_url}, timeout={timeout}s, keep_alive={self.keep_alive}")

    def generate(self, prompt: str) -> str:
        from ollama import Client
        logger.debug(f"OllamaClient.generate: prompt_length={len(prompt)}")
        logger.debug(f"Calling Ollama chat API with model={self.model}, keep_alive={self.keep_alive}")
        try:
            client = Client(host=self.base_url, timeout=self.timeout)
            response = client.chat(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                keep_alive=self.keep_alive,
            )
            result = response["message"]["content"]
            logger.info(f"Ollama response received: length={len(result)}")
            logger.debug(f"Ollama response preview: {result[:200]}...")
            return result
        except Exception as e:
            logger.error(f"Ollama API error: {e}")
            raise


class AnthropicClient(LLMClient):
    def __init__(self, api_key: str, model: str = "claude-sonnet-4-20250514"):
        import anthropic
        self.client = anthropic.Anthropic(api_key=api_key)
        self.model = model
        logger.info(f"Initialized AnthropicClient: model={model}")

    def generate(self, prompt: str) -> str:
        logger.debug(f"AnthropicClient.generate: prompt_length={len(prompt)}")
        logger.debug("Sending request to Anthropic API")
        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=2048,
                messages=[{"role": "user", "content": prompt}],
            )
            result = response.content[0].text
            logger.info(f"Anthropic response received: length={len(result)}")
            logger.debug(f"Anthropic response preview: {result[:200]}...")
            return result
        except Exception as e:
            logger.error(f"Anthropic API error: {e}")
            raise


def create_llm_client(
    provider: str = "ollama",
    ollama_model: str = "qwen2.5:7b",
    ollama_base_url: str = "http://localhost:11434",
    ollama_timeout: float = 300.0,
    ollama_keep_alive: str = "10m",
    anthropic_api_key: str | None = None,
    anthropic_model: str = "claude-sonnet-4-20250514",
) -> LLMClient:
    logger.debug(f"Creating LLM client: provider={provider}")
    if provider == "ollama":
        logger.info(f"Using Ollama provider with model: {ollama_model}, timeout={ollama_timeout}s, keep_alive={ollama_keep_alive}")
        return OllamaClient(model=ollama_model, base_url=ollama_base_url, timeout=ollama_timeout, keep_alive=ollama_keep_alive)
    elif provider == "anthropic":
        if not anthropic_api_key:
            logger.error("anthropic_api_key required for anthropic provider")
            raise ValueError("anthropic_api_key required for anthropic provider")
        logger.info(f"Using Anthropic provider with model: {anthropic_model}")
        return AnthropicClient(api_key=anthropic_api_key, model=anthropic_model)
    else:
        logger.error(f"Unknown LLM provider: {provider}")
        raise ValueError(f"Unknown LLM provider: {provider}")
