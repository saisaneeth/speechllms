import asyncio
import logging
import time
from typing import Any, Dict, List, Union
from typing import AsyncGenerator, Optional, Tuple

import openai

from vocode import getenv
from vocode.streaming.action.factory import ActionFactory
from vocode.streaming.agent.base_agent import RespondAgent
from vocode.streaming.agent.utils import (
    format_openai_chat_messages_from_transcript,
    collate_response_async,
    openai_get_tokens,
    vector_db_result_to_openai_chat_message,
)
from vocode.streaming.models.actions import FunctionCall
from vocode.streaming.models.agent import ChatGPTAgentConfig
from vocode.streaming.models.transcript import Transcript
from vocode.streaming.vector_db.factory import VectorDBFactory


def messages_from_transcript(transcript: Transcript, system_prompt: str):
    last_summary = transcript.last_summary
    if last_summary is not None:
        system_prompt += '\n THIS IS SUMMARY OF CONVERSATION SO FAR' + last_summary.text


class ChatGPTAgent(RespondAgent[ChatGPTAgentConfig]):
    def __init__(
            self,
            agent_config: ChatGPTAgentConfig,
            action_factory: ActionFactory = ActionFactory(),
            logger: Optional[logging.Logger] = None,
            openai_api_key: Optional[str] = None,
            vector_db_factory=VectorDBFactory(),
            goodbye_phrase: Optional[str] = "STOP CALL",
            last_messages_cnt: int = 4,
    ):
        super().__init__(
            agent_config=agent_config, action_factory=action_factory, logger=logger
        )
        if agent_config.azure_params:
            openai.api_type = agent_config.azure_params.api_type
            openai.api_base = getenv("AZURE_OPENAI_API_BASE")
            openai.api_version = agent_config.azure_params.api_version
            openai.api_key = getenv("AZURE_OPENAI_API_KEY")
        else:
            openai.api_type = "open_ai"
            openai.api_base = "https://api.openai.com/v1"
            openai.api_version = None
            openai.api_key = openai_api_key or getenv("OPENAI_API_KEY")
        if not openai.api_key:
            raise ValueError("OPENAI_API_KEY must be set in environment or passed in")
        self.first_response = (
            self.create_first_response(agent_config.expected_first_prompt)
            if agent_config.expected_first_prompt
            else None
        )
        self.is_first_response = True
        self.last_messages_cnt = last_messages_cnt
        self.goodbye_phrase = goodbye_phrase
        if goodbye_phrase is not None:
            self.agent_config.end_conversation_on_goodbye = True

        if self.agent_config.vector_db_config:
            self.vector_db = vector_db_factory.create_vector_db(
                self.agent_config.vector_db_config
            )

    async def is_goodbye(self, message: str):
        return self.goodbye_phrase.lower() in message.lower()

    def create_goodbye_detection_task(self, message: str):
        return asyncio.create_task(self.is_goodbye(message))

    def get_functions(self):
        assert self.agent_config.actions
        if not self.action_factory:
            return None
        return [
            self.action_factory.create_action(action_config).get_openai_function()
            for action_config in self.agent_config.actions
        ]

    def get_chat_parameters(self, messages: Optional[List] = None):
        assert self.transcript is not None

        messages = messages or format_openai_chat_messages_from_transcript(
            self.transcript, self.agent_config.prompt_preamble
        )
        last_summary = self.transcript.last_summary
        # TODO:refactor
        if last_summary is not None:
            # insert into system prompt as new line
            if self.agent_config.prompt_preamble is not None:
                first_message = messages[0]
                # check if it is system message
                if first_message['role'] == 'system':
                    first_message['content'] = self.agent_config.prompt_preamble + '\n' + last_summary.text
                    # cut messages to self.last_messages_cnt
                    if len(messages) - 1 > self.last_messages_cnt:
                        messages = [first_message] + messages[-self.last_messages_cnt:]
                else:
                    self.logger.error('First message is not system message, not inserting summary. Something is wrong.')

        parameters: Dict[str, Any] = {
            "messages": messages,
            "max_tokens": self.agent_config.max_tokens,
            "temperature": self.agent_config.temperature,
        }

        if self.agent_config.azure_params is not None:
            parameters["engine"] = self.agent_config.azure_params.engine
        else:
            parameters["model"] = self.agent_config.model_name

        if self.functions:
            parameters["functions"] = self.functions

        return parameters

    def create_first_response(self, first_prompt):
        messages = (
                       [{"role": "system", "content": self.agent_config.prompt_preamble}]
                       if self.agent_config.prompt_preamble
                       else []
                   ) + ([{"role": "user", "content": first_prompt}] if first_prompt is not None else [])

        parameters = self.get_chat_parameters(messages)
        return openai.ChatCompletion.create(**parameters)

    def attach_transcript(self, transcript: Transcript):
        self.transcript = transcript

    async def respond(
            self,
            human_input,
            conversation_id: str,
            is_interrupt: bool = False,
    ) -> Tuple[str, bool]:
        start = time.time()
        assert self.transcript is not None
        if is_interrupt and self.agent_config.cut_off_response:
            cut_off_response = self.get_cut_off_response()
            return cut_off_response, False
        self.logger.debug("LLM responding to human input")
        if self.is_first_response and self.first_response:
            self.logger.debug("First response is cached")
            self.is_first_response = False
            text = self.first_response
        else:
            chat_parameters = self.get_chat_parameters()
            chat_completion = await openai.ChatCompletion.acreate(**chat_parameters)
            text = chat_completion.choices[0].message.content
        self.logger.debug(f"LLM response: {text}")
        end = time.time()
        self.logger.debug("Response took %s", end - start)
        return text, False

    async def generate_response(
            self,
            human_input: str,
            conversation_id: str,
            is_interrupt: bool = False,
    ) -> AsyncGenerator[Union[str, FunctionCall], None]:
        if is_interrupt and self.agent_config.cut_off_response:
            cut_off_response = self.get_cut_off_response()
            yield cut_off_response
            return
        assert self.transcript is not None

        chat_parameters = {}
        if self.agent_config.vector_db_config:
            try:
                docs_with_scores = await self.vector_db.similarity_search_with_score(
                    self.transcript.get_last_user_message()[1]
                )
                docs_with_scores_str = "\n\n".join(
                    [
                        "Document: "
                        + doc[0].metadata["source"]
                        + f" (Confidence: {doc[1]})\n"
                        + doc[0].lc_kwargs["page_content"].replace(r"\n", "\n")
                        for doc in docs_with_scores
                    ]
                )
                vector_db_result = f"Found {len(docs_with_scores)} similar documents:\n{docs_with_scores_str}"
                messages = format_openai_chat_messages_from_transcript(
                    self.transcript, self.agent_config.prompt_preamble
                )
                messages.insert(
                    -1, vector_db_result_to_openai_chat_message(vector_db_result)
                )
                chat_parameters = self.get_chat_parameters(messages)
            except Exception as e:
                self.logger.error(f"Error while hitting vector db: {e}", exc_info=True)
                chat_parameters = self.get_chat_parameters()
        else:
            chat_parameters = self.get_chat_parameters()
        chat_parameters["stream"] = True
        stream = await openai.ChatCompletion.acreate(**chat_parameters)
        async for message in collate_response_async(
                openai_get_tokens(stream), get_functions=True
        ):
            yield message
