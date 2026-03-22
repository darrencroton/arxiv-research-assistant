"""Base provider class for the Science Paper Summariser.

Defines the interface that all providers (API and CLI) must implement.
"""

import logging


class Provider:
    """Base class for all LLM providers.

    Subclasses must implement:
        setup()              — Initialise provider-specific clients or validate tools.
        process_document()   — Send content to the LLM and return the summary text.
        get_max_context_size() — Return the context window size for this provider family.

    Optionally override:
        supports_direct_pdf() — Whether the provider accepts raw PDF bytes (default: False).
    """

    def __init__(self, config=None):
        self.config = config or {}
        self.model = self.config.get("model")
        self.setup()

    def setup(self):
        """Initialise provider-specific components. Override in subclasses."""
        pass

    def process_document(self, content, is_pdf, system_prompt, user_prompt, max_tokens=12288):
        """Process a document and return the generated summary text.

        Args:
            content: Document content — bytes for direct PDF upload, str for extracted text.
            is_pdf: Whether the source file is a PDF.
            system_prompt: System prompt with role, rules, and knowledge base.
            user_prompt: User prompt with task, template, and optionally paper text.
            max_tokens: Maximum tokens for the response generation.

        Returns:
            str: The generated summary text.
        """
        raise NotImplementedError

    def get_max_context_size(self):
        """Return maximum context size for this provider family."""
        raise NotImplementedError

    def supports_direct_pdf(self):
        """Return whether this provider can process PDFs directly without text extraction."""
        return False

    def validate_runtime_ready(self):
        """Fail fast when a provider cannot run in the current environment."""
        return None
