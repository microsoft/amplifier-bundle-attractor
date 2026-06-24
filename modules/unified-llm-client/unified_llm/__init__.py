"""unified-llm-client: Provider-agnostic LLM client library.

Usage:
    from unified_llm import Client, generate, stream, generate_object
    client = Client.from_env()
    result = await generate(model="claude-sonnet-4-20250514", prompt="Hello")
"""

# Core client
from unified_llm.client import Client, get_default_client, set_default_client

# High-level API
from unified_llm.generate import generate, generate_object, stream, stream_object

# Types
from unified_llm.types import (
    AdapterTimeout,
    AudioData,
    ContentKind,
    ContentPart,
    DocumentData,
    FinishReason,
    GenerateResult,
    ImageData,
    Message,
    ModelInfo,
    RateLimitInfo,
    Request,
    Response,
    ResponseFormat,
    Role,
    StepResult,
    StreamAccumulator,
    StreamEvent,
    StreamEventType,
    ThinkingData,
    TimeoutConfig,
    Tool,
    ToolCall,
    ToolCallData,
    ToolChoice,
    ToolResult,
    ToolResultData,
    Usage,
    Warning,
)

# Errors
from unified_llm.errors import (
    AbortError,
    AccessDeniedError,
    AuthenticationError,
    ConfigurationError,
    ContentFilterError,
    ContextLengthError,
    InvalidRequestError,
    InvalidToolCallError,
    NetworkError,
    NoObjectGeneratedError,
    NotFoundError,
    ProviderError,
    QuotaExceededError,
    RateLimitError,
    RequestTimeoutError,
    SDKError,
    ServerError,
    StreamError,
    StreamProtocolError,
)

# Stream validation
from unified_llm.stream_validation import validate_stream

# Retry
from unified_llm.retry import RetryPolicy

# Catalog
from unified_llm.catalog import get_latest_model, get_model_info, list_models

# Resolver — live-backed model selection (eliminates id-seam)
from unified_llm.resolver import resolve_latest, resolve_latest_for, select_latest

# Adapters
from unified_llm.adapters import ProviderAdapter

__all__ = [
    # Client
    "Client",
    "set_default_client",
    "get_default_client",
    # High-level API
    "generate",
    "stream",
    "generate_object",
    "stream_object",
    "validate_stream",
    # Types - core
    "Role",
    "ContentKind",
    "ContentPart",
    "Message",
    "ImageData",
    "AudioData",
    "DocumentData",
    "ToolCallData",
    "ToolResultData",
    "ThinkingData",
    # Types - request/response
    "Request",
    "Response",
    "ResponseFormat",
    "FinishReason",
    "Usage",
    "Warning",
    "RateLimitInfo",
    # Types - generation
    "GenerateResult",
    "StepResult",
    "StreamEvent",
    "StreamEventType",
    "StreamAccumulator",
    # Types - tools
    "Tool",
    "ToolChoice",
    "ToolCall",
    "ToolResult",
    # Types - config
    "TimeoutConfig",
    "AdapterTimeout",
    "ModelInfo",
    "RetryPolicy",
    # Errors
    "SDKError",
    "ProviderError",
    "AuthenticationError",
    "AccessDeniedError",
    "NotFoundError",
    "InvalidRequestError",
    "RateLimitError",
    "ServerError",
    "ContentFilterError",
    "ContextLengthError",
    "QuotaExceededError",
    "RequestTimeoutError",
    "AbortError",
    "NetworkError",
    "StreamError",
    "StreamProtocolError",
    "InvalidToolCallError",
    "NoObjectGeneratedError",
    "ConfigurationError",
    # Catalog
    "get_model_info",
    "list_models",
    "get_latest_model",
    # Resolver — live-backed model selection
    "select_latest",
    "resolve_latest",
    "resolve_latest_for",
    # Adapter interface
    "ProviderAdapter",
]
