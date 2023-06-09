from vocode.streaming.models.message import BaseMessage
from .base_agent import BaseAgent, RespondAgent
from ..models.agent import (
    RESTfulUserImplementedAgentConfig,
    RESTfulAgentInput,
    RESTfulAgentOutput,
    RESTfulAgentOutputType,
    RESTfulAgentText,
)
from typing import Generator, Optional, Tuple, cast
import requests
import logging
import aiohttp


class RESTfulUserImplementedAgent(RespondAgent[RESTfulUserImplementedAgentConfig]):
    def __init__(
        self,
        agent_config: RESTfulUserImplementedAgentConfig,
        logger=None,
    ):
        super().__init__(agent_config)
        if self.agent_config.generate_responses:
            raise NotImplementedError(
                "Use the WebSocket user implemented agent to stream responses"
            )
        self.logger = logger or logging.getLogger(__name__)

    async def respond(
        self,
        human_input,
        conversation_id: str,
        is_interrupt: bool = False,
    ) -> Tuple[Optional[BaseMessage], bool]:
        config = self.agent_config.respond
        body = None
        try:
            async with aiohttp.ClientSession() as session:
                payload = RESTfulAgentInput(
                    human_input=human_input, conversation_id=conversation_id
                ).dict()
                async with session.request(
                    config.method,
                    config.url,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as response:
                    body = await response.json()
                    response.raise_for_status()
                    output: RESTfulAgentOutput = RESTfulAgentOutput.parse_obj(body)
                    output_response = None
                    should_stop = False
                    if output.type == RESTfulAgentOutputType.TEXT:
                        output_response = BaseMessage(text=cast(RESTfulAgentText, output).response, metadata=output.metadata)
                    elif output.type == RESTfulAgentOutputType.END:
                        output_response = BaseMessage(text="", metadata=output.metadata)
                        should_stop = True
                    return output_response, should_stop
        except Exception as e:
            self.logger.exception(f"Error in response from RESTful agent: {body}")
            return None, True
